"""
Iter 145 — Task 2 visibility fix follow-up

Main agent applied the recommended patch: `_story_is_visible_to`
(server.py:7267-7308) now short-circuits to True when the story author
is a bot (is_bot == True), while:
  - viewer_id == author_id → visible
  - viewer_id in author.story_hidden_viewers → NOT visible
  - real (non-bot) author without friendships edge → NOT visible

This module re-validates:
  1. /api/stories/feed contains bot authors (up to 8) for a real user
  2. Non-bot users out of caller's circle stay invisible (regression)
  3. story_hidden_viewers still blocks (bot + non-bot)
  4. /api/stories/user/{author_id} works for a bot author
  5. hot_news fanout regression (admin gets, bots do NOT)
"""
from __future__ import annotations
import os
import uuid
import time
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
def fresh_user(api, mongo):
    """A brand-new email/pwd user (unique per run) — used as the 'real
    non-admin' viewer to test regression scenarios independent of admin."""
    email = f"test_iter145_{uuid.uuid4().hex[:10]}@populus-it.co"
    pwd = "Passw0rd!"
    nickname = f"tst{uuid.uuid4().hex[:8]}"
    r = api.post(f"{BASE_URL}/api/auth/signup",
                 json={"email": email, "password": pwd, "nickname": nickname})
    assert r.status_code == 200, r.text
    data = r.json()
    token = data.get("token")
    if not token:
        # Backend requires email verification → look up the freshly inserted
        # user by lowercased email (stored lowercased) and mint a JWT directly.
        u = mongo.users.find_one({"email": email.lower()}, {"_id": 0})
        assert u is not None, f"user not inserted after signup: {email}"
        # Flip verification so any endpoint checking `email_verified` accepts us.
        mongo.users.update_one({"user_id": u["user_id"]}, {"$set": {"email_verified": True}})
        u["email_verified"] = True
        token = _mint_token(u["user_id"])
        user = u
    else:
        user = data["user"]
    yield {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}
    # cleanup
    try:
        mongo.users.delete_one({"email": email.lower()})
        mongo.friendships.delete_many({"user_id": user["user_id"]})
        mongo.friendships.delete_many({"friend_id": user["user_id"]})
        mongo.stories.delete_many({"user_id": user["user_id"]})
    except Exception:
        pass


# ---------- Task 2: bot stories surface for real users -----------------

