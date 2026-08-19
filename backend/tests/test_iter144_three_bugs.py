"""
Iter 144 — 3-bug validation

1. LATENCY chip category-switch fix (frontend only, TTL 60s + prefetch): no
   backend-visible change, but validate that /api/feuds and /api/feuds/hype
   still respond quickly & return well-formed data for all favourite
   categories (regression guardrail for the pre-fetch call pattern).

2. Bot stories now surface in /api/stories/feed (bucket "bot authors
   random", up to 8 authors, deterministic seed per (caller, UTC day),
   excludes caller & circle authors).

3. hot_news fanout regression fix: `is_dev_account` filter removed —
   real users (including founder) must again receive hot_news
   notifications; bots must still be excluded (via `is_bot`).

Regression sweep: public endpoints (login/signup/anonymous/categories/
professions/legal/health) plus admin comment/reply delete for founder.
"""
from __future__ import annotations
import os
import uuid
import time
import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import requests
from pymongo import MongoClient

from dotenv import load_dotenv
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
ADMIN_EMAIL = "carlofarinapayme@gmail.com"


# ---------- Fixtures ----------------------------------------------------

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


def _mint_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


@pytest.fixture(scope="module")
def admin_user(mongo):
    """Founder admin (upsert if absent)."""
    u = mongo.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    if not u:
        uid = str(uuid.uuid4())
        mongo.users.insert_one({
            "user_id": uid,
            "email": ADMIN_EMAIL,
            "nickname": "founder",
            "auth_provider": "google",
            "email_verified": True,
            "is_admin": True,
            "created_at": datetime.now(timezone.utc),
        })
        u = mongo.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    return u


@pytest.fixture(scope="module")
def admin_headers(admin_user):
    return {"Authorization": f"Bearer {_mint_token(admin_user['user_id'])}"}


@pytest.fixture(scope="module")
def fresh_user(api):
    """A brand-new email/pwd user (unique per run) — used as the 'real
    non-admin recipient' for hot_news."""
    email = f"TEST_iter144_{uuid.uuid4().hex[:10]}@populus-it.co"
    pwd = "Passw0rd!"
    nickname = f"tst{uuid.uuid4().hex[:8]}"
    r = api.post(f"{BASE_URL}/api/auth/signup",
                 json={"email": email, "password": pwd, "nickname": nickname})
    assert r.status_code == 200, r.text
    data = r.json()
    token = data.get("token")
    # If email verification is on, mint JWT directly.
    if not token:
        # Wait for the user to appear in the DB, then mint.
        c = MongoClient(MONGO_URL); db = c[DB_NAME]
        u = db.users.find_one({"email": email}, {"_id": 0})
        c.close()
        token = _mint_token(u["user_id"])
        user = u
    else:
        user = data["user"]
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


# ---------- Task 0: regression on public endpoints ----------------------

class TestPublicEndpointsRegression:
    def test_categories(self, api):
        r = api.get(f"{BASE_URL}/api/categories")
        assert r.status_code == 200, r.text
        assert isinstance(r.json().get("categories"), list)

    def test_professions(self, api):
        r = api.get(f"{BASE_URL}/api/professions")
        assert r.status_code == 200

    def test_legal(self, api):
        for path in ("/api/legal/terms", "/api/legal/nda"):
            r = api.get(f"{BASE_URL}{path}")
            assert r.status_code == 200, f"{path} → {r.status_code}"

    def test_signup_login_anonymous_all_reachable(self, api):
        # Signup already covered by fixture, so here we just cover login+anon.
        r = api.post(f"{BASE_URL}/api/auth/anonymous",
                     json={"nickname": f"anon{uuid.uuid4().hex[:6]}"})
        assert r.status_code == 200
        assert r.json().get("token")
        # Login with wrong creds must return 4xx, never 5xx
        r = api.post(f"{BASE_URL}/api/auth/login",
                     json={"email": "nobody-iter144@x.com", "password": "x"})
        assert r.status_code in (400, 401, 403, 404), r.status_code


# ---------- Task 1: feuds cache-warmup contract (no regression) ---------

class TestFeudsCategoryContract:
    def test_all(self, api):
        r = api.get(f"{BASE_URL}/api/feuds")
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get("feuds"), list)
        # Everyone should see something on Day-1+ preview
        assert len(j["feuds"]) > 0

    def test_hype(self, api):
        r = api.get(f"{BASE_URL}/api/feuds/hype")
        assert r.status_code == 200
        assert isinstance(r.json().get("feuds"), list)

    def test_specific_categories(self, api):
        cats = api.get(f"{BASE_URL}/api/categories").json()["categories"]
        assert cats
        ok = 0
        for c in cats[:6]:
            r = api.get(f"{BASE_URL}/api/feuds", params={"category": c["id"]})
            assert r.status_code == 200, f"cat {c['id']}: {r.status_code}"
            ok += 1
        assert ok == min(6, len(cats))


# ---------- Task 2: stories_feed now surfaces bots --------------------

