"""
Iter 146 — Verify Task 1 (bot story duplicates + quota 1/24h)

Fix location: /app/backend/bot_engine.py::_bot_create_story
  1) Anti-duplicate: skip if an ACTIVE (expires_at > now) story already
     exists for (user_id=bot, feud_id=X).
  2) Quota 1/24h: skip if the bot has any story created in the last 24h
     (was: 3 previously).

We drive the internal function directly (bypasses LLM budget & scheduler)
and assert the db.stories collection reflects the expected state.

Also does a regression check on GET /api/stories/feed (bot bucket still
served) and validates story doc shape (kind='feud', comment, expires_at,
viewers).
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

# Ensure we can import bot_engine
sys.path.insert(0, "/app/backend")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
ADMIN_EMAIL = "carlofarinapayme@gmail.com"


# ---------- Fixtures --------------------------------------------------

@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture
def bot_engine_mod():
    """Import bot_engine — DB handle is (re)wired inside each async run
    because motor binds to the calling event loop, which asyncio.run
    recreates for every test.

    We snapshot & restore `_generate_story_caption` around each test so
    tests that monkey-patch it don't leak into unrelated tests.
    """
    import bot_engine as be
    _orig_caption = be._generate_story_caption
    try:
        yield be
    finally:
        be._generate_story_caption = _orig_caption


def _wire_db(be):
    """Create a fresh motor client bound to the CURRENT loop."""
    from motor.motor_asyncio import AsyncIOMotorClient
    aclient = AsyncIOMotorClient(MONGO_URL)
    be._db = aclient[DB_NAME]
    return aclient


@pytest.fixture(scope="module")
def bot_user(mongo):
    """Pick any real seeded bot from db.users."""
    b = mongo.users.find_one({"is_bot": True}, {"_id": 0})
    assert b, "No bot users seeded — bot_engine.init() should have seeded 100"
    return b


@pytest.fixture(scope="module")
def two_feuds(mongo):
    """Pick 2 distinct visible feuds."""
    fs = list(mongo.feuds.find({"is_hidden": {"$ne": True}}, {"_id": 0}).limit(5))
    assert len(fs) >= 2, "Need at least 2 visible feuds in DB"
    return fs[0], fs[1]


def _clean_bot_stories(mongo, bot_id: str, feud_ids: list[str]):
    mongo.stories.delete_many({
        "user_id": bot_id,
        "feud_id": {"$in": feud_ids},
    })


# ---------- Task 1a: anti-duplicate per (bot, feud) -------------------

class TestBotStoryAntiDuplicate:
    def test_second_call_same_feud_is_skipped(
        self, bot_engine_mod, bot_user, two_feuds, mongo
    ):
        be = bot_engine_mod
        bot_id = bot_user["user_id"]
        feud, _ = two_feuds
        feud_id = feud["feud_id"]

        _clean_bot_stories(mongo, bot_id, [feud_id])
        try:
            async def run():
                _wire_db(be)
                # Monkey-patch the LLM caption gen so tests never hit the
                # real API. Just returns a fixed caption.
                be._generate_story_caption = lambda persona, feud: _immediate("caption test")
                await be._bot_create_story(bot_user, feud)
                # Same (bot, feud) again — must be skipped by anti-dup.
                await be._bot_create_story(bot_user, feud)

            asyncio.run(run())

            count = mongo.stories.count_documents({
                "user_id": bot_id,
                "feud_id": feud_id,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            })
            assert count == 1, (
                f"Anti-duplicate FAILED: expected 1 active story for "
                f"(bot={bot_id[:8]}, feud={feud_id[:8]}), found {count}"
            )
        finally:
            _clean_bot_stories(mongo, bot_id, [feud_id])

    def test_story_doc_shape_intact(
        self, bot_engine_mod, bot_user, two_feuds, mongo
    ):
        """Verify the created story doc still has the expected fields."""
        be = bot_engine_mod
        bot_id = bot_user["user_id"]
        feud, _ = two_feuds
        feud_id = feud["feud_id"]

        _clean_bot_stories(mongo, bot_id, [feud_id])
        try:
            async def run():
                _wire_db(be)
                be._generate_story_caption = lambda persona, feud: _immediate("shape test")
                await be._bot_create_story(bot_user, feud)

            asyncio.run(run())

            doc = mongo.stories.find_one(
                {"user_id": bot_id, "feud_id": feud_id},
                {"_id": 0},
            )
            assert doc, "Story doc not inserted"
            assert doc["kind"] == "feud", f"kind must be 'feud', got {doc.get('kind')}"
            assert "comment" in doc
            assert isinstance(doc["comment"], str)
            assert "viewers" in doc and doc["viewers"] == []
            assert "expires_at" in doc and "created_at" in doc
            # expires_at should be ~24h after created_at
            delta = doc["expires_at"] - doc["created_at"]
            assert timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1)
            assert doc["story_id"].startswith("story_bot_")
        finally:
            _clean_bot_stories(mongo, bot_id, [feud_id])


# ---------- Task 1b: 1/24h quota --------------------------------------

class TestBotStoryDailyQuota:
    def test_second_feud_within_24h_is_skipped(
        self, bot_engine_mod, bot_user, two_feuds, mongo
    ):
        """After creating 1 story on feud A, a 2nd call on feud B must
        be silently skipped by the quota check (recent >= 1)."""
        be = bot_engine_mod
        bot_id = bot_user["user_id"]
        feud_a, feud_b = two_feuds

        _clean_bot_stories(mongo, bot_id, [feud_a["feud_id"], feud_b["feud_id"]])
        # Also clear any recent bot stories from the last 24h that could
        # trigger the quota block before our test insertion.
        now = datetime.now(timezone.utc)
        mongo.stories.delete_many({
            "user_id": bot_id,
            "created_at": {"$gte": now - timedelta(hours=24)},
        })
        try:
            async def run():
                _wire_db(be)
                be._generate_story_caption = lambda persona, feud: _immediate("quota test")
                await be._bot_create_story(bot_user, feud_a)
                await be._bot_create_story(bot_user, feud_b)

            asyncio.run(run())

            # Only 1 story total for this bot in the last 24h.
            n = mongo.stories.count_documents({
                "user_id": bot_id,
                "created_at": {"$gte": now - timedelta(hours=24)},
            })
            assert n == 1, (
                f"Quota 1/24h FAILED: expected 1 recent story for bot, got {n}"
            )
            # And it must be the FIRST feud (feud_a).
            doc = mongo.stories.find_one(
                {"user_id": bot_id, "created_at": {"$gte": now - timedelta(hours=24)}},
                {"_id": 0},
            )
            assert doc["feud_id"] == feud_a["feud_id"]
        finally:
            _clean_bot_stories(mongo, bot_id, [feud_a["feud_id"], feud_b["feud_id"]])

    def test_after_24h_new_story_allowed(
        self, bot_engine_mod, bot_user, two_feuds, mongo
    ):
        """Simulate: bot has an OLD story (>24h ago). New call must succeed."""
        be = bot_engine_mod
        bot_id = bot_user["user_id"]
        feud_a, feud_b = two_feuds
        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(hours=25)

        _clean_bot_stories(mongo, bot_id, [feud_a["feud_id"], feud_b["feud_id"]])
        # Ensure NO recent stories in last 24h
        mongo.stories.delete_many({
            "user_id": bot_id,
            "created_at": {"$gte": now - timedelta(hours=24)},
        })
        # Insert an old, EXPIRED story for feud_a
        mongo.stories.insert_one({
            "story_id": f"story_bot_{bot_id}_{feud_a['feud_id']}_old",
            "user_id": bot_id,
            "kind": "feud",
            "feud_id": feud_a["feud_id"],
            "comment": "old",
            "created_at": old_ts,
            "expires_at": old_ts + timedelta(hours=24),  # already expired
            "viewers": [],
        })
        try:
            async def run():
                _wire_db(be)
                be._generate_story_caption = lambda persona, feud: _immediate("post24h")
                await be._bot_create_story(bot_user, feud_b)

            asyncio.run(run())

            new_doc = mongo.stories.find_one({
                "user_id": bot_id,
                "feud_id": feud_b["feud_id"],
                "created_at": {"$gte": now - timedelta(minutes=5)},
            }, {"_id": 0})
            assert new_doc, "Bot story should have been created after 24h window"
        finally:
            _clean_bot_stories(mongo, bot_id, [feud_a["feud_id"], feud_b["feud_id"]])


# ---------- Regression: /api/stories/feed still returns bot bucket ----

def _mint_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


class TestStoriesFeedRegression:
    def test_admin_feed_ok(self, api, mongo):
        u = mongo.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
        if not u:
            pytest.skip("Admin user not present")
        token = _mint_token(u["user_id"])
        r = api.get(
            f"{BASE_URL}/api/stories/feed",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "groups" in body
        # No crash, structure intact — bot bucket may or may not have stories
        # depending on whether tick has run recently, but shape must be OK.
        assert isinstance(body["groups"], list)

    def test_feuds_endpoint_ok(self, api):
        r = api.get(f"{BASE_URL}/api/feuds?limit=5", timeout=10)
        assert r.status_code == 200
        data = r.json()
        # Should have "items" or a list — flexible check
        assert isinstance(data, (list, dict))


# ---------- Regression: auth flows still work ------------------------

class TestAuthRegression:
    def test_signup_and_anonymous(self, api):
        # Signup
        email = f"TEST_iter146_{uuid.uuid4().hex[:8]}@populus-it.co"
        r = api.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": email, "password": "TestPass123!", "nickname": f"tst{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r.status_code in (200, 201), r.text
        # Anonymous
        r2 = api.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": f"anon{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r2.status_code in (200, 201), r2.text


# ---------- helper --------------------------------------------------

async def _immediate(v):
    return v