class TestStoriesFeedBotBucket:
    def test_admin_feed_contains_bot_authors(self, api, admin_headers, admin_user, mongo):
        now = datetime.now(timezone.utc)
        bot_story_authors = list(mongo.stories.aggregate([
            {"$match": {"expires_at": {"$gt": now}}},
            {"$group": {"_id": "$user_id"}},
            {"$lookup": {"from": "users", "localField": "_id", "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_bot": True}},
            {"$limit": 20},
        ]))
        if not bot_story_authors:
            pytest.skip("No active bot stories in DB")

        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        assert r.status_code == 200, r.text
        j = r.json()
        groups = j.get("groups", [])
        bot_ids = {b["_id"] for b in bot_story_authors}
        feed_authors = {g["user_id"] for g in groups}
        bots_in_feed = feed_authors & bot_ids
        assert bots_in_feed, (
            f"After fix, admin should see bot authors in stories feed. "
            f"Feed authors count: {len(feed_authors)}. "
            f"Bot story authors in DB: {len(bot_ids)}"
        )

    def test_bot_bucket_capped_at_8(self, api, admin_headers, admin_user, mongo):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        j = r.json()
        groups = j.get("groups", [])
        author_ids = [g["user_id"] for g in groups]
        if not author_ids:
            pytest.skip("Empty feed")
        bot_rows = list(mongo.users.find(
            {"user_id": {"$in": author_ids}, "is_bot": True}, {"_id": 0, "user_id": 1}
        ))
        me = admin_user["user_id"]
        circle_ids = {r["friend_id"] for r in mongo.friendships.find(
            {"user_id": me}, {"_id": 0, "friend_id": 1}
        )}
        bots_from_bucket = [b["user_id"] for b in bot_rows if b["user_id"] not in circle_ids]
        assert len(bots_from_bucket) <= 8, f"Bot bucket exceeded cap: {len(bots_from_bucket)}"

    def test_feed_groups_have_author_info(self, api, admin_headers, admin_user):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers)
        j = r.json()
        me = admin_user["user_id"]
        for g in j.get("groups", []):
            if g["user_id"] != me:
                author = g.get("author")
                assert author is not None, f"group {g['user_id']} has no author"
                assert author.get("nickname"), f"group {g['user_id']} missing nickname"

    def test_deterministic_same_session(self, api, admin_headers):
        r1 = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        r2 = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        ids1 = [g["user_id"] for g in r1.get("groups", [])]
        ids2 = [g["user_id"] for g in r2.get("groups", [])]
        assert ids1 == ids2, "Feed order should be stable within the same UTC day"

    def test_no_duplicates_between_circle_and_bot_bucket(self, api, admin_headers):
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=admin_headers).json()
        ids = [g["user_id"] for g in r.get("groups", [])]
        assert len(ids) == len(set(ids)), f"Duplicate author groups: {ids}"

    def test_fresh_user_also_sees_bots(self, api, fresh_user, mongo):
        """A brand-new user (no friendships) must still see bots after the fix."""
        now = datetime.now(timezone.utc)
        has_bot_story = mongo.stories.count_documents({"expires_at": {"$gt": now}})
        if not has_bot_story:
            pytest.skip("No active stories in DB")
        r = api.get(f"{BASE_URL}/api/stories/feed", headers=fresh_user["headers"])
        assert r.status_code == 200, r.text
        groups = r.json().get("groups", [])
        # Must contain at least one bot (fresh user has zero circle)
        bot_user_ids = {u["user_id"] for u in mongo.users.find(
            {"is_bot": True, "user_id": {"$in": [g["user_id"] for g in groups]}},
            {"_id": 0, "user_id": 1},
        )}
        assert bot_user_ids, (
            f"Fresh user (no circle) should see bot stories via the bot bucket, "
            f"got {len(groups)} groups but 0 bots."
        )


# ---------- Task 2 endpoint: /api/stories/user/{bot_id} -----------------

class TestStoriesByUserForBot:
    def test_real_user_can_fetch_bot_stories(self, api, admin_headers, mongo):
        now = datetime.now(timezone.utc)
        # Find a bot with at least one active story
        pipe = list(mongo.stories.aggregate([
            {"$match": {"expires_at": {"$gt": now}}},
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_bot": True}},
            {"$limit": 1},
        ]))
        if not pipe:
            pytest.skip("No active bot story to probe")
        bot_id = pipe[0]["user_id"]
        r = api.get(f"{BASE_URL}/api/stories/user/{bot_id}", headers=admin_headers)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("user_id") == bot_id
        stories = j.get("stories", [])
        assert len(stories) > 0, (
            f"Real caller should see the bot's stories (bot_id={bot_id}) "
            f"but got 0. Endpoint likely still gated by friendship."
        )

    def test_fresh_user_can_fetch_bot_stories(self, api, fresh_user, mongo):
        """Same but with a brand-new user (zero circle)."""
        now = datetime.now(timezone.utc)
        pipe = list(mongo.stories.aggregate([
            {"$match": {"expires_at": {"$gt": now}}},
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_bot": True}},
            {"$limit": 1},
        ]))
        if not pipe:
            pytest.skip("No active bot story")
        bot_id = pipe[0]["user_id"]
        r = api.get(f"{BASE_URL}/api/stories/user/{bot_id}", headers=fresh_user["headers"])
        assert r.status_code == 200, r.text
        assert len(r.json().get("stories", [])) > 0


# ---------- Regression: real users out of circle stay invisible --------