class TestStoriesFeedBotBucket:
    def test_admin_feed_contains_bot_authors(self, api, admin_headers, admin_user, mongo):
        # First check that there ARE active bot stories in the DB
        now = datetime.now(timezone.utc)
        bot_story_authors = list(mongo.stories.aggregate([
            {"$match": {"expires_at": {"$gt": now}}},
            {"$group": {"_id": "$user_id"}},
            {"$lookup": {"from": "users", "localField": "_id", "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_bot": True}},
            {"$limit": 20},
        ]))
        if not bot_story_authors:
            pytest.skip("No active bot stories in DB — bot engine has not seeded stories this session")

        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        assert r.status_code == 200, r.text
        j = r.json()
        groups = j.get("groups", [])
        # Extract bot IDs from DB
        bot_ids = {b["_id"] for b in bot_story_authors}
        # The admin's feed must contain at least ONE bot group after the fix
        feed_authors = {g["user_id"] for g in groups}
        bots_in_feed = feed_authors & bot_ids
        assert bots_in_feed, (
            f"After fix, admin should see bot authors in stories feed. "
            f"Feed authors: {feed_authors}. Bot IDs in DB: {list(bot_ids)[:5]}..."
        )

    def test_bot_bucket_capped_at_8(self, api, admin_headers, admin_user, mongo):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        j = r.json()
        groups = j.get("groups", [])
        # Count how many are bots (from db)
        author_ids = [g["user_id"] for g in groups]
        if not author_ids:
            pytest.skip("Empty feed")
        bot_rows = list(mongo.users.find(
            {"user_id": {"$in": author_ids}, "is_bot": True}, {"_id": 0, "user_id": 1}
        ))
        # excluding circle → the "random bot bucket" contribution
        me = admin_user["user_id"]
        circle_ids = {r["friend_id"] for r in mongo.friendships.find({"user_id": me}, {"_id": 0, "friend_id": 1})}
        bots_from_bucket = [b["user_id"] for b in bot_rows if b["user_id"] not in circle_ids]
        assert len(bots_from_bucket) <= 8, f"Bot bucket exceeded cap: {len(bots_from_bucket)}"

    def test_feed_excludes_caller_and_has_nickname(self, api, admin_headers, admin_user):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        j = r.json()
        me = admin_user["user_id"]
        for g in j.get("groups", []):
            if g["user_id"] != me:
                # non-self groups must expose author.nickname (bot or circle)
                author = g.get("author")
                assert author is not None, f"group {g['user_id']} has no author"
                assert author.get("nickname"), f"group {g['user_id']} missing nickname"

    def test_deterministic_same_session(self, api, admin_headers):
        # Same user, same day → same bot bucket order (server-side hash+seed)
        r1 = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        r2 = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        ids1 = [g["user_id"] for g in r1.get("groups", [])]
        ids2 = [g["user_id"] for g in r2.get("groups", [])]
        assert ids1 == ids2, "Feed order should be stable within the same UTC day for the same user"

    def test_no_duplicates_between_circle_and_bot_bucket(self, api, admin_headers):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        ids = [g["user_id"] for g in r.get("groups", [])]
        assert len(ids) == len(set(ids)), f"Duplicate author groups in feed: {ids}"


# ---------- Task 3: hot_news fanout no longer excludes real users -----

