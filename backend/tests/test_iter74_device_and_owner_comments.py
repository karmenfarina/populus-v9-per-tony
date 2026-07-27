"""Iteration 74 backend tests.

Covers the three server-side changes introduced this iteration:
  1. `POST /api/auth/anonymous` — new device-scoped anon identity via
     optional `device_id` in the body.
  2. `GET /api/feuds/{feud_id}/comments?owner_user_id=X` — owner's
     comments float into a new topmost bucket (-1). Works for
     authenticated AND anonymous viewers.
  3. `POST /api/admin/generate-daily` — smoke test that the endpoint
     still returns a well-formed JSON body after the recent-feuds
     prompt-context change (the AI dedup decision itself is
     judgement-based and therefore not asserted).
"""
import os
import time
import random
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://populus-gossip.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_KEY = "populus-admin-42b8f3"

_MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
_DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = MongoClient(_MONGO_URL)
_db = _mongo[_DB_NAME]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _rand_nick(prefix: str) -> str:
    ts = int(time.time() * 1000)
    # Nicknames are lowercased server-side, so keep the prefix lowercase
    # to make direct equality assertions painless.
    return f"{prefix.lower()}{ts % 1000000}{random.randint(100, 999)}"


def _signup(prefix: str = "iter74") -> dict:
    """Create a fully-onboarded email/password user, bypassing verification."""
    ts = int(time.time() * 1000)
    salt = random.randint(1000, 9999)
    email = f"test_{prefix}_{ts}_{salt}@test.dev"
    password = "Testing123!"
    nickname = _rand_nick(prefix)
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "nickname": nickname,
    })
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    _db.users.update_one({"email": email}, {"$set": {"email_verified": True}})
    rl = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert rl.status_code == 200, rl.text
    body = rl.json()
    tok = body["token"]
    uid = body["user"]["user_id"]
    r2 = requests.patch(
        f"{API}/auth/me/profile",
        json={"age": 27, "sex": "M", "region": "Lombardia",
              "favorite_categories": ["politica", "musica"]},
        headers=_hdr(tok),
    )
    assert r2.status_code == 200, r2.text
    return {"token": tok, "user_id": uid, "email": email, "nickname": nickname}


def _pick_feud() -> str:
    r = requests.get(f"{API}/feuds")
    assert r.status_code == 200
    feuds = r.json().get("feuds", [])
    assert feuds, "need at least one feud in DB for comment-order tests"
    return feuds[0]["feud_id"]


def _vote_and_comment(user: dict, feud_id: str, text: str, side: str = "A") -> str:
    rv = requests.post(f"{API}/feuds/{feud_id}/vote",
                       json={"side": side}, headers=_hdr(user["token"]))
    # 400 is acceptable if the user has already voted this side (rerun / setup).
    assert rv.status_code in (200, 400), rv.text
    rc = requests.post(f"{API}/feuds/{feud_id}/comments",
                       json={"side": side, "text": text},
                       headers=_hdr(user["token"]))
    assert rc.status_code == 200, rc.text
    return rc.json().get("comment_id") or rc.json().get("id") or ""