class TestRealUsersStillGatedByCircle:
    """Insert an ephemeral real (non-bot) author with a story, ensure the
    caller who is NOT in their (or their) circle CANNOT see it."""

    def test_non_bot_out_of_circle_invisible(self, api, fresh_user, mongo):
        # Create a foreign real user (non-bot) with a live story
        foreign_id = f"TEST_iter145_author_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        mongo.users.insert_one({
            "user_id": foreign_id,
            "email": f"{foreign_id}@populus-it.co",
            "nickname": f"foreign_{uuid.uuid4().hex[:6]}",
            "is_bot": False,
            "auth_provider": "email",
            "created_at": now,
        })
        story_id = f"TEST_iter145_st_{uuid.uuid4().hex[:8]}"
        mongo.stories.insert_one({
            "story_id": story_id,
            "user_id": foreign_id,
            "kind": "text",
            "text": "TEST_iter145 gated non-bot story",
            "created_at": now,
            "expires_at": now + timedelta(hours=23),
        })
        try:
            # Feed must NOT include this author
            r = api.get(f"{BASE_URL}/api/stories/feed", headers=fresh_user["headers"])
            assert r.status_code == 200
            authors = {g["user_id"] for g in r.json().get("groups", [])}
            assert foreign_id not in authors, (
                "Non-bot user out of viewer's circle must not appear in feed"
            )

            # Direct fetch on /api/stories/user/{foreign_id} → empty stories
            r2 = api.get(f"{BASE_URL}/api/stories/user/{foreign_id}",
                         headers=fresh_user["headers"])
            assert r2.status_code == 200
            got = r2.json().get("stories", [])
            assert got == [], (
                f"Real non-bot author's stories must remain hidden from "
                f"a viewer outside their circle; got {len(got)}"
            )
        finally:
            mongo.stories.delete_many({"user_id": foreign_id})
            mongo.users.delete_one({"user_id": foreign_id})

    def test_non_bot_in_circle_visible(self, api, fresh_user, mongo):
        """Positive control: same setup + a friendships edge → visible."""
        foreign_id = f"TEST_iter145_friend_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        mongo.users.insert_one({
            "user_id": foreign_id,
            "email": f"{foreign_id}@populus-it.co",
            "nickname": f"friend_{uuid.uuid4().hex[:6]}",
            "is_bot": False,
            "auth_provider": "email",
            "created_at": now,
        })
        story_id = f"TEST_iter145_stF_{uuid.uuid4().hex[:8]}"
        mongo.stories.insert_one({
            "story_id": story_id,
            "user_id": foreign_id,
            "kind": "text",
            "text": "TEST_iter145 friend story",
            "created_at": now,
            "expires_at": now + timedelta(hours=23),
        })
        # Add friendship edge from fresh_user → foreign
        mongo.friendships.insert_one({
            "user_id": fresh_user["user"]["user_id"],
            "friend_id": foreign_id,
            "created_at": now,
        })
        try:
            r = api.get(f"{BASE_URL}/api/stories/user/{foreign_id}",
                        headers=fresh_user["headers"])
            assert r.status_code == 200
            got = r.json().get("stories", [])
            assert len(got) >= 1, "In-circle real user's stories must be visible"
        finally:
            mongo.stories.delete_many({"user_id": foreign_id})
            mongo.users.delete_one({"user_id": foreign_id})
            mongo.friendships.delete_many({"friend_id": foreign_id})


# ---------- Regression: story_hidden_viewers still blocks --------------

