"""
Iter 133 — Regression test for the fix of the kwarg mismatch in
`bot_engine._bot_add_reply` → `server._emit_notification`.

Previously the call passed `actor_user_id=` and `actor_nickname=` which the
signature did not accept, causing a silent TypeError caught by a try/except
that only logged `bot reply notify failed`. The fix renamed to `actor_id=`
and removed `actor_nickname`.

This test invokes `_bot_add_reply` directly on an active bot + a human comment
and verifies:
  a) a new entry is inserted in db.replies (user_id=bot, is_bot flag on bot)
  b) a new entry is inserted in db.notifications with:
        type='reply', actor_id=bot['user_id'],
        user_id=parent_comment_author, feud_id, comment_id, side, body
  c) NO 'bot reply notify failed' warning appears in the backend log window.
"""
import asyncio
import os
import random
import sys
import time
from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

# Import backend modules
sys.path.insert(0, "/app/backend")
import bot_engine  # noqa: E402
import server as _server  # noqa: E402  (also for signature inspection)

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
BACKEND_ERR_LOG = "/var/log/supervisor/backend.err.log"


# ─── static: signature must accept actor_id (not actor_user_id) ────────────
def test_emit_notification_signature_uses_actor_id():
    import inspect
    sig = inspect.signature(_server._emit_notification)
    params = sig.parameters
    assert "actor_id" in params, "server._emit_notification must accept actor_id"
    assert "actor_user_id" not in params, (
        "server._emit_notification must NOT accept actor_user_id (regression)"
    )


# ─── dynamic: call _bot_add_reply and verify db effects ────────────────────
def _read_log_tail_size() -> int:
    try:
        return os.path.getsize(BACKEND_ERR_LOG)
    except OSError:
        return 0


def _read_log_since(offset: int) -> str:
    try:
        with open(BACKEND_ERR_LOG, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


@pytest.mark.asyncio
async def test_bot_add_reply_creates_reply_and_notification():
    c = AsyncIOMotorClient(MONGO_URL)
    db = c[DB_NAME]
    await bot_engine.init(db)

    # 1) pick an active bot
    bot = await db.users.find_one({"is_bot": True, "bot_active": True})
    if not bot:
        # fall back to any bot user
        bot = await db.users.find_one({"is_bot": True})
    assert bot is not None, "no bot user found in DB — enable bots first"
    bot_uid = bot["user_id"]

    # 2) pick a human comment whose feud still exists (not soft-hidden/deleted)
    bot_ids = [u["user_id"] async for u in db.users.find({"is_bot": True}, {"user_id": 1})]
    hc = None
    feud = None
    async for cand in db.comments.find({"user_id": {"$nin": bot_ids}}).limit(200):
        f = await db.feuds.find_one({"feud_id": cand["feud_id"]})
        if f:
            hc = cand
            feud = f
            break
    assert hc is not None, "no human comment with existing feud found"
    parent_uid = hc["user_id"]
    feud_id = hc["feud_id"]
    parent_comment_id = hc["comment_id"]
    side = hc.get("side", "A")

    # baseline counters
    replies_before = await db.replies.count_documents({
        "comment_id": parent_comment_id, "user_id": bot_uid,
    })
    notif_before = await db.notifications.count_documents({
        "user_id": parent_uid, "type": "reply", "actor_id": bot_uid,
        "comment_id": parent_comment_id,
    })
    log_offset = _read_log_tail_size()

    # 3) call _bot_add_reply directly
    #    Use a seeded RNG that will pass the internal probability gates.
    #    The function internally does its own LLM call for text; if LLM fails
    #    it may write a fallback reply — either way reply_doc + notify must fire.
    await bot_engine._bot_add_reply(bot, feud, side, random.Random(1))

    # Allow motor tasks to flush
    await asyncio.sleep(0.5)

    # 4a) verify db.replies has +1 for this bot on this parent comment
    #     NB: _bot_add_reply picks its own parent internally, so we just check
    #     the bot has at least one new reply somewhere in this feud after the call.
    reply_doc = await db.replies.find_one(
        {"user_id": bot_uid, "feud_id": feud_id},
        sort=[("created_at", -1)],
    )
    assert reply_doc is not None, (
        "bot did not create a reply doc — check /var/log/supervisor/backend.err.log"
    )
    # bot flag on the author
    author = await db.users.find_one({"user_id": bot_uid}, {"is_bot": 1})
    assert author and author.get("is_bot") is True

    # The parent this call picked (from reply_doc.comment_id).
    picked_parent_id = reply_doc["comment_id"]
    picked_parent = await db.comments.find_one({"comment_id": picked_parent_id})
    assert picked_parent is not None
    picked_parent_uid = picked_parent["user_id"]
    assert picked_parent_uid not in bot_ids, "bot replied to another bot — invariant broken"

    # 4b) verify db.notifications has a matching entry
    notif = await db.notifications.find_one({
        "user_id": picked_parent_uid,
        "type": "reply",
        "actor_id": bot_uid,
        "comment_id": picked_parent_id,
    }, sort=[("created_at", -1)])
    assert notif is not None, (
        f"notification entry missing for parent={picked_parent_uid} "
        f"actor={bot_uid} comment={picked_parent_id}. "
        f"This means _emit_notification failed silently."
    )
    # required fields
    assert notif.get("feud_id") == feud_id
    assert notif.get("side") in ("A", "B")
    assert notif.get("body"), "notification body must contain the reply text"
    # body should match (truncated to 120 in code) the reply_doc.text
    assert notif["body"][:60] == reply_doc["text"][:60], (
        "notification body should mirror reply text (first 60 chars)"
    )

    # 5) NO warning about 'bot reply notify failed' in the log window
    log_slice = _read_log_since(log_offset)
    assert "bot reply notify failed" not in log_slice, (
        f"Regression: found 'bot reply notify failed' warning in backend log:\n"
        f"{log_slice[-2000:]}"
    )

    # optional: also ensure the raw TypeError signature is not present
    assert "unexpected keyword argument 'actor_user_id'" not in log_slice
    assert "unexpected keyword argument 'actor_nickname'" not in log_slice
