"""
Analytics module for Populus — developer-only dashboard.

Purpose
─────────────────────────────────────────────────────────────────────
Track user behaviour to expose the KPIs required by the growth plan
(pages 7 and 14 of Populus_Piano_di_Crescita_12_mesi.pdf):

  • Ritorno al giorno 30 (D30 retention)
  • Rapporto WAU/MAU (stickiness)
  • % utenti attivi che compiono un'azione profonda (voto o commento)
  • Voti mediani entro 24h sulle top faide

Plus the additional insights explicitly requested:
  • Categorie più frequentate
  • Statistiche sui profili

Design notes
─────────────────────────────────────────────────────────────────────
* All events land in a single collection `activity_events` with a
  minimal schema so aggregations stay flexible without schema churn.
* Dev accounts are excluded from EVERY analytics query by joining the
  `users` collection and filtering out `is_dev_account: true`.
* Endpoints are exposed under `/api/admin/analytics/*` and share the
  existing `require_admin` guard (X-Admin-Key header).
* Retention numbers are honest — we start counting from the day this
  module lands. The user opted for "from zero" (option b) so no
  historical backfill is done; the dashboard flags the accumulation
  window in its own UI.

Public API
─────────────────────────────────────────────────────────────────────
* `log_event(db, user_id, event_type, **meta)` — awaitable, best-
  effort. Never raises to the caller.
* `mount_analytics_routes(app_router, db, require_admin)` — wires all
  /admin/analytics/* endpoints onto the existing router.
* `mark_dev_accounts_from_env(db)` — startup hook. Reads the
  DEV_ACCOUNT_EMAILS env var (comma-separated) and flags matching
  users with `is_dev_account: true`.
"""
from __future__ import annotations

import os
import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Header

logger = logging.getLogger(__name__)

# ─── Event types ────────────────────────────────────────────────────
# Keep this list documented + centralized so the frontend can decode
# and the analytics endpoints can trust the vocabulary.
EVT_APP_OPEN = "app_open"
EVT_SIGNUP = "signup"
EVT_LOGIN = "login"
EVT_FEUD_VIEW = "feud_view"
EVT_VOTE_CAST = "vote_cast"
EVT_VOTE_CHANGE = "vote_change"
EVT_COMMENT_CREATED = "comment_created"
EVT_REPLY_CREATED = "reply_created"
EVT_FAVORITE_ADDED = "favorite_added"
EVT_STORY_CREATED = "story_created"
EVT_STORY_VIEW = "story_view"
EVT_SHARE_ACTION = "share_action"
EVT_NOTIFICATION_OPEN = "notification_open"

DEEP_ACTION_EVENTS = {
    EVT_VOTE_CAST,
    EVT_VOTE_CHANGE,
    EVT_COMMENT_CREATED,
    EVT_REPLY_CREATED,
}