class TestHiddenViewersRespected:
    def test_hidden_viewer_blocked_for_bot_author(self, api, fresh_user, mongo):
        """If a bot has fresh_user in story_hidden_viewers, that user must
        NOT see the bot's stories (even though bots normally bypass)."""
        now = datetime.now(timezone.utc)
        # Find any bot with a live story
        pipe = list(mongo.stories.aggregate([
            {"$match": {"expires_at": {"$gt": now}}},
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id", "as": "u"}},
            {"$match": {"u.is_bot": True}},
            {"$limit": 1},
        ]))
        if not pipe:
            pytest.skip("No active bot story")
        bot_id = pipe[0]["user_id"]
        viewer_id = fresh_user["user"]["user_id"]

        # Add viewer to the bot's hidden_viewers
        mongo.users.update_one(
            {"user_id": bot_id},
            {"$addToSet": {"story_hidden_viewers": viewer_id}},
        )
        try:
            r = api.get(f"{BASE_URL}/api/stories/user/{bot_id}",
                        headers=fresh_user["headers"])
            assert r.status_code == 200
            got = r.json().get("stories", [])
            assert got == [], (
                f"Viewer in author.story_hidden_viewers must be blocked "
                f"even for bot authors; got {len(got)}"
            )
        finally:
            mongo.users.update_one(
                {"user_id": bot_id},
                {"$pull": {"story_hidden_viewers": viewer_id}},
            )

    def test_hidden_viewer_blocked_for_non_bot_in_circle(self, api, fresh_user, mongo):
        """Real non-bot author, viewer HAS friendship edge, but is on
        story_hidden_viewers → must NOT see stories."""
        author_id = f"TEST_iter145_hidauth_{uuid.uuid4().hex[:8]}"
        viewer_id = fresh_user["user"]["user_id"]
        now = datetime.now(timezone.utc)
        mongo.users.insert_one({
            "user_id": author_id,
            "email": f"{author_id}@populus-it.co",
            "nickname": f"hidauth_{uuid.uuid4().hex[:6]}",
            "is_bot": False,
            "auth_provider": "email",
            "story_hidden_viewers": [viewer_id],
            "created_at": now,
        })
        mongo.stories.insert_one({
            "story_id": f"TEST_iter145_sthid_{uuid.uuid4().hex[:8]}",
            "user_id": author_id,
            "kind": "text",
            "text": "TEST_iter145 hidden viewer test",
            "created_at": now,
            "expires_at": now + timedelta(hours=23),
        })
        mongo.friendships.insert_one({
            "user_id": viewer_id,
            "friend_id": author_id,
            "created_at": now,
        })
        try:
            r = api.get(f"{BASE_URL}/api/stories/user/{author_id}",
                        headers=fresh_user["headers"])
            assert r.status_code == 200
            got = r.json().get("stories", [])
            assert got == [], (
                f"Viewer in story_hidden_viewers must be blocked even with "
                f"friendship edge; got {len(got)}"
            )
        finally:
            mongo.stories.delete_many({"user_id": author_id})
            mongo.users.delete_one({"user_id": author_id})
            mongo.friendships.delete_many({"friend_id": author_id})


# ---------- Regression: hot_news still works ---------------------------

class TestHotNewsFanoutStillWorks:
    def test_end_to_end_fanout_creates_notification(self, api, admin_user, mongo):
        cat = "gossip"
        mongo.users.update_one(
            {"user_id": admin_user["user_id"]},
            {"$addToSet": {"favorite_categories": cat},
             "$set": {"push_notifications": True}},
        )
        try:
            mongo.notification_locks.delete_many(
                {"key": {"$regex": f"^{admin_user['user_id']}:hot_news:"}}
            )
        except Exception:
            pass
        before = mongo.notifications.count_documents({
            "user_id": admin_user["user_id"], "type": "hot_news"
        })

        fid = f"TEST_iter145_{uuid.uuid4().hex[:8]}"
        feud = {
            "feud_id": fid,
            "title": "TEST iter145 hot faida",
            "category": cat,
            "votes_a": 12,
            "votes_b": 5,
            "hot_notified": False,
            "is_hidden": False,
            "created_at": datetime.now(timezone.utc),
        }
        mongo.feuds.insert_one(feud)
        for i in range(3):
            mongo.comments.insert_one({
                "comment_id": f"{fid}_c{i}",
                "feud_id": fid,
                "user_id": admin_user["user_id"],
                "text": f"TEST_iter145 c{i}",
                "created_at": datetime.now(timezone.utc),
            })
        try:
            admin_token = _mint_token(admin_user["user_id"])
            r = api.post(f"{BASE_URL}/api/feuds/{fid}/vote",
                         json={"side": "B"},
                         headers={"Authorization": f"Bearer {admin_token}"})
            assert r.status_code < 400, f"vote failed: {r.status_code} {r.text}"

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
                f"hot_news notification not created (before={before}, after={after})"
            )

            bot_notifs = mongo.notifications.count_documents({
                "type": "hot_news",
                "feud_id": fid,
                "user_id": {"$in": [b["user_id"] for b in mongo.users.find(
                    {"is_bot": True}, {"_id": 0, "user_id": 1}
                ).limit(200)]},
            })
            assert bot_notifs == 0, f"Bots got hot_news (should be 0): {bot_notifs}"
        finally:
            mongo.feuds.delete_many({"feud_id": fid})
            mongo.comments.delete_many({"feud_id": fid})
            mongo.notifications.delete_many({"feud_id": fid})
