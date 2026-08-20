"""
Populus — Bot behaviour engine.

Responsibilities
─────────────────────────────────────────────────────────────────────
1. Seed 100 bot user documents in `users` collection on startup.
2. Keep a small `bot_config` document with:
     • enabled: bool  — master ON/OFF switch
     • active_count: int (0..100) — how many bots are "online"
3. Provide `apply_active_count(n)` — flips the first `n` personas
   (which are already balanced by construction) to `bot_active=True`,
   the rest to `False`.
4. Provide `bot_tick()` — the periodic action executed every 30 min.
5. Provide `run_initial_burst()` — invoked when the admin toggles
   the switch to ON so the founder sees activity immediately.
6. Provide direct DB helpers (`_bot_cast_vote`, `_bot_add_comment`,
   `_bot_create_story`) that mimic the public endpoints but SKIP:
     • analytics logging (bots are excluded by `is_dev_account: True`)
     • moderation (LLM output is filtered by its own safety rules)
     • notification fanout to spare load at bot scale

Analytics isolation
─────────────────────────────────────────────────────────────────────
Every bot user is created with BOTH `is_bot: True` AND
`is_dev_account: True`. Every existing analytics query already filters
`is_dev_account: {"$ne": True}`, so bots are invisible to the dashboard
by construction. The demographics endpoint gets an extra `is_bot:
{"$ne": True}` filter added in server.py just to be extra safe.
"""
from __future__ import annotations

import os
import asyncio
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from bot_personas import (
    build_personas,
    system_prompt_for,
    story_prompt_for,
    random_style_hint,
    BANNED_OPENERS,
)

logger = logging.getLogger("bot_engine")

# Global handles wired by `init(db)` — we avoid importing server.py to
# prevent a circular import.
_db = None
_scheduler = None
_llm_lock = asyncio.Lock()

# Per-(bot_id, feud_id) locks that serialize the "check-if-already-contributed
# → insert" critical section. Without this, a concurrent bot burst can race
# two tasks past the existence check before either has committed its write,
# producing duplicate contributions on the same feud. In-process only —
# sufficient because the backend runs a single worker.
_contribution_locks: Dict[Any, asyncio.Lock] = {}
_contribution_locks_guard = asyncio.Lock()


async def _get_contribution_lock(bot_id: str, feud_id: str) -> asyncio.Lock:
    """Return (creating if needed) the lock guarding contributions of
    `bot_id` on `feud_id`. Creation itself is guarded by a module-level
    lock so two concurrent callers can't build separate Lock objects."""
    key = (bot_id, feud_id)
    lock = _contribution_locks.get(key)
    if lock is not None:
        return lock
    async with _contribution_locks_guard:
        lock = _contribution_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _contribution_locks[key] = lock
    return lock

# Story TTL constant duplicated from server.py to avoid the import
# cycle. Keep in sync if that value ever changes there.
_STORY_TTL_HOURS = 24


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════
async def init(db) -> None:
    """Bootstrap the engine: seed bots + create indexes.

    Idempotent: safe to call on every process boot.
    """
    global _db
    _db = db
    try:
        await db.users.create_index("is_bot")
        await db.users.create_index("bot_active")
    except Exception as e:
        logger.warning(f"bot_engine index creation: {e}")
    await _ensure_bots_seeded()
    await _ensure_config()
    logger.info("bot_engine ready")


