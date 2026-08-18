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
        # 2) Maybe comment
        if rng.random() < com_prob:
            await _bot_add_comment(bot, feud, voted_side)
        # 3) Rare: post a story sharing this feud
        if rng.random() < story_prob:
            await _bot_create_story(bot, feud)
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


async def _bot_add_comment(
    bot: Dict[str, Any], feud: Dict[str, Any], side: str
) -> None:
    """Generate a natural short Italian comment via Claude Haiku 4.5
    and insert it into `comments`. Skips analytics + notifications.
    """
    persona_full = _rehydrate_persona(bot)
    llm_text = await _generate_comment(persona_full, feud, side)
    if not llm_text:
        return
    # Basic length safety + cleanup
    llm_text = _clean_comment(llm_text)
    if not llm_text:
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


async def _bot_create_story(bot: Dict[str, Any], feud: Dict[str, Any]) -> None:
    """Post a 'feud' story (24 h TTL) sharing this feud on the bot's
    profile. Optional 1-line caption via LLM.
    """
    # Respect story quota (5/day by default)
    since = _now() - timedelta(hours=_STORY_TTL_HOURS)
    recent = await _db.stories.count_documents({
        "user_id": bot["user_id"],
        "created_at": {"$gte": since},
    })
    if recent >= 3:
        return
    caption = await _generate_story_caption(_rehydrate_persona(bot))
    now = _now()
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


async def _generate_story_caption(persona: Dict[str, Any]) -> Optional[str]:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return None
    system = story_prompt_for(persona)
    async with _llm_lock:
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"botstory-{persona.get('display_name', 'x')[:20]}-{int(_now().timestamp())}",
                system_message=system,
            ).with_model("anthropic", "claude-haiku-4-5-20251001")
            resp = await chat.send_message(UserMessage(text="Genera la frase per la story."))
            return (str(resp) or "").strip() or None
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
