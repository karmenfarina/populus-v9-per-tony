"""Strict tests for `/api/circle/suggestions` (iter58 fix).

The endpoint must ONLY surface users the viewer is really connected to:
  1. DM contacts ("chat")
  2. Reply exchanges ("commenti")

Explicitly excluded from surfacing:
  - Friends-of-friends (indirect graph traversal)
  - Pure co-commenters on the same feud (no reply link)
  - Self, blocked pairs, users already in the viewer's circle
"""
import os
import time
import uuid

import bcrypt
import pytest
import requests
from pymongo import MongoClient


BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

CHAT_A = {"email": "chat_a@test.it", "password": "test123"}
CHAT_B = {"email": "chat_b@test.it", "password": "test123"}


def _login(creds):
    r = requests.post(f"{API}/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login {creds['email']} failed: {r.status_code} {r.text}"
    j = r.json()
    return j["token"], j["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


@pytest.fixture(scope="module")
def sessions():
    ta, ua = _login(CHAT_A)
    tb, ub = _login(CHAT_B)
    return {"a": (ta, ua), "b": (tb, ub)}


@pytest.fixture(scope="module")
def chat_c(mongo):
    """Create a fresh verified user chat_c directly in Mongo (signup requires
    email verification which we cannot complete from a test)."""
    # Purge any leftover TEST_chat_c* from a previous run.
    old = list(mongo.users.find({"email": {"$regex": r"^chat_c_\w+@test\.it$"}}, {"_id": 0, "user_id": 1}))
    for u in old:
        uid = u["user_id"]
        mongo.users.delete_many({"user_id": uid})
        mongo.friendships.delete_many({"$or": [{"user_id": uid}, {"friend_id": uid}]})
        mongo.comments.delete_many({"user_id": uid})
        mongo.replies.delete_many({"user_id": uid})
        mongo.votes.delete_many({"user_id": uid})
        mongo.messages.delete_many({"$or": [{"sender_id": uid}, {"recipient_id": uid}]})
        mongo.user_blocks.delete_many({"$or": [{"blocker_id": uid}, {"blocked_id": uid}]})

    suffix = uuid.uuid4().hex[:6]
    email = f"chat_c_{suffix}@test.it"
    user_id = f"user_c_{suffix}"
    nickname = f"chat_c_{suffix}"
    pwd_hash = bcrypt.hashpw(b"test123", bcrypt.gensalt()).decode("utf-8")
    doc = {
        "user_id": user_id,
        "email": email,
        "nickname": nickname,
        "password_hash": pwd_hash,
        "auth_provider": "email",
        "email_verified": True,
        "created_at": __import__("datetime").datetime.utcnow(),
        "majority_votes": 0,
        "minority_votes": 0,
        "total_votes": 0,
        "onboarding_completed": True,
        "terms_accepted": True,
        "favorite_categories": ["politica"],
        "age": 30,
        "sex": "na",
        "region": "Lazio",
    }
    mongo.users.insert_one(doc)
    # Login to get a token.
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": "test123"}, timeout=15)
    assert r.status_code == 200, f"chat_c login failed: {r.status_code} {r.text}"
    token = r.json()["token"]

    yield {"user_id": user_id, "token": token, "email": email, "nickname": nickname}

    # Full cleanup.
    mongo.users.delete_one({"user_id": user_id})
    mongo.friendships.delete_many({"$or": [{"user_id": user_id}, {"friend_id": user_id}]})
    mongo.comments.delete_many({"user_id": user_id})
    mongo.replies.delete_many({"user_id": user_id})
    mongo.votes.delete_many({"user_id": user_id})
    mongo.messages.delete_many({"$or": [{"sender_id": user_id}, {"recipient_id": user_id}]})
    mongo.user_blocks.delete_many({"$or": [{"blocker_id": user_id}, {"blocked_id": user_id}]})


def _pick_active_feud(token):
    r = requests.get(f"{API}/feuds", headers=_auth(token), timeout=15)
    assert r.status_code == 200, r.text
    feuds = r.json().get("feuds", [])
    assert feuds, "no active feuds available for test"
    return feuds[0]["feud_id"]


def _vote(token, feud_id, side="A"):
    r = requests.post(f"{API}/feuds/{feud_id}/vote", json={"side": side}, headers=_auth(token), timeout=15)
    # 400 = already voted with same side — acceptable.
    assert r.status_code in (200, 400), f"vote failed: {r.status_code} {r.text}"


def _get_suggestions(token, limit=40):
    r = requests.get(f"{API}/circle/suggestions?limit={limit}", headers=_auth(token), timeout=15)
    assert r.status_code == 200, r.text
    return r.json().get("users", [])


# ---------------------------------------------------------------------------
# Test 1 — DM contact surfaces with "chat" reason
# ---------------------------------------------------------------------------

class TestDmContact:
    def test_dm_contact_appears_with_chat_reason(self, sessions):
        ta, ua = sessions["a"]
        tb, ub = sessions["b"]

        # Ensure at least one DM (idempotent — sends a fresh ping).
        r = requests.post(
            f"{API}/messages/send",
            json={"recipient_id": ub["user_id"], "text": f"TEST_ping_{uuid.uuid4().hex[:5]}"},
            headers=_auth(ta),
            timeout=15,
        )
        assert r.status_code in (200, 201), r.text

        # Ensure chat_b is NOT in chat_a's circle (would otherwise be excluded).
        requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)

        users = _get_suggestions(ta)
        b_row = next((u for u in users if u["user_id"] == ub["user_id"]), None)
        assert b_row is not None, f"chat_b should appear as DM contact. Got: {[u['user_id'] for u in users]}"
        assert "chat" in b_row["reasons"], f"expected 'chat' in reasons, got {b_row['reasons']}"
        # Reasons must ONLY contain the strict labels.
        for reason in b_row["reasons"]:
            assert reason in ("chat", "commenti"), f"unexpected reason: {reason}"


# ---------------------------------------------------------------------------
# Test 2 — Reply exchange surfaces with "commenti" reason
# ---------------------------------------------------------------------------

class TestReplyExchange:
    def test_reply_exchange_appears(self, sessions, mongo):
        ta, ua = sessions["a"]
        tb, ub = sessions["b"]

        feud_id = _pick_active_feud(ta)
        # Both vote (side A) so they can comment/reply.
        _vote(ta, feud_id, "A")
        _vote(tb, feud_id, "A")

        # chat_a posts a comment.
        r = requests.post(
            f"{API}/feuds/{feud_id}/comments",
            json={"text": f"TEST_parent_{uuid.uuid4().hex[:5]}"},
            headers=_auth(ta),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        cmt_id = r.json()["comment"]["comment_id"]

        # chat_b replies.
        rr = requests.post(
            f"{API}/comments/{cmt_id}/replies",
            json={"text": f"TEST_reply_{uuid.uuid4().hex[:5]}"},
            headers=_auth(tb),
            timeout=15,
        )
        assert rr.status_code == 200, rr.text
        reply_id = rr.json()["reply"]["reply_id"]

        # Make sure chat_b is NOT in chat_a's circle (else excluded).
        requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)

        try:
            users = _get_suggestions(ta)
            b_row = next((u for u in users if u["user_id"] == ub["user_id"]), None)
            assert b_row is not None, f"chat_b (reply exchange) must appear. Got: {[u['user_id'] for u in users]}"
            assert "commenti" in b_row["reasons"], (
                f"expected 'commenti' in reasons, got {b_row['reasons']}"
            )
            # "chat" may also be present (from Test 1 DM history) — that's fine.
            for reason in b_row["reasons"]:
                assert reason in ("chat", "commenti"), f"unexpected reason: {reason}"
        finally:
            # Cleanup: delete the reply and comment we created.
            requests.delete(f"{API}/replies/{reply_id}", headers=_auth(tb), timeout=10)
            requests.delete(f"{API}/comments/{cmt_id}", headers=_auth(ta), timeout=10)


# ---------------------------------------------------------------------------
# Test 3 — Pure co-commenter must NOT appear
# ---------------------------------------------------------------------------

class TestPureCoCommenterExcluded:
    def test_pure_cocommenter_not_in_suggestions(self, sessions, chat_c, mongo):
        ta, ua = sessions["a"]
        tc = chat_c["token"]
        uc_id = chat_c["user_id"]

        feud_id = _pick_active_feud(ta)
        # chat_a and chat_c both vote and comment on the same feud
        # (but never reply to each other and never DM).
        _vote(ta, feud_id, "A")
        _vote(tc, feud_id, "A")

        ra = requests.post(
            f"{API}/feuds/{feud_id}/comments",
            json={"text": f"TEST_a_cocom_{uuid.uuid4().hex[:5]}"},
            headers=_auth(ta), timeout=15,
        )
        assert ra.status_code == 200, ra.text
        a_cmt = ra.json()["comment"]["comment_id"]

        rc = requests.post(
            f"{API}/feuds/{feud_id}/comments",
            json={"text": f"TEST_c_cocom_{uuid.uuid4().hex[:5]}"},
            headers=_auth(tc), timeout=15,
        )
        assert rc.status_code == 200, rc.text
        c_cmt = rc.json()["comment"]["comment_id"]

        try:
            users = _get_suggestions(ta, limit=40)
            ids = [u["user_id"] for u in users]
            assert uc_id not in ids, (
                f"chat_c (pure co-commenter) MUST NOT appear. suggestions payload: "
                f"{[{'user_id': u['user_id'], 'reasons': u['reasons']} for u in users]}"
            )
        finally:
            requests.delete(f"{API}/comments/{a_cmt}", headers=_auth(ta), timeout=10)
            requests.delete(f"{API}/comments/{c_cmt}", headers=_auth(tc), timeout=10)


# ---------------------------------------------------------------------------
# Test 4 — Friends-of-friends must NOT appear
# ---------------------------------------------------------------------------

class TestFriendsOfFriendsExcluded:
    def test_friend_of_friend_not_in_suggestions(self, sessions, chat_c, mongo):
        ta, ua = sessions["a"]
        tb, ub = sessions["b"]
        tc = chat_c["token"]
        uc_id = chat_c["user_id"]

        # Ensure chat_a and chat_c have NO direct connection:
        # - no DMs
        # - no reply exchanges
        mongo.messages.delete_many(
            {"$or": [
                {"sender_id": ua["user_id"], "recipient_id": uc_id},
                {"sender_id": uc_id, "recipient_id": ua["user_id"]},
            ]}
        )
        # Clean any leftover comments/replies chat_c may have created earlier.
        mongo.comments.delete_many({"user_id": uc_id})
        mongo.replies.delete_many({"user_id": uc_id})

        # Build the FoF graph: chat_b -> chat_a's circle, chat_c -> chat_b's circle.
        add_b = requests.post(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)
        assert add_b.status_code in (200, 201), add_b.text
        add_c = requests.post(f"{API}/circle/{uc_id}", headers=_auth(tb), timeout=10)
        assert add_c.status_code in (200, 201), add_c.text

        try:
            users = _get_suggestions(ta, limit=40)
            ids = [u["user_id"] for u in users]
            assert uc_id not in ids, (
                f"chat_c (friend-of-friend) MUST NOT appear. suggestions payload: "
                f"{[{'user_id': u['user_id'], 'reasons': u['reasons']} for u in users]}"
            )
        finally:
            requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)
            requests.delete(f"{API}/circle/{uc_id}", headers=_auth(tb), timeout=10)