async def start_scheduler() -> None:
    """Kick off the APScheduler background loop (every 30 min)."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except Exception as e:
        logger.warning(f"apscheduler not available, tick disabled: {e}")
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(bot_tick, "interval", minutes=30, id="bot_tick", replace_existing=True)
    _scheduler.start()
    logger.info("bot_engine scheduler started (30-min tick)")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


async def get_state() -> Dict[str, Any]:
    """Snapshot for the admin panel."""
    cfg = await _get_config()
    active = await _db.users.count_documents({"is_bot": True, "bot_active": True})
    total = await _db.users.count_documents({"is_bot": True})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "active_count": int(cfg.get("active_count", 0)),
        "reported_active": int(active),
        "total_bots": int(total),
        "last_tick_at": cfg.get("last_tick_at"),
        "last_burst_at": cfg.get("last_burst_at"),
    }


async def set_enabled(enabled: bool) -> Dict[str, Any]:
    """Master ON/OFF. When flipping ON, we also fire a burst so the
    admin sees activity right away (see run_initial_burst)."""
    await _db.bot_config.update_one(
        {"_id": "config"},
        {"$set": {"enabled": bool(enabled), "updated_at": _now()}},
        upsert=True,
    )
    if enabled:
        # If active_count is still 0, default to 30 bots — a
        # reasonable starting point that already looks lively.
        cfg = await _get_config()
        if int(cfg.get("active_count", 0)) == 0:
            await set_active_count(30, run_burst=False)
        # Fire burst asynchronously so the API call returns fast.
        asyncio.create_task(run_initial_burst())
    else:
        # Deactivate all bots so they stop appearing "online".
        await _db.users.update_many({"is_bot": True}, {"$set": {"bot_active": False}})
    return await get_state()


async def set_active_count(n: int, run_burst: bool = True) -> Dict[str, Any]:
    """Set N bots active (0..100). Balanced-by-construction: the persona
    list is already interleaved across political_lean × topic × activity,
    so activating the first N indices gives a diverse sample.
    """
    n = max(0, min(100, int(n)))
    # Enable first N by bot_index; disable the rest.
    await _db.users.update_many(
        {"is_bot": True, "bot_index": {"$lt": n}},
        {"$set": {"bot_active": True}},
    )
    await _db.users.update_many(
        {"is_bot": True, "bot_index": {"$gte": n}},
        {"$set": {"bot_active": False}},
    )
    await _db.bot_config.update_one(
        {"_id": "config"},
        {"$set": {"active_count": n, "updated_at": _now()}},
        upsert=True,
    )
    if run_burst:
        cfg = await _get_config()
        if cfg.get("enabled") and n > 0:
            asyncio.create_task(run_initial_burst())
    return await get_state()


async def run_initial_burst() -> None:
    """One-off flush of activity so the admin can verify the bots are
    live right after enabling them. Behaves like a single `bot_tick`
    with slightly higher probabilities.
    """
    try:
        await _db.bot_config.update_one(
            {"_id": "config"},
            {"$set": {"last_burst_at": _now()}},
            upsert=True,
        )
        await _tick_internal(burst=True)
    except Exception as e:
        logger.warning(f"bot burst failed: {e}")


async def bot_tick() -> None:
    """Periodic entry point. Executes if `enabled=True`."""
    try:
        cfg = await _get_config()
        if not cfg.get("enabled"):
            return
        await _tick_internal(burst=False)
        await _db.bot_config.update_one(
            {"_id": "config"},
            {"$set": {"last_tick_at": _now()}},
            upsert=True,
        )
    except Exception as e:
        logger.exception(f"bot_tick error: {e}")


async def reset_content(kinds: List[str]) -> Dict[str, Any]:
    """Delete existing bot-authored content from the platform.

    `kinds` is any subset of {'comments', 'stories', 'votes'}. Anything
    else is ignored. The bot USER documents themselves are never
    touched — this only wipes the artefacts they produced.

    Rationale: after a persona re-seed, old comments still carry the
    STALE nickname/user_id snapshot. Tapping such a comment opens a
    profile with a different name → confusing. The admin can now hit
    "reset" and start fresh with new personas.
    """
    result: Dict[str, Any] = {"comments_deleted": 0, "stories_deleted": 0, "votes_deleted": 0}
    if _db is None:
        return result
    bot_ids = [
        u["user_id"] async for u in _db.users.find(
            {"is_bot": True}, {"_id": 0, "user_id": 1}
        )
    ]
    if not bot_ids:
        return result
    if "comments" in kinds:
        r = await _db.comments.delete_many({"user_id": {"$in": bot_ids}})
        result["comments_deleted"] = int(r.deleted_count)
        # Also drop replies whose parent comment was authored by a bot —
        # but those disappear naturally when the parent is gone, so we
        # skip to avoid an expensive lookup. Replies AUTHORED by a bot
        # are deleted separately:
        try:
            r2 = await _db.replies.delete_many({"user_id": {"$in": bot_ids}})
            result["replies_deleted"] = int(r2.deleted_count)
        except Exception:
            pass
    if "stories" in kinds:
        r = await _db.stories.delete_many({"user_id": {"$in": bot_ids}})
        result["stories_deleted"] = int(r.deleted_count)
    if "votes" in kinds:
        # Restore feud counters BEFORE deleting so vote totals don't get
        # out of sync. Group bot votes by (feud_id, side), then $inc the
        # opposite counter with a negative delta.
        try:
            agg = _db.votes.aggregate([
                {"$match": {"user_id": {"$in": bot_ids}}},
                {"$group": {
                    "_id": {"feud_id": "$feud_id", "side": "$side"},
                    "n": {"$sum": 1},
                }},
            ])
            async for row in agg:
                fid = row["_id"]["feud_id"]
                side = row["_id"]["side"]
                n = int(row["n"])
                if not fid or n <= 0:
                    continue
                field = "votes_a" if side == "A" else "votes_b"
                await _db.feuds.update_one({"feud_id": fid}, {"$inc": {field: -n}})
        except Exception as e:
            logger.warning(f"bot vote counter rollback failed: {e}")
        r = await _db.votes.delete_many({"user_id": {"$in": bot_ids}})
        result["votes_deleted"] = int(r.deleted_count)
        # Zero out per-bot vote tallies on the user documents so counters
        # match the new (empty) history.
        await _db.users.update_many(
            {"is_bot": True},
            {"$set": {"total_votes": 0, "majority_votes": 0, "minority_votes": 0}},
        )
    return result


# ═══════════════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════════════
def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_config() -> Dict[str, Any]:
    doc = await _db.bot_config.find_one({"_id": "config"}) or {}
    return doc


async def _ensure_config() -> None:
    existing = await _db.bot_config.find_one({"_id": "config"})
    if existing is None:
        await _db.bot_config.insert_one({
            "_id": "config",
            "enabled": False,
            "active_count": 0,
            "created_at": _now(),
        })


async def _ensure_bots_seeded() -> None:
    """Upsert 100 bot user documents. Preserves already-created records
    (so their history/votes survive re-boots) while adding fields to
    older bots when the schema changes.
    """
    personas = build_personas()
    now = _now()
    for p in personas:
        base = {
            "user_id": p["user_id"],
            "email": p["email"],
            "nickname": p["nickname"],
            "auth_provider": "bot",
            "email_verified": True,
            "onboarding_completed": True,
            "is_bot": True,
            "is_dev_account": True,  # ← analytics exclusion
            "is_anonymous": False,
            "age": p["age"],
            "sex": p["sex"],
            "region": p["region"],
            "city": p["city"],
            "profession": p["profession"],
            "bio": p["bio"],
            "display_name": p["display_name"],
            "favorite_categories": p["favorite_categories"],
            "bot_index": p["bot_index"],
            "bot_persona": {
                "main_topic": p["main_topic"],
                "secondary_topic": p["secondary_topic"],
                "political_lean": p["political_lean"],
                "party_bias": p["party_bias"],
                "tone": p["tone"],
                "verbosity": p["verbosity"],
                "activity_level": p["activity_level"],
                "activity_probability": p["activity_probability"],
                "comment_probability": p["comment_probability"],
                "story_probability": p["story_probability"],
            },
            "majority_votes": 0,
            "minority_votes": 0,
            "total_votes": 0,
        }
        # `bot_active` and `created_at` are set only on insert so
        # admin toggles are preserved across reboots.
        await _db.users.update_one(
            {"user_id": p["user_id"]},
            {
                "$set": base,
                "$setOnInsert": {"created_at": now, "bot_active": False},
            },
            upsert=True,
        )
    logger.info(f"bot_engine: 100 bot users upserted")


# ─── Tick loop ─────────────────────────────────────────────────────
async def _tick_internal(burst: bool = False) -> None:
    """For each active bot, decide (with per-bot probability) whether
    to vote / comment / post a story. Runs bots concurrently with a
    small semaphore to protect the DB and the LLM budget.
    """
    active_bots = await _db.users.find(
        {"is_bot": True, "bot_active": True},
        {"_id": 0},
    ).to_list(200)
    if not active_bots:
        return

    # Fetch up to 40 recent, visible feuds — one query, shared for all bots.
    recent_feuds = await _fetch_candidate_feuds(limit=40)
    if not recent_feuds:
        return

    sem = asyncio.Semaphore(6)  # limit concurrent LLM calls

    async def _run_one(bot):
        async with sem:
            try:
                await _act_for_bot(bot, recent_feuds, burst=burst)
            except Exception as e:
                logger.warning(f"bot {bot.get('user_id')} action failed: {e}")

    await asyncio.gather(*[_run_one(b) for b in active_bots])


async def _fetch_candidate_feuds(limit: int = 40) -> List[Dict[str, Any]]:
    """Recent visible feuds ordered by newest first."""
    cursor = _db.feuds.find(
        {"is_hidden": {"$ne": True}},
        {"_id": 0},
    ).sort("created_at", -1).limit(limit)
    return await cursor.to_list(limit)


def _rng_for_bot(bot: Dict[str, Any], key: str = "") -> random.Random:
    """Per-bot RNG that varies by call so we don't repeat decisions."""
    r = random.Random()
    r.seed(f"{bot.get('user_id')}-{key}-{_now().timestamp() // 900}")
    return r


