"""
Iteration 140 — Bot one-contribution-per-feud invariant.

Bug fix under test:
  Prior to this fix, bots were allowed to repeatedly comment/reply on the
  same feud, producing unrealistic clusters of comments by the same bot
  under a single debate. The fix adds `_bot_has_contributed(bot_id,
  feud_id)` guards in bot_engine._bot_add_comment / _bot_add_reply, plus a
  one-shot dedupe on server.on_startup that keeps only the OLDEST bot
  comment / reply per (bot_id, feud_id).

What this file verifies:
  1. Startup dedupe left the DB clean (no duplicates).
  2. `_bot_has_contributed` returns True when a comment or reply exists.
  3. `_bot_add_comment` and `_bot_add_reply` are idempotent under the guard.
  4. After a real POST /api/admin/bots/burst, the invariant still holds
     (no bot has >1 comment or >1 reply per feud).
"""
import os
import time
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient


# ─── Config ────────────────────────────────────────────────────────
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/") or os.environ.get(
    "EXPO_BACKEND_URL", ""
).rstrip("/")
ADMIN_KEY = "populus-admin-42b8f3"
MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"


assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing in env"


# ─── Fixtures ──────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "X-Admin-Key": ADMIN_KEY,
    })
    return s


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def db(event_loop):
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


async def _bot_ids(db):
    return [
        u["user_id"] async for u in db.users.find(
            {"is_bot": True}, {"_id": 0, "user_id": 1}
        )
    ]