# ---------------------------------------------------------------------------
# Test 5 — Self / already-in-circle / blocked exclusion
# ---------------------------------------------------------------------------

class TestExclusions:
    def test_self_never_appears(self, sessions):
        ta, ua = sessions["a"]
        users = _get_suggestions(ta)
        assert ua["user_id"] not in [u["user_id"] for u in users], (
            "viewer must never appear in own suggestions"
        )

    def test_in_circle_excluded(self, sessions):
        ta, _ = sessions["a"]
        _, ub = sessions["b"]
        r = requests.post(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)
        assert r.status_code in (200, 201), r.text
        try:
            users = _get_suggestions(ta)
            assert ub["user_id"] not in [u["user_id"] for u in users], (
                "circle members must be excluded"
            )
        finally:
            requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)

    def test_blocked_excluded(self, sessions):
        ta, ua = sessions["a"]
        _, ub = sessions["b"]
        # chat_a blocks chat_b
        r = requests.post(f"{API}/users/{ub['user_id']}/block", headers=_auth(ta), timeout=10)
        assert r.status_code == 200, r.text
        try:
            users = _get_suggestions(ta)
            assert ub["user_id"] not in [u["user_id"] for u in users], (
                "blocked users must be excluded from suggestions"
            )
        finally:
            requests.delete(f"{API}/users/{ub['user_id']}/block", headers=_auth(ta), timeout=10)