# ===========================================================================
# 1. /api/auth/anonymous — device-scoped identity
# ===========================================================================
class TestAnonymousDeviceScoped:
    _ids: set = set()

    @classmethod
    def teardown_class(cls):
        ids = cls._ids
        if ids:
            try:
                _db.users.delete_many({"user_id": {"$in": list(ids)}})
            except Exception:
                pass

    def test_a_same_device_same_nickname_returns_same_user(self):
        device = f"TEST-dev-{uuid.uuid4()}"
        nick = _rand_nick("anonA")
        r1 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": device})
        assert r1.status_code == 200, r1.text
        j1 = r1.json()
        uid1 = j1["user"]["user_id"]
        TestAnonymousDeviceScoped._ids.add(uid1)

        r2 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": device})
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        uid2 = j2["user"]["user_id"]
        assert uid1 == uid2, f"expected same user_id, got {uid1} vs {uid2}"
        # Both calls must return a JWT (fresh on 2nd call).
        assert j1["token"] and j2["token"]
        assert j2["user"].get("nickname") == nick

    def test_b_same_device_different_nickname_updates_nickname(self):
        device = f"TEST-dev-{uuid.uuid4()}"
        nick1 = _rand_nick("anonB1")
        nick2 = _rand_nick("anonB2")
        r1 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick1, "device_id": device})
        assert r1.status_code == 200, r1.text
        uid1 = r1.json()["user"]["user_id"]
        TestAnonymousDeviceScoped._ids.add(uid1)

        r2 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick2, "device_id": device})
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        uid2 = j2["user"]["user_id"]
        assert uid1 == uid2, "device_id resume must preserve user_id"
        assert j2["user"]["nickname"] == nick2, (
            f"nickname was NOT updated on resume: {j2['user']['nickname']!r} != {nick2!r}"
        )

        # DB should also reflect the new nickname.
        db_doc = _db.users.find_one({"user_id": uid1}, {"nickname": 1, "device_id": 1, "_id": 0})
        assert db_doc and db_doc.get("nickname") == nick2
        assert db_doc.get("device_id") == device

    def test_c_different_device_ids_return_different_users(self):
        dev1 = f"TEST-dev-{uuid.uuid4()}"
        dev2 = f"TEST-dev-{uuid.uuid4()}"
        nick = _rand_nick("anonC")
        r1 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": dev1})
        r2 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": dev2})
        assert r1.status_code == 200 and r2.status_code == 200
        uid1 = r1.json()["user"]["user_id"]
        uid2 = r2.json()["user"]["user_id"]
        TestAnonymousDeviceScoped._ids.update({uid1, uid2})
        assert uid1 != uid2, "distinct device_ids must yield distinct user_ids"

    def test_d_no_device_id_returns_fresh_user_every_time(self):
        nick = _rand_nick("anonD")
        r1 = requests.post(f"{API}/auth/anonymous", json={"nickname": nick})
        r2 = requests.post(f"{API}/auth/anonymous", json={"nickname": nick})
        assert r1.status_code == 200 and r2.status_code == 200
        uid1 = r1.json()["user"]["user_id"]
        uid2 = r2.json()["user"]["user_id"]
        TestAnonymousDeviceScoped._ids.update({uid1, uid2})
        assert uid1 != uid2, "legacy no-device_id path must always create a new user"
        # Sanity: neither should have device_id set in DB.
        d1 = _db.users.find_one({"user_id": uid1}, {"_id": 0, "device_id": 1})
        d2 = _db.users.find_one({"user_id": uid2}, {"_id": 0, "device_id": 1})
        assert not (d1 or {}).get("device_id")
        assert not (d2 or {}).get("device_id")

    def test_e_empty_device_id_treated_as_legacy(self):
        """A blank/whitespace device_id must not resurrect any user."""
        nick = _rand_nick("anonE")
        r1 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": "   "})
        r2 = requests.post(f"{API}/auth/anonymous",
                           json={"nickname": nick, "device_id": ""})
        assert r1.status_code == 200 and r2.status_code == 200
        uid1 = r1.json()["user"]["user_id"]
        uid2 = r2.json()["user"]["user_id"]
        TestAnonymousDeviceScoped._ids.update({uid1, uid2})
        assert uid1 != uid2