# ─── Helpers ────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _aware(dt: datetime) -> datetime:
    """Ensure a datetime is UTC-aware. Mongo returns naive datetimes;
    our KPIs subtract them from timezone-aware `now`, so normalize here."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _ensure_indexes(db) -> None:
    """Idempotent index creation. Cheap on repeated calls."""
    try:
        await db.activity_events.create_index([("user_id", 1), ("ts", -1)])
        await db.activity_events.create_index([("event_type", 1), ("ts", -1)])
        await db.activity_events.create_index("ts")
        await db.users.create_index("is_dev_account")
        await db.users.create_index("last_active_at")
    except Exception as e:
        logger.warning(f"analytics index bootstrap warning: {e}")


# ─── Reset baseline ─────────────────────────────────────────────────
# When the developer clicks "reset", we store a timestamp in
# app_settings and every analytics aggregation ignores documents older
# than it. Non-destructive: the raw data (users, votes, comments) is
# preserved for the app itself.
_BASELINE_KEY = "analytics_baseline_at"


async def _get_baseline(db) -> Optional[datetime]:
    try:
        doc = await db.app_settings.find_one({"_id": _BASELINE_KEY})
        if not doc:
            return None
        ts = doc.get("value")
        if isinstance(ts, datetime):
            return _aware(ts)
        return None
    except Exception as e:
        logger.warning(f"_get_baseline failed: {e}")
        return None


async def _set_baseline(db, when: datetime) -> None:
    await db.app_settings.update_one(
        {"_id": _BASELINE_KEY},
        {"$set": {"value": _aware(when)}},
        upsert=True,
    )


def _since_filter(field: str, baseline: Optional[datetime], extra_since: Optional[datetime] = None) -> Dict[str, Any]:
    """Build a `$gte` filter combining the reset baseline (if any) with
    an optional extra `since` window. Returns {} if neither applies."""
    lower = None
    if baseline is not None:
        lower = baseline
    if extra_since is not None:
        lower = extra_since if lower is None else max(lower, extra_since)
    return {field: {"$gte": lower}} if lower is not None else {}


async def log_event(db, user_id: Optional[str], event_type: str, **meta) -> None:
    """Fire-and-forget event logger. Never raises to the caller."""
    if not user_id or not event_type:
        return
    try:
        now = _utcnow()
        doc: Dict[str, Any] = {
            "user_id": user_id,
            "event_type": event_type,
            "ts": now,
        }
        # Whitelist a couple of common meta keys so downstream aggregations
        # can key on them without unpacking a nested dict.
        for k in ("category", "feud_id", "side", "target_user_id"):
            if k in meta and meta[k] is not None:
                doc[k] = meta[k]
        # Everything else lands in `meta` untouched.
        remaining = {k: v for k, v in meta.items() if k not in doc}
        if remaining:
            doc["meta"] = remaining
        await db.activity_events.insert_one(doc)
        # Touch `last_active_at` so the users collection can be queried
        # directly for stickiness/DAU without hitting activity_events.
        await db.users.update_one(
            {"user_id": user_id}, {"$set": {"last_active_at": now}}
        )
    except Exception as e:
        logger.warning(f"log_event failed ({event_type}, {user_id}): {e}")


# ─── Dev-account tagging ────────────────────────────────────────────
def _parse_dev_emails() -> List[str]:
    raw = os.environ.get("DEV_ACCOUNT_EMAILS") or ""
    parts = [p.strip().lower() for p in raw.split(",")]
    return [p for p in parts if p]


async def mark_dev_accounts_from_env(db) -> int:
    """Tag every user whose email matches the DEV_ACCOUNT_EMAILS env var.

    Idempotent: safe to call at every server boot.
    Returns the number of users flagged in this call.
    """
    emails = _parse_dev_emails()
    if not emails:
        return 0
    try:
        res = await db.users.update_many(
            {"email": {"$in": emails}, "is_dev_account": {"$ne": True}},
            {"$set": {"is_dev_account": True}},
        )
        if res.modified_count:
            logger.info(
                f"analytics: tagged {res.modified_count} dev-account user(s) "
                f"from DEV_ACCOUNT_EMAILS"
            )
        return int(res.modified_count or 0)
    except Exception as e:
        logger.warning(f"mark_dev_accounts_from_env failed: {e}")
        return 0


async def maybe_flag_new_user_as_dev(db, user_doc: dict) -> None:
    """Called right after a fresh user is inserted. Checks the email
    against the DEV_ACCOUNT_EMAILS list and, if a match, flips
    `is_dev_account` on the freshly-created record. No-op otherwise.
    """
    if not user_doc:
        return
    email = (user_doc.get("email") or "").lower()
    if not email:
        return
    if email not in _parse_dev_emails():
        return
    try:
        await db.users.update_one(
            {"user_id": user_doc["user_id"]}, {"$set": {"is_dev_account": True}}
        )
        # Reflect in the local dict so the caller doesn't return stale data.
        user_doc["is_dev_account"] = True
    except Exception as e:
        logger.warning(f"maybe_flag_new_user_as_dev failed: {e}")


# ─── Query helpers ──────────────────────────────────────────────────
async def _non_dev_user_ids(db) -> List[str]:
    """Returns the user_ids that count toward metrics (excludes dev)."""
    cursor = db.users.find({"is_dev_account": {"$ne": True}}, {"_id": 0, "user_id": 1})
    return [u["user_id"] async for u in cursor]


async def _count_distinct_active(db, since: datetime, baseline: Optional[datetime] = None) -> int:
    """Distinct non-dev users with at least one event since `since`."""
    lower = since if baseline is None else max(since, baseline)
    pipeline = [
        {"$match": {"ts": {"$gte": lower}}},
        {"$group": {"_id": "$user_id"}},
        {"$lookup": {
            "from": "users", "localField": "_id", "foreignField": "user_id", "as": "u",
        }},
        {"$match": {"u.is_dev_account": {"$ne": True}}},
        {"$count": "n"},
    ]
    docs = await db.activity_events.aggregate(pipeline).to_list(1)
    return int(docs[0]["n"]) if docs else 0


# ─── Analytics router factory ───────────────────────────────────────
def build_analytics_router(db, require_admin) -> APIRouter:
    """Return a fully-wired APIRouter that mounts /admin/analytics/*.

    `require_admin` is the existing Depends(...) callable from server.py
    — we reuse it as-is so the X-Admin-Key gate stays consistent.
    """
    router = APIRouter()

    @router.get("/admin/analytics/overview")
    async def overview(_: bool = Depends(require_admin)):
        now = _utcnow()
        baseline = await _get_baseline(db)
        d1 = now - timedelta(days=1)
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        non_dev = {"is_dev_account": {"$ne": True}}
        # User counts respect the reset baseline: only users created
        # after it count toward "total". Historical accounts stay in
        # the DB but are invisible to analytics.
        user_since = _since_filter("created_at", baseline)
        total_users = await db.users.count_documents({**non_dev, **user_since})
        anon_users = await db.users.count_documents(
            {**non_dev, **user_since, "auth_provider": "anonymous"}
        )
        registered_users = total_users - anon_users

        # Votes / comments totals — filter by author's dev flag via lookup.
        async def _count_from(coll, since: Optional[datetime]):
            match = _since_filter("created_at", baseline, since)
            pipe: List[dict] = [{"$match": match}] if match else []
            pipe.extend([
                {"$lookup": {
                    "from": "users", "localField": "user_id",
                    "foreignField": "user_id", "as": "u",
                }},
                {"$match": {"u.is_dev_account": {"$ne": True}}},
                {"$count": "n"},
            ])
            r = await coll.aggregate(pipe).to_list(1)
            return int(r[0]["n"]) if r else 0

        total_votes = await _count_from(db.votes, None)
        votes_24h = await _count_from(db.votes, d1)
        votes_7d = await _count_from(db.votes, d7)
        votes_30d = await _count_from(db.votes, d30)

        total_comments = await _count_from(db.comments, None)
        comments_24h = await _count_from(db.comments, d1)
        comments_7d = await _count_from(db.comments, d7)

        # New signups
        signups_24h = await db.users.count_documents(
            {**non_dev, **_since_filter("created_at", baseline, d1)}
        )
        signups_7d = await db.users.count_documents(
            {**non_dev, **_since_filter("created_at", baseline, d7)}
        )
        signups_30d = await db.users.count_documents(
            {**non_dev, **_since_filter("created_at", baseline, d30)}
        )

        # DAU/WAU/MAU
        dau = await _count_distinct_active(db, d1, baseline)
        wau = await _count_distinct_active(db, d7, baseline)
        mau = await _count_distinct_active(db, d30, baseline)
        wau_mau = round(100 * wau / mau, 1) if mau else 0.0

        return {
            "generated_at": _iso(now),
            "baseline_at": _iso(baseline) if baseline else None,
            "users": {
                "total": total_users,
                "anonymous": anon_users,
                "registered": registered_users,
                "signups_24h": signups_24h,
                "signups_7d": signups_7d,
                "signups_30d": signups_30d,
            },
            "engagement": {
                "total_votes": total_votes,
                "votes_24h": votes_24h,
                "votes_7d": votes_7d,
                "votes_30d": votes_30d,
                "total_comments": total_comments,
                "comments_24h": comments_24h,
                "comments_7d": comments_7d,
            },
            "active_users": {
                "dau": dau,
                "wau": wau,
                "mau": mau,
                "wau_mau_ratio_pct": wau_mau,
            },
        }

    @router.get("/admin/analytics/active-users")
    async def active_users_series(days: int = 30, _: bool = Depends(require_admin)):
        """Daily distinct-active-user counts for the last N days.
        Frontend renders as a line/bar chart."""
        days = max(7, min(90, int(days or 30)))
        now = _utcnow()
        baseline = await _get_baseline(db)
        floor = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        if baseline is not None and baseline > floor:
            floor = baseline
        pipeline = [
            {"$match": {"ts": {"$gte": floor}}},
            {"$lookup": {
                "from": "users", "localField": "user_id",
                "foreignField": "user_id", "as": "u",
            }},
            {"$match": {"u.is_dev_account": {"$ne": True}}},
            {"$group": {
                "_id": {
                    "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}},
                    "user_id": "$user_id",
                },
            }},
            {"$group": {"_id": "$_id.day", "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]
        rows = await db.activity_events.aggregate(pipeline).to_list(days + 5)
        series = [{"date": r["_id"], "dau": r["count"]} for r in rows]
        return {"series": series, "days": days}

    @router.get("/admin/analytics/retention")
    async def retention(_: bool = Depends(require_admin)):
        """Weekly cohorts + D1/D7/D30 return rates.

        Only counts users whose `created_at` is > now-90d (otherwise the
        cohort size gets huge and the numbers stop being actionable for
        early growth). Reads solely from the `users` and
        `activity_events` collections — dev users excluded.
        """
        now = _utcnow()
        baseline = await _get_baseline(db)
        floor = now - timedelta(days=90)
        if baseline is not None and baseline > floor:
            floor = baseline
        # Pull cohorts (users grouped by ISO week of creation).
        cohort_pipe = [
            {"$match": {
                "is_dev_account": {"$ne": True},
                "created_at": {"$gte": floor},
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-W%V", "date": "$created_at"}},
                "user_ids": {"$push": "$user_id"},
                "count": {"$sum": 1},
                "earliest": {"$min": "$created_at"},
            }},
            {"$sort": {"earliest": 1}},
        ]
        cohorts = await db.users.aggregate(cohort_pipe).to_list(20)

        results = []
        for c in cohorts:
            user_ids = c.get("user_ids") or []
            if not user_ids:
                continue
            # For each user compute the age of the newest event they have
            # in days-since-signup and bucket.
            d1_hits = d7_hits = d30_hits = 0
            for uid in user_ids:
                # Their signup date is the min ts in activity_events for
                # them, but we already have it: use created_at from users.
                # Simpler: query one user's `created_at` up-front.
                # To keep this fast we skip and use last_active_at from users.
                # But we DO need first_activity_after_1d/7d/30d — so query
                # activity_events for that specific user with ranges.
                pass
            # Efficient version: single aggregation per cohort.
            uids = user_ids
            cohort_users = await db.users.find(
                {"user_id": {"$in": uids}}, {"_id": 0, "user_id": 1, "created_at": 1}
            ).to_list(len(uids))
            uid_to_signup = {u["user_id"]: u["created_at"] for u in cohort_users}
            # Fetch events for these users in bulk.
            evt_cursor = db.activity_events.find(
                {"user_id": {"$in": uids}}, {"_id": 0, "user_id": 1, "ts": 1}
            )
            per_user_days: Dict[str, List[float]] = {}
            async for ev in evt_cursor:
                signup = uid_to_signup.get(ev["user_id"])
                if not signup:
                    continue
                delta_days = (_aware(ev["ts"]) - _aware(signup)).total_seconds() / 86400.0
                per_user_days.setdefault(ev["user_id"], []).append(delta_days)
            for uid in uids:
                days_active = per_user_days.get(uid, [])
                if any(0.5 <= d < 2.5 for d in days_active):
                    d1_hits += 1
                if any(5.5 <= d < 8.5 for d in days_active):
                    d7_hits += 1
                if any(27 <= d < 33 for d in days_active):
                    d30_hits += 1
            cohort_size = len(uids)
            # Only surface D-N rates when the cohort is old enough.
            cohort_age_days = (now - _aware(c["earliest"])).total_seconds() / 86400.0
            def _pct(n): return round(100 * n / cohort_size, 1) if cohort_size else 0.0
            results.append({
                "cohort": c["_id"],
                "cohort_start": _iso(_aware(c["earliest"])),
                "size": cohort_size,
                "d1_pct": _pct(d1_hits) if cohort_age_days >= 2 else None,
                "d7_pct": _pct(d7_hits) if cohort_age_days >= 8 else None,
                "d30_pct": _pct(d30_hits) if cohort_age_days >= 31 else None,
            })
        # Aggregate D30 across ready cohorts for the KPI card.
        ready_d30 = [c for c in results if c["d30_pct"] is not None]
        overall_d30 = None
        if ready_d30:
            total_size = sum(c["size"] for c in ready_d30)
            total_hits = sum((c["d30_pct"] / 100.0) * c["size"] for c in ready_d30)
            overall_d30 = round(100 * total_hits / total_size, 1) if total_size else 0.0
        return {"cohorts": results, "overall_d30_pct": overall_d30}

    @router.get("/admin/analytics/deep-action-rate")
    async def deep_action_rate(days: int = 7, _: bool = Depends(require_admin)):
        """% of active users (last N days) who performed at least one
        deep action (vote/comment/reply) in that window.
        """
        days = max(1, min(30, int(days or 7)))
        baseline = await _get_baseline(db)
        since = _utcnow() - timedelta(days=days)
        if baseline is not None and baseline > since:
            since = baseline
        # Distinct active users
        active_ids_pipeline = [
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {"_id": "$user_id"}},
            {"$lookup": {"from": "users", "localField": "_id",
                         "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_dev_account": {"$ne": True}}},
            {"$project": {"_id": 1}},
        ]
        active_docs = await db.activity_events.aggregate(active_ids_pipeline).to_list(100000)
        active_ids = [d["_id"] for d in active_docs]
        if not active_ids:
            return {"active": 0, "deep_action_users": 0, "pct": 0.0, "days": days}
        deep_docs = await db.activity_events.aggregate([
            {"$match": {
                "ts": {"$gte": since},
                "user_id": {"$in": active_ids},
                "event_type": {"$in": list(DEEP_ACTION_EVENTS)},
            }},
            {"$group": {"_id": "$user_id"}},
            {"$count": "n"},
        ]).to_list(1)
        deep_n = int(deep_docs[0]["n"]) if deep_docs else 0
        pct = round(100 * deep_n / len(active_ids), 1)
        return {
            "active": len(active_ids),
            "deep_action_users": deep_n,
            "pct": pct,
            "days": days,
        }

    @router.get("/admin/analytics/top-feuds-24h")
    async def top_feuds_24h(_: bool = Depends(require_admin)):
        """Median votes received within 24h of creation, computed over
        the last 30 days of feuds. Only counts votes from non-dev users."""
        now = _utcnow()
        baseline = await _get_baseline(db)
        window_start = now - timedelta(days=30)
        if baseline is not None and baseline > window_start:
            window_start = baseline
        feuds = await db.feuds.find(
            {"created_at": {"$gte": window_start}},
            {"_id": 0, "feud_id": 1, "created_at": 1, "title": 1, "category_label": 1},
        ).sort("created_at", -1).to_list(500)
        rows: List[Dict[str, Any]] = []
        for f in feuds:
            fid = f["feud_id"]
            created = _aware(f["created_at"])
            deadline = created + timedelta(hours=24)
            n = await db.votes.aggregate([
                {"$match": {
                    "feud_id": fid,
                    "created_at": {"$lte": deadline},
                }},
                {"$lookup": {"from": "users", "localField": "user_id",
                             "foreignField": "user_id", "as": "u"}},
                {"$match": {"u.is_dev_account": {"$ne": True}}},
                {"$count": "n"},
            ]).to_list(1)
            votes_first_24h = int(n[0]["n"]) if n else 0
            rows.append({
                "feud_id": fid,
                "title": f.get("title") or "",
                "category_label": f.get("category_label") or "",
                "votes_first_24h": votes_first_24h,
                "created_at": _iso(created),
                "_age_seconds": (now - created).total_seconds(),
            })
        # Sort desc, take top 10 for the KPI, but compute median over ALL
        # feuds in the window (excluding those younger than 24h since
        # they can still grow).
        eligible = [r["votes_first_24h"] for r in rows if r["_age_seconds"] >= 86400]
        median_votes = int(statistics.median(eligible)) if eligible else 0
        top = sorted(rows, key=lambda r: -r["votes_first_24h"])[:10]
        # strip internal helper key before returning
        for r in top:
            r.pop("_age_seconds", None)
        for r in rows:
            r.pop("_age_seconds", None)
        return {
            "median_votes_first_24h": median_votes,
            "sample_size": len(eligible),
            "top": top,
        }

    @router.get("/admin/analytics/categories")
    async def categories(_: bool = Depends(require_admin)):
        """Category-level engagement — votes, views, comments, distinct
        active users. Non-dev only, all-time (post-baseline)."""
        baseline = await _get_baseline(db)
        base_match_votes = [{"$match": _since_filter("created_at", baseline)}] if baseline else []
        base_match_events = [{"$match": _since_filter("ts", baseline)}] if baseline else []
        # Votes per category — needs a join to `feuds` for the category.
        pipe_votes = base_match_votes + [
            {"$lookup": {"from": "users", "localField": "user_id",
                         "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_dev_account": {"$ne": True}}},
            {"$lookup": {"from": "feuds", "localField": "feud_id",
                         "foreignField": "feud_id", "as": "f"}},
            {"$unwind": {"path": "$f", "preserveNullAndEmptyArrays": True}},
            {"$group": {"_id": "$f.category", "votes": {"$sum": 1}}},
        ]
        pipe_comments = base_match_votes + [
            {"$lookup": {"from": "users", "localField": "user_id",
                         "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_dev_account": {"$ne": True}}},
            {"$lookup": {"from": "feuds", "localField": "feud_id",
                         "foreignField": "feud_id", "as": "f"}},
            {"$unwind": {"path": "$f", "preserveNullAndEmptyArrays": True}},
            {"$group": {"_id": "$f.category", "comments": {"$sum": 1}}},
        ]
        pipe_views = base_match_events + [
            {"$lookup": {"from": "users", "localField": "user_id",
                         "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_dev_account": {"$ne": True}}},
            {"$group": {"_id": "$category", "views": {"$sum": {"$ifNull": ["$count", 1]}}}},
        ]

        v = await db.votes.aggregate(pipe_votes).to_list(50)
        c = await db.comments.aggregate(pipe_comments).to_list(50)
        w = await db.feud_views.aggregate(pipe_views).to_list(50)
        # Distinct active users per category via activity_events
        pipe_active = base_match_events + [
            {"$lookup": {"from": "users", "localField": "user_id",
                         "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_dev_account": {"$ne": True}, "category": {"$ne": None}}},
            {"$group": {"_id": {"cat": "$category", "uid": "$user_id"}}},
            {"$group": {"_id": "$_id.cat", "active_users": {"$sum": 1}}},
        ]
        au = await db.activity_events.aggregate(pipe_active).to_list(50)

        merged: Dict[str, Dict[str, Any]] = {}
        for r in v:
            k = r["_id"] or "unknown"
            merged.setdefault(k, {"category": k})["votes"] = r["votes"]
        for r in c:
            k = r["_id"] or "unknown"
            merged.setdefault(k, {"category": k})["comments"] = r["comments"]
        for r in w:
            k = r["_id"] or "unknown"
            merged.setdefault(k, {"category": k})["views"] = r["views"]
        for r in au:
            k = r["_id"] or "unknown"
            merged.setdefault(k, {"category": k})["active_users"] = r["active_users"]

        out = []
        for k, entry in merged.items():
            out.append({
                "category": k,
                "votes": int(entry.get("votes") or 0),
                "comments": int(entry.get("comments") or 0),
                "views": int(entry.get("views") or 0),
                "active_users": int(entry.get("active_users") or 0),
            })
        out.sort(key=lambda x: -x["votes"])
        return {"categories": out}

    @router.get("/admin/analytics/profiles")
    async def profiles(_: bool = Depends(require_admin)):
        """Aggregate stats about user profiles (non-dev, post-baseline)."""
        baseline = await _get_baseline(db)
        non_dev = {"is_dev_account": {"$ne": True}, **_since_filter("created_at", baseline)}
        total = await db.users.count_documents(non_dev)
        if total == 0:
            return {
                "total": 0, "with_photo_pct": 0, "with_bio_pct": 0,
                "with_circle_pct": 0, "with_display_name_pct": 0,
                "onboarded_pct": 0, "avg_circle_size": 0,
                "auth_providers": {}, "regions": [], "ages": {}, "sex": {},
                "push_enabled_pct": 0,
            }

        with_photo = await db.users.count_documents({
            **non_dev, "primary_photo_id": {"$ne": None},
        })
        with_bio = await db.users.count_documents({
            **non_dev, "bio": {"$exists": True, "$ne": ""},
        })
        with_display = await db.users.count_documents({
            **non_dev, "display_name": {"$exists": True, "$ne": ""},
        })
        onboarded = await db.users.count_documents({
            **non_dev, "onboarding_completed": True,
        })
        push_enabled = await db.users.count_documents({
            **non_dev, "push_notifications": {"$ne": False},
        })

        # Distinct auth providers
        prov_pipe = [
            {"$match": non_dev},
            {"$group": {"_id": {"$ifNull": ["$auth_provider", "unknown"]}, "n": {"$sum": 1}}},
        ]
        auth_providers = {}
        async for row in db.users.aggregate(prov_pipe):
            auth_providers[row["_id"]] = row["n"]

        # Circle counts — join via friendships collection.
        circle_pipe = [
            {"$lookup": {"from": "friendships", "localField": "user_id",
                         "foreignField": "user_id", "as": "friends"}},
            {"$match": non_dev},
            {"$project": {"_id": 0, "circle_size": {"$size": "$friends"}}},
        ]
        circle_sizes: List[int] = []
        async for row in db.users.aggregate(circle_pipe):
            circle_sizes.append(int(row["circle_size"] or 0))
        with_circle = sum(1 for s in circle_sizes if s > 0)
        avg_circle = round(sum(circle_sizes) / len(circle_sizes), 1) if circle_sizes else 0

        # Region breakdown
        region_pipe = [
            {"$match": non_dev},
            {"$group": {"_id": {"$ifNull": ["$region", "unknown"]}, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]
        regions = []
        async for r in db.users.aggregate(region_pipe):
            regions.append({"region": r["_id"], "count": r["n"]})

        # Age buckets
        AGE_BUCKETS = [
            ("13-17", 13, 18),
            ("18-24", 18, 25),
            ("25-34", 25, 35),
            ("35-44", 35, 45),
            ("45-54", 45, 55),
            ("55-64", 55, 65),
            ("65+", 65, 121),
        ]
        age_counts = {name: 0 for name, _, _ in AGE_BUCKETS}
        age_counts["unknown"] = 0
        async for u in db.users.find(non_dev, {"_id": 0, "age": 1}):
            a = u.get("age")
            if not isinstance(a, int):
                age_counts["unknown"] += 1
                continue
            placed = False
            for name, lo, hi in AGE_BUCKETS:
                if lo <= a < hi:
                    age_counts[name] += 1
                    placed = True
                    break
            if not placed:
                age_counts["unknown"] += 1

        # Sex breakdown
        sex_pipe = [
            {"$match": non_dev},
            {"$group": {"_id": {"$ifNull": ["$sex", "unknown"]}, "n": {"$sum": 1}}},
        ]
        sex_counts = {}
        async for r in db.users.aggregate(sex_pipe):
            sex_counts[r["_id"]] = r["n"]

        def pct(n): return round(100 * n / total, 1)

        return {
            "total": total,
            "with_photo_pct": pct(with_photo),
            "with_bio_pct": pct(with_bio),
            "with_display_name_pct": pct(with_display),
            "with_circle_pct": pct(with_circle),
            "onboarded_pct": pct(onboarded),
            "push_enabled_pct": pct(push_enabled),
            "avg_circle_size": avg_circle,
            "auth_providers": auth_providers,
            "regions": regions[:15],
            "ages": age_counts,
            "sex": sex_counts,
        }

    @router.get("/admin/analytics/funnel")
    async def funnel(_: bool = Depends(require_admin)):
        """Conversion funnel: signup → first vote → first comment.
        Restricted to users created in the last 30 days for actionable numbers."""
        baseline = await _get_baseline(db)
        floor = _utcnow() - timedelta(days=30)
        if baseline is not None and baseline > floor:
            floor = baseline
        non_dev = {"is_dev_account": {"$ne": True}, "created_at": {"$gte": floor}}
        total = await db.users.count_documents(non_dev)
        if total == 0:
            return {"signups": 0, "with_vote_pct": 0, "with_comment_pct": 0, "days": 30}
        signup_uids = [
            u["user_id"]
            async for u in db.users.find(non_dev, {"_id": 0, "user_id": 1})
        ]
        with_vote = await db.votes.aggregate([
            {"$match": {"user_id": {"$in": signup_uids}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "n"},
        ]).to_list(1)
        with_comment = await db.comments.aggregate([
            {"$match": {"user_id": {"$in": signup_uids}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "n"},
        ]).to_list(1)
        v = int(with_vote[0]["n"]) if with_vote else 0
        c = int(with_comment[0]["n"]) if with_comment else 0
        return {
            "signups": total,
            "with_vote": v,
            "with_vote_pct": round(100 * v / total, 1),
            "with_comment": c,
            "with_comment_pct": round(100 * c / total, 1),
            "days": 30,
        }

    @router.get("/admin/analytics/dev-accounts")
    async def list_dev_accounts(_: bool = Depends(require_admin)):
        """List every user currently flagged as a dev account.
        Handy for the developer dashboard so you can verify the exclusion
        list is correct."""
        docs = await db.users.find(
            {"is_dev_account": True},
            {"_id": 0, "user_id": 1, "email": 1, "nickname": 1, "auth_provider": 1},
        ).to_list(200)
        return {"dev_accounts": docs}

    @router.post("/admin/analytics/dev-accounts/toggle")
    async def toggle_dev_account(payload: dict, _: bool = Depends(require_admin)):
        """Manually flip a user's dev-account flag.
        Body: `{"user_id": "...", "is_dev": true|false}`."""
        uid = str(payload.get("user_id") or "").strip()
        is_dev = bool(payload.get("is_dev"))
        if not uid:
            raise HTTPException(status_code=400, detail="user_id mancante")
        res = await db.users.update_one(
            {"user_id": uid}, {"$set": {"is_dev_account": is_dev}}
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        return {"ok": True, "user_id": uid, "is_dev_account": is_dev}

    @router.get("/admin/analytics/reset")
    async def get_reset_state(_: bool = Depends(require_admin)):
        """Current baseline timestamp. Returns null if never reset."""
        b = await _get_baseline(db)
        return {"baseline_at": _iso(b) if b else None}

    @router.post("/admin/analytics/reset")
    async def reset_analytics(_: bool = Depends(require_admin)):
        """Reset ALL analytics KPIs to zero, non-destructively.

        Strategy: bump the reset baseline to `now` so every aggregation
        ignores pre-existing votes/comments/users/events. Also wipes the
        activity_events and feud_views collections so DAU/WAU/MAU start
        fresh even without a baseline filter.

        The raw votes/comments/users records stay intact — the app itself
        keeps working exactly as before, but the dashboard reports zero.
        """
        now = _utcnow()
        await _set_baseline(db, now)
        # Nuke event streams for a clean slate on DAU/WAU/MAU too.
        try:
            await db.activity_events.delete_many({})
            await db.feud_views.delete_many({})
            await db.users.update_many({}, {"$unset": {"last_active_at": ""}})
        except Exception as e:
            logger.warning(f"reset_analytics event wipe warning: {e}")
        return {"ok": True, "baseline_at": _iso(now)}

    return router


# ─── Public app-open event ─────────────────────────────────────────
def build_public_analytics_router(db, get_current_user_optional) -> APIRouter:
    """Endpoints callable by the app (not admin-only) — used to record
    the 'app opened' event once per launch."""
    router = APIRouter()

    @router.post("/analytics/app-open")
    async def app_open(user: Optional[dict] = Depends(get_current_user_optional)):
        if user and user.get("user_id"):
            await log_event(db, user["user_id"], EVT_APP_OPEN)
        return {"ok": True}

    return router