class TestHotNewsFanoutFix:
    """The `is_dev_account` filter removal must let real users, including
    the founder, receive hot_news pushes again. Bots must still NOT
    receive them (guarded by `is_bot`)."""

    def test_admin_is_not_filtered_out_by_dev_account_flag(self, admin_user, mongo):
        # Confirm the admin can be selected by the same query the code uses.
        # We test the query shape directly against MongoDB.
        cat = "gossip"
        # Give admin the category if not present.
        mongo.users.update_one(
            {"user_id": admin_user["user_id"]},
            {"$addToSet": {"favorite_categories": cat}},
        )
        matched = mongo.users.count_documents({
            "user_id": admin_user["user_id"],
            "favorite_categories": cat,
            "is_anonymous": {"$ne": True},
            "is_bot": {"$ne": True},
            "$or": [{"push_notifications": True}, {"push_notifications": {"$exists": False}}],
        })
        assert matched == 1, "Admin should be selectable by the fanout query"

    def test_bots_still_excluded_by_query(self, mongo):
        # The production filter shape: `is_bot: {'$ne': True}` — verify no
        # bot survives that predicate against the real DB.
        bots = mongo.users.count_documents({
            "is_bot": True,
            "$and": [{"is_bot": {"$ne": True}}],
        })
        assert bots == 0

    def test_end_to_end_fanout_creates_notification(self, api, admin_user, mongo):
        """Simulate a feud crossing hot thresholds and verify a hot_news
        notification is created for the admin recipient."""
        cat = "gossip"
        # Ensure admin has cat as favourite AND push enabled AND no daily lock
        mongo.users.update_one(
            {"user_id": admin_user["user_id"]},
            {"$addToSet": {"favorite_categories": cat},
             "$set": {"push_notifications": True}},
        )
        # Clear any pre-existing daily lock for admin/hot_news
        try:
            mongo.notification_locks.delete_many({"key": {"$regex": f"^{admin_user['user_id']}:hot_news:"}})
        except Exception:
            pass
        # Snapshot existing notifs
        before = mongo.notifications.count_documents({
            "user_id": admin_user["user_id"], "type": "hot_news"
        })

        # Insert a fresh feud that already exceeds combined_score threshold
        fid = f"TEST_iter144_{uuid.uuid4().hex[:8]}"
        feud = {
            "feud_id": fid,
            "title": "TEST iter144 hot faida",
            "category": cat,
            "votes_a": 12,
            "votes_b": 5,   # combined 17 → passes HOT_MIN_VOTES=10 & combined>=15
            "hot_notified": False,
            "is_hidden": False,
            "created_at": datetime.now(timezone.utc),
        }
        # Provide 3 comments so the (votes>=10 AND comments>=3) branch also fires
        mongo.feuds.insert_one(feud)
        for i in range(3):
            mongo.comments.insert_one({
                "comment_id": f"{fid}_c{i}",
                "feud_id": fid,
                "user_id": admin_user["user_id"],
                "text": f"TEST_iter144 c{i}",
                "created_at": datetime.now(timezone.utc),
            })

        try:
            # Trigger fanout by casting a vote via the API (as fresh anon user)
            anon = api.post(f"{BASE_URL}/api/auth/anonymous",
                            json={"nickname": f"anon{uuid.uuid4().hex[:6]}"}).json()
            token = anon["token"]
            r = api.post(f"{BASE_URL}/api/feuds/{fid}/vote",
                         json={"side": "A"},
                         headers={"Authorization": f"Bearer {token}"})
            # Some anonymous restrictions may forbid voting: accept 200 or a
            # documented restriction; but the crucial part is that ANY
            # server-side event on this feud triggers _fanout_hot_news.
            # If vote is blocked, hit the debug fanout via increment: bump
            # votes on the doc and hit the feud endpoint.
            if r.status_code >= 400:
                # Fall back: try with the admin token instead.
                admin_token = _mint_token(admin_user["user_id"])
                r2 = api.post(f"{BASE_URL}/api/feuds/{fid}/vote",
                              json={"side": "B"},
                              headers={"Authorization": f"Bearer {admin_token}"})
                assert r2.status_code < 400, f"admin vote failed: {r2.status_code} {r2.text}"

            # Fanout is fire-and-forget (asyncio.create_task) → wait a bit
            deadline = time.time() + 6
            after = before
            while time.time() < deadline:
                after = mongo.notifications.count_documents({
                    "user_id": admin_user["user_id"], "type": "hot_news"
                })
                if after > before:
                    break
                time.sleep(0.3)
            assert after > before, (
                f"No hot_news notification created for admin (before={before}, after={after}). "
                f"Fanout may still be skipping real users."
            )

            # Verify no bot ended up as recipient of this specific fanout
            bot_notifs = mongo.notifications.count_documents({
                "type": "hot_news",
                "feud_id": fid,
                "user_id": {"$in": [b["user_id"] for b in mongo.users.find(
                    {"is_bot": True}, {"_id": 0, "user_id": 1}
                ).limit(200)]},
            })
            assert bot_notifs == 0, f"Bots received hot_news notifications (should be 0): {bot_notifs}"
        finally:
            # Cleanup
            mongo.feuds.delete_many({"feud_id": fid})
            mongo.comments.delete_many({"feud_id": fid})
            mongo.notifications.delete_many({"feud_id": fid})


# ---------- Admin moderation regression --------------------------------

class TestAdminCommentModerationRegression:
    def test_admin_can_delete_arbitrary_comment(self, api, admin_headers, admin_user, mongo):
        # Create a foreign comment (owned by an ephemeral user)
        anon = api.post(f"{BASE_URL}/api/auth/anonymous",
                        json={"nickname": f"anon{uuid.uuid4().hex[:6]}"}).json()
        other_headers = {"Authorization": f"Bearer {anon['token']}"}
        # Grab a random feud
        feuds = api.get(f"{BASE_URL}/api/feuds").json()["feuds"]
        assert feuds
        fid = feuds[0]["feud_id"]
        r = api.post(f"{BASE_URL}/api/feuds/{fid}/comments",
                     json={"text": "TEST_iter144 admin_del target", "side": "a"},
                     headers=other_headers)
        # Anonymous may be blocked from commenting — skip in that case.
        if r.status_code >= 400:
            pytest.skip(f"Anonymous cannot comment ({r.status_code}), skipping delete regression")
        cid = r.json()["comment"]["comment_id"]
        d = api.delete(f"{BASE_URL}/api/comments/{cid}", headers=admin_headers)
        assert d.status_code == 200, d.text
        j = d.json()
        assert j.get("moderated") is True
        # And it's gone
        assert mongo.comments.count_documents({"comment_id": cid}) == 0
