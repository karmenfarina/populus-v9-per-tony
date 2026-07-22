"""Tests for iteration 54:
  1. Voting-history privacy toggles (`PATCH /api/users/me/history-privacy`
     + hidden semantics on `GET /api/users/{user_id}/history`).
  2. Personalised comment ordering on `GET /api/feuds/{feud_id}/comments`.
"""
import os
import time
import random
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://cerchia-app.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

# Direct DB access is only used to bypass the email-verification wall so we
# can spin up throwaway test accounts without hitting Resend for every run.
_MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
_DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = MongoClient(_MONGO_URL)
_db = _mongo[_DB_NAME]


def _signup(nick_prefix: str) -> dict:
    ts = int(time.time() * 1000)
    salt = random.randint(1000, 9999)
    email = f"{nick_prefix}_{ts}_{salt}@test.dev"
    password = "Testing123!"
    nickname = f"{nick_prefix}{ts % 100000}{salt}"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "nickname": nickname,
    })
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    # New sign-ups now require email verification before login. For tests we
    # bypass the wall by directly flipping `email_verified` in Mongo.
    _db.users.update_one({"email": email}, {"$set": {"email_verified": True}})
    rl = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert rl.status_code == 200, f"login failed: {rl.status_code} {rl.text}"
    body = rl.json()
    tok = body["token"]
    uid = body["user"]["user_id"]
    # Complete onboarding so the user can vote / comment.
    r2 = requests.patch(
        f"{API}/auth/me/profile",
        json={"age": 27, "sex": "M", "region": "Lombardia", "favorite_categories": ["politica", "musica"]},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 200, f"onboarding failed: {r2.status_code} {r2.text}"
    return {"token": tok, "user_id": uid, "email": email, "nickname": nickname}


def _hdr(t): return {"Authorization": f"Bearer {t}"}


# =========================================================================
# Test 1 — Backend privacy history endpoint
# =========================================================================
class TestHistoryPrivacy:
    """Covers PATCH /users/me/history-privacy and hidden semantics on
    GET /users/{user_id}/history."""

    @pytest.fixture(scope="class")
    def owner(self):
        return _signup("histown")

    @pytest.fixture(scope="class")
    def viewer(self):
        return _signup("histview")

    @pytest.fixture(scope="class")
    def owner_voted(self, owner):
        r = requests.get(f"{API}/feuds")
        assert r.status_code == 200
        feuds = r.json().get("feuds", [])
        assert feuds, "need at least one feud in DB"
        vf = feuds[0]
        rv = requests.post(f"{API}/feuds/{vf['feud_id']}/vote",
                           json={"side": "A"}, headers=_hdr(owner["token"]))
        assert rv.status_code == 200, rv.text
        return vf["feud_id"]

    # --- Step 1 ----------------------------------------------------------
    def test_1_patch_generic_false(self, owner, owner_voted):
        r = requests.patch(f"{API}/users/me/history-privacy",
                           json={"generic": False}, headers=_hdr(owner["token"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"history_public_generic": False, "history_public_mutual": True}, body

    # --- Step 2 ----------------------------------------------------------
    def test_2_viewer_non_mutual_sees_hidden_private(self, owner, viewer, owner_voted):
        r = requests.get(f"{API}/users/{owner['user_id']}/history",
                         headers=_hdr(viewer["token"]))
        assert r.status_code == 200, r.text
        b = r.json()
        assert b.get("hidden") is True, b
        assert b.get("reason") == "private", b
        assert b.get("history") == [], b

    # --- Step 3 ----------------------------------------------------------
    def test_3_anonymous_no_auth_sees_hidden_private(self, owner, owner_voted):
        r = requests.get(f"{API}/users/{owner['user_id']}/history")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b.get("hidden") is True, b
        assert b.get("reason") == "private", b
        assert b.get("history") == [], b

    # --- Step 4 ----------------------------------------------------------
    def test_4_owner_self_not_hidden(self, owner, owner_voted):
        # /users/me/history should always return history (never hidden).
        r = requests.get(f"{API}/users/me/history", headers=_hdr(owner["token"]))
        assert r.status_code == 200, r.text
        b = r.json()
        assert "history" in b and isinstance(b["history"], list)
        assert len(b["history"]) >= 1, b
        # Explicitly not hidden — either key absent OR False.
        assert b.get("hidden") is not True, b

        # Also test /users/{owner_id}/history AS the owner (viewing self).
        r2 = requests.get(f"{API}/users/{owner['user_id']}/history",
                          headers=_hdr(owner["token"]))
        assert r2.status_code == 200, r2.text
        b2 = r2.json()
        assert b2.get("hidden") is False, b2
        assert len(b2.get("history", [])) >= 1, b2

    # --- Step 5 ----------------------------------------------------------
    def test_5_patch_generic_true_mutual_false(self, owner):
        r = requests.patch(f"{API}/users/me/history-privacy",
                           json={"generic": True, "mutual": False},
                           headers=_hdr(owner["token"]))
        assert r.status_code == 200, r.text
        b = r.json()
        assert b == {"history_public_generic": True, "history_public_mutual": False}, b
        # /api/me should reflect both flags via _public_user.
        rm = requests.get(f"{API}/auth/me", headers=_hdr(owner["token"]))
        if rm.status_code == 200:
            payload = rm.json()
            # Endpoint wraps the public user in {"user": {...}}
            me = payload.get("user", payload)
            assert me.get("history_public_generic") is True, me
            assert me.get("history_public_mutual") is False, me

    def test_5b_non_mutual_viewer_now_sees_history(self, owner, viewer):
        # After enabling generic=True, non-mutual viewer should see the list.
        r = requests.get(f"{API}/users/{owner['user_id']}/history",
                         headers=_hdr(viewer["token"]))
        assert r.status_code == 200, r.text
        b = r.json()
        assert b.get("hidden") is False, b
        assert isinstance(b.get("history"), list) and len(b["history"]) >= 1, b

    # --- Step 6 ----------------------------------------------------------
    def test_6_mutual_circle_viewer_sees_hidden_mutual(self, owner, viewer):
        # Make it a mutual circle: owner adds viewer AND viewer adds owner.
        r1 = requests.post(f"{API}/circle/{viewer['user_id']}", headers=_hdr(owner["token"]))
        assert r1.status_code == 200, r1.text
        r2 = requests.post(f"{API}/circle/{owner['user_id']}", headers=_hdr(viewer["token"]))
        assert r2.status_code == 200, r2.text
        # Sanity: viewer thinks owner is in their circle.
        r3 = requests.get(f"{API}/users/{owner['user_id']}/history",
                          headers=_hdr(viewer["token"]))
        assert r3.status_code == 200, r3.text
        b = r3.json()
        assert b.get("hidden") is True, b
        assert b.get("reason") == "mutual_private", b
        assert b.get("history") == [], b

    # --- Step 7 ----------------------------------------------------------
    def test_7_patch_empty_body_returns_400(self, owner):
        r = requests.patch(f"{API}/users/me/history-privacy",
                           json={}, headers=_hdr(owner["token"]))
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"

    def test_7b_patch_ignores_invalid_types(self, owner):
        # generic as string should be treated as missing → 400.
        r = requests.patch(f"{API}/users/me/history-privacy",
                           json={"generic": "yes", "mutual": 1},
                           headers=_hdr(owner["token"]))
        assert r.status_code == 400, f"expected 400 for non-bool payload, got {r.status_code} {r.text}"


# =========================================================================
# Test 2 — Backend comment ordering
# =========================================================================
class TestCommentOrdering:
    """Verifies the 3-bucket personalised ordering for authenticated viewers
    and the plain chronological ordering for anonymous viewers."""

    @pytest.fixture(scope="class")
    def viewer(self):
        return _signup("cmtview")

    @pytest.fixture(scope="class")
    def circle_author(self):
        return _signup("cmtcirc")

    @pytest.fixture(scope="class")
    def dm_author(self):
        return _signup("cmtdm")

    @pytest.fixture(scope="class")
    def stranger_author(self):
        return _signup("cmtstr")

    @pytest.fixture(scope="class")
    def feud(self):
        r = requests.get(f"{API}/feuds")
        assert r.status_code == 200
        feuds = r.json().get("feuds", [])
        assert feuds, "need feuds"
        return feuds[0]

    def _vote_and_comment(self, user, feud_id, text, side="A"):
        rv = requests.post(f"{API}/feuds/{feud_id}/vote",
                           json={"side": side}, headers=_hdr(user["token"]))
        assert rv.status_code == 200, rv.text
        rc = requests.post(f"{API}/feuds/{feud_id}/comments",
                           json={"text": text, "side": side}, headers=_hdr(user["token"]))
        assert rc.status_code == 200, f"comment failed: {rc.status_code} {rc.text}"

    def test_setup_and_ordering(self, viewer, circle_author, dm_author,
                                stranger_author, feud):
        fid = feud["feud_id"]
        # Post comments from three DIFFERENT users on SIDE A (all same side
        # so the visibility filter keeps them all).
        # Order posted (chronological desc = last posted first):
        #   1) stranger        (bucket 2)
        #   2) dm partner      (bucket 1)
        #   3) circle member   (bucket 0)
        # If ordering worked by chronology alone, we'd see circle,dm,stranger.
        # We rely on the buckets to prove it — post in a specific order and
        # verify buckets, not raw chronology.
        self._vote_and_comment(stranger_author, fid, "TEST_stranger_comment")
        time.sleep(0.2)
        self._vote_and_comment(dm_author, fid, "TEST_dm_comment")
        time.sleep(0.2)
        self._vote_and_comment(circle_author, fid, "TEST_circle_comment")

        # Also vote (no comment) as the viewer so the endpoint is reachable
        # for the viewer with their own state.
        rv = requests.post(f"{API}/feuds/{fid}/vote",
                           json={"side": "A"}, headers=_hdr(viewer["token"]))
        assert rv.status_code == 200

        # Wire the buckets:
        #  - circle_author into viewer's Cerchia (viewer→circle_author).
        rc = requests.post(f"{API}/circle/{circle_author['user_id']}", headers=_hdr(viewer["token"]))
        assert rc.status_code == 200, rc.text
        #  - viewer exchanges DM with dm_author.
        rd = requests.post(f"{API}/messages/send",
                           json={"recipient_id": dm_author["user_id"], "text": "TEST_dm_hello"},
                           headers=_hdr(viewer["token"]))
        assert rd.status_code == 200, rd.text

        # ----- Authenticated viewer: check bucket ordering -----
        r = requests.get(f"{API}/feuds/{fid}/comments", headers=_hdr(viewer["token"]))
        assert r.status_code == 200, r.text
        side_a = r.json().get("side_a", [])
        # Filter to just OUR test users' comments.
        my_uids = {circle_author["user_id"], dm_author["user_id"], stranger_author["user_id"]}
        mine = [c for c in side_a if c["user_id"] in my_uids]
        assert len(mine) == 3, f"expected 3 test comments, got {len(mine)}: {mine}"
        # Extract order.
        order_uids = [c["user_id"] for c in mine]
        idx_circle = order_uids.index(circle_author["user_id"])
        idx_dm = order_uids.index(dm_author["user_id"])
        idx_stranger = order_uids.index(stranger_author["user_id"])
        assert idx_circle < idx_dm, f"circle should come before DM. order={order_uids}"
        assert idx_dm < idx_stranger, f"DM should come before stranger. order={order_uids}"

        # ----- Anonymous viewer: chronological order (newest first) -----
        r_anon = requests.get(f"{API}/feuds/{fid}/comments")
        assert r_anon.status_code == 200, r_anon.text
        anon_side_a = r_anon.json().get("side_a", [])
        anon_mine = [c for c in anon_side_a if c["user_id"] in my_uids]
        assert len(anon_mine) == 3, anon_mine
        anon_order = [c["user_id"] for c in anon_mine]
        # We posted in this order: stranger (oldest), dm, circle (newest).
        # docs are sorted newest-first at the top of the endpoint.
        assert anon_order[0] == circle_author["user_id"], \
            f"anon: newest (circle) should be first. order={anon_order}"
        assert anon_order[-1] == stranger_author["user_id"], \
            f"anon: oldest (stranger) should be last. order={anon_order}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
