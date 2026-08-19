"""Backend tests for founder-admin moderation of comments and replies.

Feature under test:
- DELETE /api/comments/{comment_id}
- DELETE /api/replies/{reply_id}
Founder admin (email == carlofarinapayme@gmail.com) can delete ANY
comment/reply. Response must include `moderated: true` only when the
admin deletes content authored by someone else.
"""
import os
import uuid
import time
import pytest
import requests
import bcrypt
import jwt as pyjwt
from dotenv import load_dotenv
from pymongo import MongoClient

# Ensure backend env is loaded
load_dotenv("/app/backend/.env")

# Frontend .env holds EXPO_PUBLIC_BACKEND_URL — load it too as a fallback.
load_dotenv("/app/frontend/.env")
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing"
API = f"{BASE_URL}/api"

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]

FOUNDER_EMAIL = "carlofarinapayme@gmail.com"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


def _hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _make_jwt(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _upsert_real_user(email: str, nickname: str, password: str) -> dict:
    """Directly upsert a verified email user into Mongo, returning the doc."""
    existing = _db.users.find_one({"email": email.lower()})
    if existing:
        _db.users.update_one(
            {"user_id": existing["user_id"]},
            {"$set": {
                "password_hash": _hash_pw(password),
                "email_verified": True,
                "auth_provider": "email",
                "nickname": nickname,
                "is_bot": False,
            }},
        )
        return _db.users.find_one({"user_id": existing["user_id"]})
    uid = f"user_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": uid,
        "email": email.lower(),
        "nickname": nickname,
        "password_hash": _hash_pw(password),
        "auth_provider": "email",
        "email_verified": True,
        "majority_votes": 0, "minority_votes": 0, "total_votes": 0,
        "is_bot": False,
    }
    _db.users.insert_one(doc)
    return doc


def _login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ensure_vote(headers: dict, feud_id: str, side: str = "A"):
    r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=headers, json={"side": side}, timeout=30)
    assert r.status_code in (200, 400), f"vote failed: {r.status_code} {r.text}"


def _post_comment(headers: dict, feud_id: str, text: str = "TEST_comment") -> str:
    r = requests.post(f"{API}/feuds/{feud_id}/comments", headers=headers, json={"text": text}, timeout=30)
    assert r.status_code == 200, f"post comment failed: {r.status_code} {r.text}"
    return r.json()["comment"]["comment_id"]