async def _act_for_bot(
    bot: Dict[str, Any], feuds: List[Dict[str, Any]], burst: bool = False
) -> None:
    persona = bot.get("bot_persona") or {}
    act_prob = float(persona.get("activity_probability", 0.4))
    com_prob = float(persona.get("comment_probability", 0.35))
    story_prob = float(persona.get("story_probability", 0.05))
    if burst:
        # Burst amplifies engagement so verification is immediate.
        act_prob = min(1.0, act_prob * 1.8 + 0.2)
        com_prob = min(1.0, com_prob * 1.5 + 0.1)
        story_prob = min(0.35, story_prob * 3.0 + 0.05)

    rng = _rng_for_bot(bot, "action")
    if rng.random() > act_prob:
        return

    # Pick 1-3 feuds this bot cares about
    fav_categories = set(bot.get("favorite_categories") or [])
    main_topic = persona.get("main_topic")
    scored = _score_feuds_for_bot(feuds, fav_categories, main_topic, rng)
    if not scored:
        return
    n_actions = rng.choice([1, 1, 2, 2, 3])
    for feud in scored[:n_actions]:
        # 1) Vote if not voted yet
        voted_side = await _bot_cast_vote(bot, feud, rng)
        if voted_side is None:
            continue
        # 2) Maybe comment. The commenting side is loosely aligned with
        # the CURRENT vote distribution on the feud (with the bot's own
        # voted side as a mild anchor) so bot comments end up distributed
        # across both factions coherently — not piled up on one side.
        if rng.random() < com_prob:
            comment_side = _pick_comment_side(feud, voted_side, rng)
            await _bot_add_comment(bot, feud, comment_side)
        # 3) Rare: post a story sharing this feud
        if rng.random() < story_prob:
            await _bot_create_story(bot, feud)
        # 3b) Sometimes reply to a REAL user's comment on this feud so
        # the founder/real users get an interaction back on Populus,
        # not just silent votes/comments in parallel. Probability is
        # tuned so ~1 in 4 bot actions on a feud lead to a reply IF a
        # human comment is available to reply to.
        if rng.random() < com_prob * 0.6:
            await _bot_add_reply(bot, feud, voted_side, rng)
        # 4) Hot-news trigger — bot engagement DOES count toward the
        # thresholds (bots are real users of the platform for engagement
        # purposes). Fire-and-forget; the fanout is idempotent (uses the
        # `hot_notified` flag on the feud). Delegates to `server._fanout_hot_news`
        # which we import lazily to avoid a hard dependency cycle.
        try:
            fresh_feud = await _db.feuds.find_one({"feud_id": feud["feud_id"]}, {"_id": 0})
            if fresh_feud and not fresh_feud.get("hot_notified"):
                import server as _server  # lazy import
                await _server._fanout_hot_news(fresh_feud)
        except Exception as e:
            logger.warning(f"bot hot-news fanout failed: {e}")