# ===========================================================================
# 2. /api/feuds/{feud_id}/comments?owner_user_id=X ordering
# ===========================================================================
class TestOwnerScopedCommentOrdering:

    @pytest.fixture(scope="class")
    def viewer(self):
        return _signup("ownview")

    @pytest.fixture(scope="class")
    def owner(self):
        return _signup("ownown")

    @pytest.fixture(scope="class")
    def other1(self):
        return _signup("othr1")

    @pytest.fixture(scope="class")
    def other2(self):
        return _signup("othr2")

    @pytest.fixture(scope="class")
    def feud_with_comments(self, viewer, owner, other1, other2):
        fid = _pick_feud()
        # Post comments in an order where the owner is NOT the most recent —
        # this way, if bucketing works, owner still floats to top.
        _vote_and_comment(owner, fid, f"TEST_owner_early_{uuid.uuid4().hex[:6]}", "A")
        _vote_and_comment(other1, fid, f"TEST_other1_{uuid.uuid4().hex[:6]}", "A")
        _vote_and_comment(other2, fid, f"TEST_other2_{uuid.uuid4().hex[:6]}", "A")
        _vote_and_comment(viewer, fid, f"TEST_viewer_{uuid.uuid4().hex[:6]}", "A")
        # Owner also posts on side B so we can verify per-side ordering.
        _vote_and_comment(owner, fid, f"TEST_owner_sideB_{uuid.uuid4().hex[:6]}", "A")
        return fid

    @classmethod
    def teardown_class(cls):
        # Best-effort cleanup — nuke comments containing our TEST_ marker.
        try:
            _db.comments.delete_many({"text": {"$regex": "^TEST_"}})
        except Exception:
            pass

    # ----- (a) owner's comments float on side_a when owner_user_id given -----
    def test_a_owner_first_authenticated_viewer(self, viewer, owner, feud_with_comments):
        fid = feud_with_comments
        r = requests.get(
            f"{API}/feuds/{fid}/comments",
            params={"owner_user_id": owner["user_id"]},
            headers=_hdr(viewer["token"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "side_a" in body and "side_b" in body
        side_a = body["side_a"]
        assert side_a, "expected some side_a comments"

        # All owner comments in side_a must appear before any non-owner
        # comment (except the viewer's own comments which share bucket 0
        # → still, owner is bucket -1 so it should come first).
        owner_positions = [i for i, c in enumerate(side_a)
                           if c["user_id"] == owner["user_id"]]
        non_owner_positions = [i for i, c in enumerate(side_a)
                               if c["user_id"] != owner["user_id"]]
        assert owner_positions, "expected owner comment(s) in side_a"
        assert non_owner_positions, "expected non-owner comment(s) in side_a"
        assert max(owner_positions) < min(non_owner_positions), (
            f"owner comments not floated to top: owner_pos={owner_positions} "
            f"non_owner_pos={non_owner_positions}"
        )

    # ----- (b) without owner_user_id → legacy bucketing (regression) --------
    def test_b_without_owner_user_id_regression(self, viewer, owner, feud_with_comments):
        fid = feud_with_comments
        r = requests.get(
            f"{API}/feuds/{fid}/comments",
            headers=_hdr(viewer["token"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        side_a = body["side_a"]
        assert side_a, "expected side_a comments"
        # With no owner_user_id + no circle/DM relationship, owner is in
        # bucket 2 with all "others". The viewer's OWN comment is bucket 0,
        # so it must appear BEFORE the owner's now. This confirms the
        # "no owner_user_id" path skips the -1 promotion.
        viewer_positions = [i for i, c in enumerate(side_a)
                            if c["user_id"] == viewer["user_id"]]
        owner_positions = [i for i, c in enumerate(side_a)
                           if c["user_id"] == owner["user_id"]]
        assert viewer_positions, "viewer's own comment missing"
        assert owner_positions, "owner's comments missing"
        assert min(viewer_positions) < min(owner_positions), (
            "regression: viewer's own comment should outrank owner without owner_user_id"
        )

    # ----- (c) owner_user_id == viewer → no crash, viewer surfaces ---------
    def test_c_owner_is_viewer_no_bug(self, viewer, feud_with_comments):
        fid = feud_with_comments
        r = requests.get(
            f"{API}/feuds/{fid}/comments",
            params={"owner_user_id": viewer["user_id"]},
            headers=_hdr(viewer["token"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        side_a = body["side_a"]
        # Viewer's own comment must still be present.
        assert any(c["user_id"] == viewer["user_id"] for c in side_a), (
            "viewer's own comment vanished when owner_user_id == viewer"
        )
        # Sanity: response has no _id leakage.
        for c in side_a:
            assert "_id" not in c

    # ----- (d) works when the viewer is anonymous --------------------------
    def test_d_anonymous_viewer_owner_first(self, owner, feud_with_comments):
        fid = feud_with_comments
        r = requests.get(
            f"{API}/feuds/{fid}/comments",
            params={"owner_user_id": owner["user_id"]},
            # NO auth header — anonymous viewer.
        )
        assert r.status_code == 200, r.text
        body = r.json()
        side_a = body["side_a"]
        assert side_a
        owner_positions = [i for i, c in enumerate(side_a)
                           if c["user_id"] == owner["user_id"]]
        non_owner_positions = [i for i, c in enumerate(side_a)
                               if c["user_id"] != owner["user_id"]]
        assert owner_positions and non_owner_positions
        assert max(owner_positions) < min(non_owner_positions), (
            f"anon viewer: owner not floated to top. "
            f"owner={owner_positions} others={non_owner_positions}"
        )


# ===========================================================================
# 3. /api/admin/generate-daily — smoke test (no crash after prompt change)
# ===========================================================================
class TestGenerateDailySmoke:

    def test_generate_daily_returns_valid_json_no_crash(self):
        # Use count=1 to keep the test cheap (single LLM call).
        r = requests.post(
            f"{API}/admin/generate-daily",
            params={"count": 1},
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=180,  # generation + fact-check can be slow.
        )
        # We accept 200 only. 500 would indicate a crash from the
        # recent-feuds prompt-context change.
        assert r.status_code == 200, (
            f"generate-daily crashed: {r.status_code} {r.text[:400]}"
        )
        body = r.json()
        assert "created" in body, f"missing 'created' key: {body}"
        assert isinstance(body["created"], list), (
            f"'created' must be a list, got {type(body['created'])}"
        )
        # Each returned feud (if any) must be a dict with a feud_id.
        for f in body["created"]:
            assert isinstance(f, dict) and f.get("feud_id"), f
            # Ensure no ObjectId leak.
            assert "_id" not in f