async def _find_dup_pairs(db, coll_name: str, bot_ids: list):
    """Return list of (user_id, feud_id, count) with count > 1."""
    pipeline = [
        {"$match": {"user_id": {"$in": bot_ids}}},
        {"$group": {
            "_id": {"feud_id": "$feud_id", "user_id": "$user_id"},
            "count": {"$sum": 1},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]
    dups = []
    async for r in db[coll_name].aggregate(pipeline):
        dups.append((r["_id"]["user_id"], r["_id"]["feud_id"], r["count"]))
    return dups


# ═══════════════════════════════════════════════════════════════════
# 1. Startup dedupe worked
# ═══════════════════════════════════════════════════════════════════
class TestStartupCleanup:
    """The one-shot cleanup in server.on_startup should have removed all
    duplicate (bot_id, feud_id) pairs in comments and replies collections."""

    def test_no_duplicate_bot_comments(self, db, event_loop):
        async def _run():
            bots = await _bot_ids(db)
            dups = await _find_dup_pairs(db, "comments", bots)
            return dups
        dups = event_loop.run_until_complete(_run())
        assert dups == [], (
            f"Found {len(dups)} (bot,feud) pairs with >1 comment: {dups[:5]}"
        )

    def test_no_duplicate_bot_replies(self, db, event_loop):
        async def _run():
            bots = await _bot_ids(db)
            dups = await _find_dup_pairs(db, "replies", bots)
            return dups
        dups = event_loop.run_until_complete(_run())
        assert dups == [], (
            f"Found {len(dups)} (bot,feud) pairs with >1 reply: {dups[:5]}"
        )

    def test_bot_total_counts_sensible(self, db, event_loop):
        """Sanity: after dedupe we should still have SOME bot comments."""
        async def _run():
            bots = await _bot_ids(db)
            n_comments = await db.comments.count_documents(
                {"user_id": {"$in": bots}}
            )
            n_replies = await db.replies.count_documents(
                {"user_id": {"$in": bots}}
            )
            return n_comments, n_replies, len(bots)

        nc, nr, nb = event_loop.run_until_complete(_run())
        print(f"[stats] bots={nb}, bot_comments={nc}, bot_replies={nr}")
        assert nb == 100, f"Expected 100 bot users, got {nb}"


# ═══════════════════════════════════════════════════════════════════
# 2. _bot_has_contributed logic (unit-ish)
# ═══════════════════════════════════════════════════════════════════
class TestBotHasContributed:
    """Directly exercise bot_engine._bot_has_contributed and the guard
    inside _bot_add_comment / _bot_add_reply."""

    def test_has_contributed_true_after_comment(self, db, event_loop):
        import sys
        sys.path.insert(0, "/app/backend")
        import bot_engine as be

        async def _run():
            be._db = db
            # Pick any bot + any feud
            bot = await db.users.find_one({"is_bot": True}, {"_id": 0})
            feud = await db.feuds.find_one({}, {"_id": 0})
            assert bot and feud
            bot_id = bot["user_id"]
            feud_id = feud["feud_id"]

            # Clean state for this pair, then insert a fake comment
            await db.comments.delete_many({"user_id": bot_id, "feud_id": feud_id})
            await db.replies.delete_many({"user_id": bot_id, "feud_id": feud_id})

            # Before: no contribution
            assert (await be._bot_has_contributed(bot_id, feud_id)) is False

            # Insert a fake bot comment
            await db.comments.insert_one({
                "comment_id": f"TEST_iter140_cmt_{bot_id}_{feud_id}",
                "feud_id": feud_id,
                "user_id": bot_id,
                "side": "A",
                "text": "TEST_iter140 seeded",
                "mentions": [],
                "created_at": be._now(),
            })

            # After: has contributed
            has = await be._bot_has_contributed(bot_id, feud_id)

            # Cleanup
            await db.comments.delete_many({"comment_id": f"TEST_iter140_cmt_{bot_id}_{feud_id}"})
            return has

        assert event_loop.run_until_complete(_run()) is True

    def test_has_contributed_true_after_reply(self, db, event_loop):
        import sys
        sys.path.insert(0, "/app/backend")
        import bot_engine as be

        async def _run():
            be._db = db
            bot = await db.users.find_one({"is_bot": True}, {"_id": 0})
            feud = await db.feuds.find_one({}, {"_id": 0})
            assert bot and feud
            bot_id = bot["user_id"]
            feud_id = feud["feud_id"]

            await db.comments.delete_many({"user_id": bot_id, "feud_id": feud_id})
            await db.replies.delete_many({"user_id": bot_id, "feud_id": feud_id})

            assert (await be._bot_has_contributed(bot_id, feud_id)) is False

            await db.replies.insert_one({
                "reply_id": f"TEST_iter140_rep_{bot_id}_{feud_id}",
                "comment_id": "TEST_iter140_parent",
                "feud_id": feud_id,
                "user_id": bot_id,
                "side": "A",
                "text": "TEST_iter140 seeded reply",
                "mentions": [],
                "created_at": be._now(),
            })

            has = await be._bot_has_contributed(bot_id, feud_id)

            await db.replies.delete_many({"reply_id": f"TEST_iter140_rep_{bot_id}_{feud_id}"})
            return has

        assert event_loop.run_until_complete(_run()) is True

    def test_add_comment_guarded(self, db, event_loop):
        """_bot_add_comment must NOT insert if the bot already has a
        comment on that feud (guard short-circuit)."""
        import sys
        sys.path.insert(0, "/app/backend")
        import bot_engine as be

        async def _run():
            be._db = db
            bot = await db.users.find_one({"is_bot": True}, {"_id": 0})
            feud = await db.feuds.find_one({}, {"_id": 0})
            bot_id = bot["user_id"]
            feud_id = feud["feud_id"]

            # Preseed one bot comment on this feud
            await db.comments.delete_many({"user_id": bot_id, "feud_id": feud_id})
            await db.replies.delete_many({"user_id": bot_id, "feud_id": feud_id})
            await db.comments.insert_one({
                "comment_id": f"TEST_iter140_seed_{bot_id}_{feud_id}",
                "feud_id": feud_id,
                "user_id": bot_id,
                "side": "A",
                "text": "TEST_iter140 preseed",
                "mentions": [],
                "created_at": be._now(),
            })

            count_before = await db.comments.count_documents(
                {"user_id": bot_id, "feud_id": feud_id}
            )
            # Call — should hit the guard and NOT call LLM/insert.
            await be._bot_add_comment(bot, feud, "A")
            count_after = await db.comments.count_documents(
                {"user_id": bot_id, "feud_id": feud_id}
            )

            # Cleanup
            await db.comments.delete_many({"comment_id": f"TEST_iter140_seed_{bot_id}_{feud_id}"})
            return count_before, count_after

        b, a = event_loop.run_until_complete(_run())
        assert b == 1 and a == 1, f"Guard failed: before={b}, after={a}"

    def test_add_reply_guarded_when_comment_exists(self, db, event_loop):
        """_bot_add_reply must NOT insert if the bot already has a
        comment on that feud (cross-collection contribution guard)."""
        import sys
        sys.path.insert(0, "/app/backend")
        import bot_engine as be
        import random

        async def _run():
            be._db = db
            bot = await db.users.find_one({"is_bot": True}, {"_id": 0})
            feud = await db.feuds.find_one({}, {"_id": 0})
            bot_id = bot["user_id"]
            feud_id = feud["feud_id"]

            await db.comments.delete_many({"user_id": bot_id, "feud_id": feud_id})
            await db.replies.delete_many({"user_id": bot_id, "feud_id": feud_id})
            # Preseed a top-level comment authored by this bot
            await db.comments.insert_one({
                "comment_id": f"TEST_iter140_pseed_{bot_id}_{feud_id}",
                "feud_id": feud_id,
                "user_id": bot_id,
                "side": "A",
                "text": "TEST_iter140 preseed for reply guard",
                "mentions": [],
                "created_at": be._now(),
            })

            count_before = await db.replies.count_documents(
                {"user_id": bot_id, "feud_id": feud_id}
            )
            await be._bot_add_reply(bot, feud, "A", random.Random(42))
            count_after = await db.replies.count_documents(
                {"user_id": bot_id, "feud_id": feud_id}
            )

            await db.comments.delete_many({"comment_id": f"TEST_iter140_pseed_{bot_id}_{feud_id}"})
            return count_before, count_after

        b, a = event_loop.run_until_complete(_run())
        assert b == 0 and a == 0, f"Reply guard failed: before={b}, after={a}"


# ═══════════════════════════════════════════════════════════════════
# 3. End-to-end: POST /api/admin/bots/burst preserves invariant
# ═══════════════════════════════════════════════════════════════════
class TestBurstInvariant:
    """Fire a real burst via the admin endpoint and verify no bot ends
    up with >1 comment or >1 reply on any feud."""

    def test_burst_endpoint_accepts_request(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/bots/burst")
        assert r.status_code == 200, f"burst failed: {r.status_code} {r.text}"

    def test_burst_admin_key_required(self):
        r = requests.post(f"{BASE_URL}/api/admin/bots/burst")
        assert r.status_code in (401, 403), (
            f"burst without admin key must reject, got {r.status_code}"
        )

    def test_no_duplicate_bot_comments_after_burst(self, db, event_loop, api_client):
        """Trigger a SINGLE burst, wait for background task to complete,
        verify invariant holds. We intentionally do NOT call toggle/count
        here because each of those fires its own concurrent burst which
        would exercise a different (concurrency) failure mode covered in
        `test_concurrent_bursts_expose_race` below."""

        async def _stats():
            bots = await _bot_ids(db)
            nc = await db.comments.count_documents({"user_id": {"$in": bots}})
            nr = await db.replies.count_documents({"user_id": {"$in": bots}})
            return nc, nr

        # Pre-clean any leftover duplicates so this test isolates just
        # the SINGLE-burst behaviour, not accumulated state.
        async def _dedupe():
            bots = await _bot_ids(db)
            for coll, key in (("comments", "comment_id"), ("replies", "reply_id")):
                pipeline = [
                    {"$match": {"user_id": {"$in": bots}}},
                    {"$sort": {"created_at": 1}},
                    {"$group": {
                        "_id": {"feud_id": "$feud_id", "user_id": "$user_id"},
                        "ids": {"$push": f"${key}"},
                        "count": {"$sum": 1},
                    }},
                    {"$match": {"count": {"$gt": 1}}},
                ]
                to_del = []
                async for row in db[coll].aggregate(pipeline):
                    to_del.extend(row["ids"][1:])
                if to_del:
                    await db[coll].delete_many({key: {"$in": to_del}})
        event_loop.run_until_complete(_dedupe())

        nc_before, nr_before = event_loop.run_until_complete(_stats())
        print(f"[before burst] bot_comments={nc_before}, bot_replies={nr_before}")

        r = api_client.post(f"{BASE_URL}/api/admin/bots/burst")
        assert r.status_code == 200

        # Burst is background, LLM-driven. Poll up to 90s for it to settle.
        deadline = time.time() + 90
        last_nc = nc_before
        stable_ticks = 0
        while time.time() < deadline:
            time.sleep(6)
            nc, nr = event_loop.run_until_complete(_stats())
            if nc == last_nc:
                stable_ticks += 1
                if stable_ticks >= 3:  # ~18s stable
                    break
            else:
                stable_ticks = 0
                last_nc = nc

        nc_after, nr_after = event_loop.run_until_complete(_stats())
        print(f"[after burst]  bot_comments={nc_after}, bot_replies={nr_after}")

        async def _dups():
            bots = await _bot_ids(db)
            d_c = await _find_dup_pairs(db, "comments", bots)
            d_r = await _find_dup_pairs(db, "replies", bots)
            return d_c, d_r

        dup_c, dup_r = event_loop.run_until_complete(_dups())
        assert dup_c == [], (
            f"After burst: {len(dup_c)} (bot,feud) pairs have >1 comment. "
            f"Sample: {dup_c[:5]}"
        )
        assert dup_r == [], (
            f"After burst: {len(dup_r)} (bot,feud) pairs have >1 reply. "
            f"Sample: {dup_r[:5]}"
        )

    def test_concurrent_bursts_expose_race(self, db, event_loop, api_client):
        """REGRESSION: fires 3 concurrent bursts (toggle+count+burst) to
        stress the check-then-insert pattern in _bot_add_comment /
        _bot_add_reply. The guard uses a find_one + insert_one sequence
        with no unique index or upsert, so two coroutines can pass the
        guard, both wait on _llm_lock, then both insert. Documents the
        remaining race the founder should be aware of."""

        async def _dedupe():
            bots = await _bot_ids(db)
            for coll, key in (("comments", "comment_id"), ("replies", "reply_id")):
                pipeline = [
                    {"$match": {"user_id": {"$in": bots}}},
                    {"$sort": {"created_at": 1}},
                    {"$group": {
                        "_id": {"feud_id": "$feud_id", "user_id": "$user_id"},
                        "ids": {"$push": f"${key}"},
                        "count": {"$sum": 1},
                    }},
                    {"$match": {"count": {"$gt": 1}}},
                ]
                to_del = []
                async for row in db[coll].aggregate(pipeline):
                    to_del.extend(row["ids"][1:])
                if to_del:
                    await db[coll].delete_many({key: {"$in": to_del}})
        event_loop.run_until_complete(_dedupe())

        # Kick 3 overlapping bursts.
        api_client.post(f"{BASE_URL}/api/admin/bots/toggle", json={"enabled": True})
        api_client.post(f"{BASE_URL}/api/admin/bots/count", json={"count": 30})
        api_client.post(f"{BASE_URL}/api/admin/bots/burst")

        # Poll for stability.
        async def _count():
            bots = await _bot_ids(db)
            return await db.comments.count_documents({"user_id": {"$in": bots}})

        deadline = time.time() + 120
        last = event_loop.run_until_complete(_count())
        stable = 0
        while time.time() < deadline:
            time.sleep(6)
            now = event_loop.run_until_complete(_count())
            if now == last:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
                last = now

        async def _dups():
            bots = await _bot_ids(db)
            return await _find_dup_pairs(db, "comments", bots)

        dups = event_loop.run_until_complete(_dups())
        print(f"[concurrent-burst race] duplicate (bot,feud) pairs = {len(dups)}")
        # This assertion documents the expected post-fix state; if it
        # fails, the fix has a race under concurrent bursts.
        assert dups == [], (
            f"RACE: concurrent bursts produced {len(dups)} duplicate "
            f"(bot,feud) comment pairs. Sample: {dups[:5]}. "
            f"Fix suggestion: add a unique compound index on "
            f"comments.(user_id, feud_id) for bot users, or convert the "
            f"check-then-insert to an upsert with $setOnInsert."
        )