def _score_feuds_for_bot(
    feuds: List[Dict[str, Any]],
    fav_categories: set,
    main_topic: Optional[str],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """Rank feuds by affinity: main_topic +3, favorites +2, others +0.
    Small random jitter breaks ties naturally.
    """
    def score(f):
        s = rng.random() * 0.5
        cat = f.get("category")
        if cat and cat == main_topic:
            s += 3
        if cat and cat in fav_categories:
            s += 2
        return s
    ranked = sorted(feuds, key=score, reverse=True)
    return ranked


# ─── Vote / Comment / Story ────────────────────────────────────────
async def _bot_cast_vote(
    bot: Dict[str, Any], feud: Dict[str, Any], rng: random.Random
) -> Optional[str]:
    """Cast a vote if the bot hasn't voted on this feud yet. Returns
    the chosen side ('A' or 'B') or the existing side, or None if the
    action was skipped.
    """
    feud_id = feud.get("feud_id")
    user_id = bot["user_id"]
    existing = await _db.votes.find_one({"feud_id": feud_id, "user_id": user_id}, {"_id": 0})
    if existing:
        return existing.get("side")

    side = _pick_side_for_bot(bot, feud, rng)
    now = _now()
    # Insert vote + increment feud counter — mirrors server.vote_feud
    # WITHOUT analytics/notifications.
    try:
        await _db.votes.insert_one({
            "vote_id": f"vote_{user_id}_{feud_id}",
            "feud_id": feud_id,
            "user_id": user_id,
            "side": side,
            "created_at": now,
            "change_count": 0,
            "feud_snapshot": {
                "title": feud.get("title"),
                "category": feud.get("category"),
                "category_label": feud.get("category_label"),
                "party_a": feud.get("party_a"),
                "party_b": feud.get("party_b"),
                "image_url": feud.get("image_url"),
            },
        })
        inc_field = "votes_a" if side == "A" else "votes_b"
        await _db.feuds.update_one({"feud_id": feud_id}, {"$inc": {inc_field: 1}})
        await _db.users.update_one(
            {"user_id": user_id},
            {"$inc": {"total_votes": 1}},
        )
        return side
    except Exception as e:
        # Unique index violation → someone raced us. Fetch the existing.
        exi = await _db.votes.find_one({"feud_id": feud_id, "user_id": user_id}, {"_id": 0})
        if exi:
            return exi.get("side")
        logger.warning(f"bot vote failed for {user_id}/{feud_id}: {e}")
        return None


def _pick_side_for_bot(
    bot: Dict[str, Any], feud: Dict[str, Any], rng: random.Random
) -> str:
    """Choose A or B. For political feuds we use the bot's ideological
    lean (`left_side_probability` → probability that the bot votes for
    whichever side reads as 'progressive'). For non-political feuds
    we're roughly balanced with mild noise so votes look natural.
    """
    persona = bot.get("bot_persona") or {}
    category = feud.get("category") or ""
    if category == "politica":
        left_p = float(persona.get("party_bias", {}).get("left_side_probability", 0.5))
        # Heuristic: read party_a and party_b, decide which one is
        # "progressive" (left-leaning) using a keyword list. If unclear,
        # fall back to a coin flip anchored on the lean.
        left_side = _guess_left_side(feud.get("party_a", ""), feud.get("party_b", ""))
        if left_side is None:
            return "A" if rng.random() < left_p else "B"
        # left_side is 'A' or 'B' — pick it with probability left_p
        return left_side if rng.random() < left_p else ("B" if left_side == "A" else "A")
    # Non-political: near-random with a nudge based on tone
    tone = persona.get("tone", "")
    bias = 0.5
    if tone in ("polemico", "cinico", "sarcastico"):
        bias = 0.55  # slight preference for the underdog (side B)
    return "B" if rng.random() < bias else "A"


def _pick_comment_side(
    feud: Dict[str, Any], voted_side: str, rng: random.Random
) -> str:
    """Choose which side the bot COMMENTS on.

    We deliberately decouple this from the voted side so bot comments
    are distributed across both factions in a way that roughly tracks
    the current vote distribution on the feud (never perfectly — with
    smoothing + jitter so it doesn't look mechanical).

    Blend:
      * p_votes: current vote share of side A, Laplace-smoothed so a
                 brand-new feud with 0 votes defaults to ~0.5 instead
                 of being undefined.
      * voted_anchor: bot's own voted side counts for ~25% of the
                      decision so persona still leaks through.
      * jitter: ±0.06 uniform noise so distribution looks organic.
    """
    try:
        va = int(feud.get("votes_a", 0) or 0)
    except Exception:
        va = 0
    try:
        vb = int(feud.get("votes_b", 0) or 0)
    except Exception:
        vb = 0
    total = va + vb
    # Laplace smoothing (α=3): early feuds gravitate to 50/50, populated
    # feuds converge to the real ratio.
    p_a_votes = (va + 3.0) / (total + 6.0)
    voted_anchor = 1.0 if voted_side == "A" else 0.0
    # 75% distribution, 25% persona anchor
    p_a = 0.75 * p_a_votes + 0.25 * voted_anchor
    # Mild jitter so consecutive bots don't produce identical splits
    p_a += rng.uniform(-0.06, 0.06)
    # Clamp so extreme feuds still get the occasional minority comment
    p_a = max(0.08, min(0.92, p_a))
    return "A" if rng.random() < p_a else "B"


_LEFT_KEYWORDS = re.compile(
    r"\b(sinistr|pd|schlein|conte|m5s|movimento 5 stelle|verdi|"
    r"progressist|riformist|dem|calenda|renzi|europa verde)",
    re.IGNORECASE,
)
_RIGHT_KEYWORDS = re.compile(
    r"\b(destr|meloni|salvini|lega|fdi|fratelli d'italia|berlusconi|"
    r"forza italia|centrodestra|sovranist)",
    re.IGNORECASE,
)


def _guess_left_side(a: str, b: str) -> Optional[str]:
    a_left = bool(_LEFT_KEYWORDS.search(a or ""))
    a_right = bool(_RIGHT_KEYWORDS.search(a or ""))
    b_left = bool(_LEFT_KEYWORDS.search(b or ""))
    b_right = bool(_RIGHT_KEYWORDS.search(b or ""))
    if a_left and not b_left:
        return "A"
    if b_left and not a_left:
        return "B"
    if a_right and not b_right:
        return "B"  # if A is right → left = B
    if b_right and not a_right:
        return "A"
    return None


async def _bot_has_contributed(bot_id: str, feud_id: str) -> bool:
    """Return True if this bot already left a top-level comment OR a
    reply on the given feud. Used to enforce the "one contribution per
    feud per bot" realism rule — real users rarely spam multiple
    comments on the same debate. See bug report: bots leaving many
    comments under the same feud looked unnatural.
    """
    if not bot_id or not feud_id:
        return False
    existing_comment = await _db.comments.find_one(
        {"feud_id": feud_id, "user_id": bot_id}, {"_id": 1}
    )
    if existing_comment:
        return True
    existing_reply = await _db.replies.find_one(
        {"feud_id": feud_id, "user_id": bot_id}, {"_id": 1}
    )
    return existing_reply is not None


async def _bot_add_comment(
    bot: Dict[str, Any], feud: Dict[str, Any], side: str
) -> None:
    """Generate a natural short Italian comment via Claude Haiku 4.5
    and insert it into `comments`. Skips analytics + notifications.

    Enforces one-contribution-per-feud: if this bot already commented
    OR replied on this feud we skip silently (realism guard). The
    check-and-insert critical section is protected by a per-(bot, feud)
    asyncio lock so concurrent bot bursts cannot race duplicates
    through the existence check.
    """
    lock = await _get_contribution_lock(bot["user_id"], feud["feud_id"])
    async with lock:
        if await _bot_has_contributed(bot["user_id"], feud["feud_id"]):
            return
        persona_full = _rehydrate_persona(bot)
        llm_text = await _generate_comment(persona_full, feud, side)
        if not llm_text:
            return
        # Basic length safety + cleanup
        llm_text = _clean_comment(llm_text)
        if not llm_text:
            return
        # Re-check inside the lock, right before the write, in case the LLM
        # call above took long enough that another task committed first.
        if await _bot_has_contributed(bot["user_id"], feud["feud_id"]):
            return
        now = _now()
        doc = {
            "comment_id": f"cmt_bot_{bot['user_id']}_{feud['feud_id']}_{int(now.timestamp())}",
            "feud_id": feud["feud_id"],
            "user_id": bot["user_id"],
            "nickname": bot.get("nickname"),
            "side": side,
            "text": llm_text,
            "mentions": [],
            "created_at": now,
        }
        try:
            await _db.comments.insert_one(doc)
        except Exception as e:
            logger.warning(f"bot comment insert failed: {e}")


async def _bot_add_reply(
    bot: Dict[str, Any], feud: Dict[str, Any], side: str, rng: random.Random,
) -> None:
    """Pick a real-user comment on this feud and post a natural reply.

    Behaviour choices:
      * Only replies to HUMANS (comments authored by non-bot / non-dev users).
        This ensures the founder and real users actually get pinged back,
        which is the whole point of the feature.
      * Skips comments the bot already replied to (idempotent-ish — we
        allow at most one bot reply per human comment per bot).
      * Prefers comments on the SAME side as the bot (agree-and-add) with
        50% probability; otherwise picks any side to keep debate lively.
      * Generates a short natural comment via Claude Haiku with a
        "reply-style" system prompt (references the parent's opinion).
      * Emits a `reply` notification to the parent-comment author via
        `server._emit_notification` (fire-and-forget) so the founder
        SEES the reply arrive.
    """
    feud_id = feud.get("feud_id")
    if not feud_id:
        return
    # Realism guard: at most ONE contribution per feud per bot (either a
    # top-level comment or a reply — never accumulate multiple).
    if await _bot_has_contributed(bot["user_id"], feud_id):
        return
    # Fetch bot ids (used to filter out replies to other bots)
    bot_ids = [
        u["user_id"] async for u in _db.users.find(
            {"is_bot": True}, {"_id": 0, "user_id": 1}
        )
    ]
    # Candidate comments: authored by humans, not by this bot, on this feud
    candidates = await _db.comments.find(
        {
            "feud_id": feud_id,
            "user_id": {"$nin": bot_ids},
        },
        {"_id": 0},
    ).sort("created_at", -1).limit(30).to_list(30)
    if not candidates:
        return
    # Prefer same-side comments with 50% chance
    if rng.random() < 0.5:
        same_side = [c for c in candidates if c.get("side") == side]
        if same_side:
            candidates = same_side
    # Skip comments this bot already replied to
    my_replies = await _db.replies.distinct(
        "comment_id",
        {"user_id": bot["user_id"], "feud_id": feud_id},
    )
    my_replies_set = set(my_replies or [])
    candidates = [c for c in candidates if c.get("comment_id") not in my_replies_set]
    if not candidates:
        return
    parent = rng.choice(candidates)
    parent_text = (parent.get("text") or "").strip()
    reply_text = await _generate_reply(
        _rehydrate_persona(bot), feud, side, parent_text
    )
    if not reply_text:
        return
    reply_text = _clean_comment(reply_text)
    if not reply_text:
        return
    now = _now()
    reply_doc = {
        "reply_id": f"rep_bot_{bot['user_id']}_{parent['comment_id']}_{int(now.timestamp())}",
        "comment_id": parent["comment_id"],
        "feud_id": feud_id,
        "user_id": bot["user_id"],
        "nickname": bot.get("nickname"),
        "side": side,
        "text": reply_text,
        "mentions": [],
        "created_at": now,
    }
    # Serialize the final "re-check + insert" step per (bot, feud) so that
    # concurrent bursts cannot slip two contributions past the guard.
    lock = await _get_contribution_lock(bot["user_id"], feud_id)
    async with lock:
        if await _bot_has_contributed(bot["user_id"], feud_id):
            return
        try:
            await _db.replies.insert_one(reply_doc)
        except Exception as e:
            logger.warning(f"bot reply insert failed: {e}")
            return
    # Notify parent-comment author (never bots).
    try:
        parent_uid = parent.get("user_id")
        if parent_uid and parent_uid not in bot_ids and parent_uid != bot["user_id"]:
            import server as _server  # lazy import to avoid cycle
            await _server._emit_notification(
                parent_uid,
                "reply",
                title="Nuova risposta al tuo commento",
                body=reply_text[:120],
                feud_id=feud_id,
                comment_id=parent["comment_id"],
                side=side,
                actor_id=bot["user_id"],
            )
    except Exception as e:
        logger.warning(f"bot reply notify failed: {e}")


async def _generate_reply(
    persona: Dict[str, Any], feud: Dict[str, Any], side: str, parent_text: str,
) -> Optional[str]:
    """Ask Claude Haiku to reply to a real user's comment on this feud."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return None
    hint = random_style_hint(random.Random(), persona.get("verbosity"))
    system = system_prompt_for(persona, hint)
    side_label = feud.get("party_a") if side == "A" else feud.get("party_b")
    user_prompt = (
        f"Post: «{feud.get('title', '')}» — categoria {feud.get('category_label') or ''}. "
        f"Fazioni: «{feud.get('party_a', '')}» vs «{feud.get('party_b', '')}». "
        f"Tu stai dalla parte di: «{side_label}». "
        f"Un altro utente ha appena scritto questo commento: «{parent_text[:220]}». "
        f"Rispondi in modo naturale, come faresti in una discussione social. "
        f"Puoi essere d'accordo, in disaccordo o aggiungere un punto — a seconda della tua personalità. "
        f"Regole: max 220 caratteri, italiano informale, non usare l'@ del destinatario."
    )
    async with _llm_lock:
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"botreply-{persona.get('display_name', 'x')[:20]}-{int(_now().timestamp())}",
                system_message=system,
            ).with_model("anthropic", "claude-haiku-4-5-20251001")
            resp = await chat.send_message(UserMessage(text=user_prompt))
            return (str(resp) or "").strip() or None
        except Exception as e:
            logger.warning(f"LLM reply gen failed: {e}")
            return None


async def _bot_create_story(bot: Dict[str, Any], feud: Dict[str, Any]) -> None:
    """Post a 'feud' story (24 h TTL) sharing this feud on the bot's
    profile. Optional 1-line caption via LLM.

    Two guardrails prevent the historical over-production of bot stories:
      1) Anti-duplicate: at most ONE active story per (bot, feud). If
         the bot already has an unexpired story about this feud we
         skip — no need to spam multiple takes on the same topic.
      2) Daily quota: at most 1 active story per bot in any 24 h
         rolling window (down from 3). With ~100 bots this caps the
         featured-bot bucket to a sane background hum instead of the
         hundreds of stories we were previously accumulating.
    """
    now = _now()
    # (1) Anti-duplicate: same bot + same feud + still-active TTL.
    dup = await _db.stories.find_one({
        "user_id": bot["user_id"],
        "feud_id": feud["feud_id"],
        "expires_at": {"$gt": now},
    })
    if dup:
        return
    # (2) 1/24h quota per bot.
    since = now - timedelta(hours=_STORY_TTL_HOURS)
    recent = await _db.stories.count_documents({
        "user_id": bot["user_id"],
        "created_at": {"$gte": since},
    })
    if recent >= 1:
        return
    caption = await _generate_story_caption(_rehydrate_persona(bot), feud)
    doc = {
        "story_id": f"story_bot_{bot['user_id']}_{feud['feud_id']}_{int(now.timestamp())}",
        "user_id": bot["user_id"],
        "kind": "feud",
        "feud_id": feud["feud_id"],
        "comment": (caption or "")[:200],
        "created_at": now,
        "expires_at": now + timedelta(hours=_STORY_TTL_HOURS),
        "viewers": [],
    }
    try:
        await _db.stories.insert_one(doc)
    except Exception as e:
        logger.warning(f"bot story insert failed: {e}")


# ─── LLM ────────────────────────────────────────────────────────────
def _rehydrate_persona(bot: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the flat persona summary stored in `bot_persona` with a
    few identity fields so we can reuse the persona helpers.
    """
    p = dict(bot.get("bot_persona") or {})
    p.update({
        "display_name": bot.get("display_name") or bot.get("nickname"),
        "age": bot.get("age"),
        "profession": bot.get("profession") or "utente",
        "city": bot.get("city") or "",
        "region": bot.get("region") or "",
    })
    # Guard against missing keys from older bots
    p.setdefault("tone", "serio")
    p.setdefault("verbosity", "breve")
    p.setdefault("political_lean", "centro")
    p.setdefault("main_topic", "politica")
    return p