def _post_reply(headers: dict, cid: str, text: str = "TEST_reply") -> str:
    r = requests.post(f"{API}/comments/{cid}/replies", headers=headers, json={"text": text}, timeout=30)
    assert r.status_code == 200, f"post reply failed: {r.status_code} {r.text}"
    return r.json()["reply"]["reply_id"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def feud_id() -> str:
    r = requests.get(f"{API}/feuds", timeout=30)
    assert r.status_code == 200
    feuds = r.json().get("feuds", [])
    assert feuds, "No feuds available for testing"
    return feuds[0]["feud_id"]


@pytest.fixture(scope="module")
def user_a() -> dict:
    suffix = uuid.uuid4().hex[:10]
    # NOTE: nickname/email carefully chosen to NOT match testing_agent cleanup regex
    # (`test_a_*`, `test_b_*`, `test_agent_*`, or `@example.com`).
    nick = f"alicem{suffix}"
    email = f"alicem_{suffix}@populus-it.co"
    doc = _upsert_real_user(email, nick, "Passw0rd!")
    token = _login(email, "Passw0rd!")
    return {"user_id": doc["user_id"], "email": email, "nickname": nick,
            "token": token, "headers": _bearer(token)}


@pytest.fixture(scope="module")
def user_b() -> dict:
    suffix = uuid.uuid4().hex[:10]
    nick = f"bobxx{suffix}"
    email = f"bobxx_{suffix}@populus-it.co"
    doc = _upsert_real_user(email, nick, "Passw0rd!")
    token = _login(email, "Passw0rd!")
    return {"user_id": doc["user_id"], "email": email, "nickname": nick,
            "token": token, "headers": _bearer(token)}


@pytest.fixture(scope="module")
def admin_auth() -> dict:
    """Ensure the founder admin exists with a known password and log in."""
    # Do NOT wipe if already present (Google OAuth account should persist);
    # just add/refresh a password so we can generate a JWT locally.
    existing = _db.users.find_one({"email": FOUNDER_EMAIL})
    if existing:
        uid = existing["user_id"]
        # Preserve existing user_id + provider; we bypass /auth/login and mint
        # a JWT directly (matches server.make_jwt signature).
        token = _make_jwt(uid)
        return {"user_id": uid, "email": FOUNDER_EMAIL, "token": token,
                "headers": _bearer(token)}
    # Otherwise, create the user with email provider so JWT works.
    uid = f"user_{uuid.uuid4().hex[:12]}"
    _db.users.insert_one({
        "user_id": uid,
        "email": FOUNDER_EMAIL,
        "nickname": f"founder_{uuid.uuid4().hex[:6]}",
        "password_hash": _hash_pw("Passw0rd!"),
        "auth_provider": "email",
        "email_verified": True,
        "majority_votes": 0, "minority_votes": 0, "total_votes": 0,
        "is_bot": False,
    })
    token = _make_jwt(uid)
    return {"user_id": uid, "email": FOUNDER_EMAIL, "token": token,
            "headers": _bearer(token)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestAdminCanDeleteAnyComment:
    def test_admin_deletes_other_user_comment(self, user_a, admin_auth, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_admin_moderation_1")
        # sanity: exists in DB
        assert _db.comments.find_one({"comment_id": cid}) is not None
        r = requests.delete(f"{API}/comments/{cid}", headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("moderated") is True, f"expected moderated=true, got {body}"
        # DB verify
        assert _db.comments.find_one({"comment_id": cid}) is None

    def test_admin_deletes_other_user_reply(self, user_a, admin_auth, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_admin_reply_parent")
        rid = _post_reply(user_a["headers"], cid, "TEST_admin_moderation_reply")
        assert _db.replies.find_one({"reply_id": rid}) is not None
        r = requests.delete(f"{API}/replies/{rid}", headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("moderated") is True, f"expected moderated=true, got {body}"
        assert _db.replies.find_one({"reply_id": rid}) is None
        # cleanup parent comment
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)

    def test_admin_cascade_deletes_replies(self, user_a, user_b, admin_auth, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        _ensure_vote(user_b["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_cascade_root")
        r1 = _post_reply(user_a["headers"], cid, "TEST_cascade_r1")
        r2 = _post_reply(user_b["headers"], cid, "TEST_cascade_r2")
        # Sanity — both replies in DB
        assert _db.replies.count_documents({"comment_id": cid}) >= 2
        # Admin deletes root
        r = requests.delete(f"{API}/comments/{cid}", headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("moderated") is True
        # Cascade — root gone AND all its replies gone
        assert _db.comments.find_one({"comment_id": cid}) is None
        assert _db.replies.count_documents({"comment_id": cid}) == 0
        # Individual reply lookups
        assert _db.replies.find_one({"reply_id": r1}) is None
        assert _db.replies.find_one({"reply_id": r2}) is None


class TestAuthorSelfDeleteRegression:
    def test_author_deletes_own_comment_moderated_false(self, user_a, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_self_delete_cmt")
        r = requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        # Spec: `moderated` should be false (or absent/falsey) when author deletes own content
        assert not body.get("moderated"), f"expected moderated falsey, got {body}"

    def test_author_deletes_own_reply_moderated_false(self, user_a, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_self_reply_parent")
        rid = _post_reply(user_a["headers"], cid, "TEST_self_reply")
        r = requests.delete(f"{API}/replies/{rid}", headers=user_a["headers"], timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert not body.get("moderated"), f"expected moderated falsey, got {body}"
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)

    def test_admin_deletes_own_comment_moderated_false(self, admin_auth, feud_id):
        _ensure_vote(admin_auth["headers"], feud_id, "A")
        cid = _post_comment(admin_auth["headers"], feud_id, "TEST_admin_self_comment")
        r = requests.delete(f"{API}/comments/{cid}", headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        # Admin deleting THEIR OWN content must NOT flag as moderation
        assert not body.get("moderated"), f"expected moderated falsey when admin deletes own, got {body}"


class TestRegularUserCannotDeleteOthers:
    def test_regular_user_cannot_delete_others_comment(self, user_a, user_b, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_forbidden_cmt")
        r = requests.delete(f"{API}/comments/{cid}", headers=user_b["headers"], timeout=30)
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"
        # comment must still exist
        assert _db.comments.find_one({"comment_id": cid}) is not None
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)

    def test_regular_user_cannot_delete_others_reply(self, user_a, user_b, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_forbidden_reply_parent")
        rid = _post_reply(user_a["headers"], cid, "TEST_forbidden_reply")
        r = requests.delete(f"{API}/replies/{rid}", headers=user_b["headers"], timeout=30)
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"
        # reply must still exist
        assert _db.replies.find_one({"reply_id": rid}) is not None
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)


class TestErrorCases:
    def test_delete_nonexistent_comment_returns_404(self, admin_auth):
        r = requests.delete(f"{API}/comments/cmt_does_not_exist_zzz",
                            headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 404, r.text

    def test_delete_nonexistent_reply_returns_404(self, admin_auth):
        r = requests.delete(f"{API}/replies/rpl_does_not_exist_zzz",
                            headers=admin_auth["headers"], timeout=30)
        assert r.status_code == 404, r.text

    def test_delete_comment_unauthenticated_returns_401(self, user_a, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_unauth_del")
        r = requests.delete(f"{API}/comments/{cid}", timeout=30)
        # get_current_user without a Bearer token → 401 (unauthenticated)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)

    def test_delete_reply_unauthenticated_returns_401(self, user_a, feud_id):
        _ensure_vote(user_a["headers"], feud_id, "A")
        cid = _post_comment(user_a["headers"], feud_id, "TEST_unauth_reply_parent")
        rid = _post_reply(user_a["headers"], cid, "TEST_unauth_reply")
        r = requests.delete(f"{API}/replies/{rid}", timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=user_a["headers"], timeout=30)