async def _generate_comment(
    persona: Dict[str, Any], feud: Dict[str, Any], side: str
) -> Optional[str]:
    """One-shot Claude Haiku 4.5 call. Retries once on failure, then
    returns None so the tick can move on.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        logger.warning(f"emergentintegrations unavailable: {e}")
        return None
    # Per-call random style hint (opening + micro-quirk + optional
    # verbosity override) — feeds into the system prompt so every
    # comment sounds different, even when the same bot posts several
    # in a row.
    hint = random_style_hint(random.Random(), persona.get("verbosity"))
    system = system_prompt_for(persona, hint)
    side_label = feud.get("party_a") if side == "A" else feud.get("party_b")
    other_label = feud.get("party_b") if side == "A" else feud.get("party_a")
    user_prompt = (
        f"Post: «{feud.get('title', '')}». "
        f"Categoria: {feud.get('category_label') or feud.get('category') or ''}. "
        f"Le due fazioni sono «{feud.get('party_a', '')}» e «{feud.get('party_b', '')}». "
        f"Tu stai dalla parte di: «{side_label}» (contro «{other_label}»). "
        f"Scrivi il tuo commento personale sotto questo post."
    )
    async with _llm_lock:
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"bot-{persona.get('display_name', 'x')[:20]}-{int(_now().timestamp())}",
                system_message=system,
            ).with_model("anthropic", "claude-haiku-4-5-20251001")
            resp = await chat.send_message(UserMessage(text=user_prompt))
            return (str(resp) or "").strip() or None
        except Exception as e:
            logger.warning(f"LLM comment gen failed: {e}")
            return None


async def _generate_story_caption(
    persona: Dict[str, Any], feud: Dict[str, Any]
) -> Optional[str]:
    """Short caption for a story SHARING a specific feud.

    Historical bug: the user_prompt used to be a bare 'Genera la frase per
    la story.' with zero feud context — the model then either hallucinated
    a caption unrelated to the shared feud ("fuori luogo") or, worse,
    politely refused with a meta-comment like "non posso commentare, non
    mi hai indicato il post di riferimento" which we then stored verbatim
    as the caption. Now we inject title + parties + category so the
    output is always about the actual feud being shared.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return None
    system = story_prompt_for(persona)
    title = (feud.get('title') or '').strip()
    party_a = (feud.get('party_a') or '').strip()
    party_b = (feud.get('party_b') or '').strip()
    cat = (feud.get('category_label') or feud.get('category') or '').strip()
    parties_line = f"Le due fazioni sono «{party_a}» e «{party_b}». " if party_a and party_b else ""
    user_prompt = (
        f"Stai condividendo QUESTO post nella tua story: «{title}». "
        f"Categoria: {cat}. {parties_line}"
        f"Scrivi UNA sola frase personale (max 120 caratteri) che accompagni la condivisione. "
        f"OBBLIGATORIO: cita almeno UNA delle due fazioni per nome (o il soggetto principale del titolo) "
        f"e prendi una posizione o esprimi un'opinione riconoscibile — non una generica reazione. "
        f"VIETATO scrivere frasi vaghe tipo 'mi piace', 'veramente interessante', 'ma quanto e' vero', "
        f"'mi ritrovo in questo', 'mi tocca il cuore', 'ma dai davvero?' o simili banalita' senza contenuto. "
        f"Rispondi solo con la frase, in italiano informale, senza premesse ne' emoji ne' hashtag."
    )
    async with _llm_lock:
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"botstory-{persona.get('display_name', 'x')[:20]}-{int(_now().timestamp())}",
                system_message=system,
            ).with_model("anthropic", "claude-haiku-4-5-20251001")
            resp = await chat.send_message(UserMessage(text=user_prompt))
            text = (str(resp) or "").strip()
            if not text:
                return None
            # Sanity filter: if the model politely refused or asked for
            # context anyway, drop it (would look worse than no caption).
            # "non posso" e' matchato solo se seguito da un verbo di
            # rifiuto (commentare/generare/…) per non tagliare frasi
            # legittime tipo "non posso credere ai miei occhi".
            low = text.lower()
            _refusal_re = re.compile(
                r"non posso (commentare|generare|dare|fornire|rispondere|scrivere|farlo)"
                r"|non riesco a (commentare|generare|dare|rispondere)"
                r"|non mi hai (dato|fornito|indicato|passato)"
                r"|(puoi (darmi|fornirmi|indicarmi|passarmi)|mi servirebbe|mi serve (il|vedere|leggere|sapere|conoscere))"
                r"|non ho abbastanza (contesto|informazioni)"
                r"|(post|contenuto) di riferimento"
                r"|riferimento a un post"
                r"|non vedo un post"
                r"|(quale|qual) (post|contenuto|argomento) (stai|vuoi|devo|e|hai)"
                r"|non ho ricevuto (il|un) post"
                r"|dammi il post"
                r"|potresti condividere il contenuto"
                r"|serve il contesto"
                r"|manca il post"
                r"|riferirmi a"
                r"|condividere il contenuto"
                r"|mi puoi indicare"
                r"|di che post"
                r"|condividi il contenuto"
                r"|il post che stai condividendo"
                r"|scrivere il commento"
            )
            if _refusal_re.search(low):
                logger.info(f"story caption refusal filtered: {text[:80]}")
                return None
            # Second filter: reject GENERIC / vague captions that don't
            # reference the actual feud content. User complaint: bot
            # captions like "questo mi piace", "veramente interessante",
            # "ma quanto e' vero questo" are too obviously bot-generated
            # and add zero context. We require the caption to reference
            # at least one significant word (>=4 chars) from either
            # `title` or `party_a`/`party_b`. Falls back to accepting
            # captions >=90 chars if no match (a long caption is unlikely
            # to be a bare generic filler).
            import unicodedata as _ud
            def _norm(s: str) -> str:
                s = _ud.normalize('NFD', s.lower())
                return ''.join(c for c in s if not _ud.combining(c))
            _text_norm = _norm(text)
            _pool = f"{title} {party_a} {party_b}"
            _stopwords = {
                'della','delle','degli','dello','sulla','sulle','sugli',
                'sopra','sotto','sono','stato','stata','stanno','essere',
                'questa','questo','questi','queste','quello','quella',
                'quelli','quelle','molto','molta','molti','molte','ancora',
                'anche','dopo','prima','tanto','tanti','proprio','tutti',
                'tutte','contro','vero','vera','bene','male','fatto',
                'fatti','cosa','cose','punto','punti','giorno','giorni',
                'volta','volte','oggi','ieri','domani',
            }
            keywords = {
                _norm(w).strip('«»"\'.,;:!?()[]{}') for w in _pool.split()
                if len(w) >= 4
            }
            keywords = {k for k in keywords if len(k) >= 4 and k not in _stopwords}
            if keywords:
                has_hook = any(k in _text_norm for k in keywords)
                if not has_hook and len(text) < 90:
                    logger.info(f"story caption too generic (no keyword hook): {text[:80]}")
                    return None
            return text
        except Exception as e:
            logger.warning(f"LLM story caption failed: {e}")
            return None


def _clean_comment(text: str) -> str:
    """Post-process LLM output so it looks like a real user's message.

    Strips filler openers (sinceramente, ecco, boh…) that Claude tends
    to slip in despite the system-prompt blacklist. This is a safety
    net, not a replacement for the prompt-level guard.
    """
    if not text:
        return ""
    t = text.strip().strip('"').strip("'").strip("«»").strip()
    # Strip explicit LLM prefaces first.
    t = re.sub(
        r"^(ecco (?:il )?(?:commento|mio commento|il mio parere)[:\-\s]*)",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"^(commento[:\-\s]*)", "", t, flags=re.IGNORECASE)

    # Iteratively strip a banned opener. Sometimes Claude chains two
    # ("Sinceramente, boh, ...") so we loop up to 3 times.
    for _ in range(3):
        stripped = _strip_banned_opener(t)
        if stripped == t:
            break
        t = stripped

    # Cap at 400 chars to be well below the DB limit (500)
    if len(t) > 400:
        t = t[:400].rsplit(" ", 1)[0] + "…"
    return t.strip()


# Precompile once — the list is short and stable.
_BANNED_OPENER_RE = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in BANNED_OPENERS) + r")\b[\s,;:\-—–]*",
    re.IGNORECASE,
)


def _strip_banned_opener(text: str) -> str:
    """Remove one banned opener token from the start of `text`.

    Handles «Sinceramente,», «Sinceramente:», «Sinceramente —», or
    plain «Sinceramente ». If found, uppercase the next initial so the
    sentence still reads naturally.
    """
    m = _BANNED_OPENER_RE.match(text)
    if not m:
        return text
    rest = text[m.end():].lstrip()
    if not rest:
        return text  # avoid nuking the whole comment
    # Uppercase the first character so the sentence still starts clean.
    return rest[0].upper() + rest[1:] if rest else text
