"""Backend spot-check for /api/users/{owner_id}/circle:
- new fields is_me, in_my_circle are booleans
- viewer (me) placed first when they are in the target's circle
- mutual (in_my_circle=True) placed before non-mutual others
"""
import os
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://voti-scroll-fix.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

_mongo = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
_db = _mongo[os.environ.get("DB_NAME", "test_database")]


def _signup(nick: str):
    # Email signup + force email_verified in DB (avoids the mail-verify flow).
    email = f"test_circle_{uuid.uuid4().hex[:8]}@test.it"
    password = "test1234"
    r = requests.post(f"{API}/auth/signup", json={"email": email, "password": password, "nickname": nick}, timeout=30)
    assert r.status_code in (200, 201), r.text
    # Force verify then login to get a real token.
    _db.users.update_one({"email": email}, {"$set": {"email_verified": True}})
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def actors():
    # A = viewer, B = circle owner, C = another user (in B's circle only), D = mutual with viewer
    a_tok, a = _signup(f"tstA{uuid.uuid4().hex[:5]}")
    b_tok, b = _signup(f"tstB{uuid.uuid4().hex[:5]}")
    c_tok, c = _signup(f"tstC{uuid.uuid4().hex[:5]}")
    d_tok, d = _signup(f"tstD{uuid.uuid4().hex[:5]}")

    # B adds: A (viewer), C, D
    for uid in (a["user_id"], c["user_id"], d["user_id"]):
        r = requests.post(f"{API}/circle/{uid}", headers=_auth(b_tok), timeout=20)
        assert r.status_code in (200, 201), f"B->add {uid}: {r.status_code} {r.text}"

    # A adds D (so D is mutual from A's viewpoint)
    r = requests.post(f"{API}/circle/{d['user_id']}", headers=_auth(a_tok), timeout=20)
    assert r.status_code in (200, 201), r.text

    return {"a": (a_tok, a), "b": (b_tok, b), "c": (c_tok, c), "d": (d_tok, d)}


class TestCircleOrdering:
    def test_fields_and_order(self, actors):
        a_tok, a = actors["a"]
        _, b = actors["b"]
        _, c = actors["c"]
        _, d = actors["d"]
        r = requests.get(f"{API}/users/{b['user_id']}/circle", headers=_auth(a_tok), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        members = body.get("members", [])
        ids = [m["user_id"] for m in members]
        assert set(ids) == {a["user_id"], c["user_id"], d["user_id"]}, f"unexpected members: {ids}"

        # Fields present and booleans
        for m in members:
            assert isinstance(m.get("is_me"), bool), m
            assert isinstance(m.get("in_my_circle"), bool), m

        # Order: viewer (A) first
        assert members[0]["user_id"] == a["user_id"], f"viewer not first: {ids}"
        assert members[0]["is_me"] is True

        # Then D (mutual, in_my_circle=True) before C
        by_id = {m["user_id"]: i for i, m in enumerate(members)}
        assert by_id[d["user_id"]] < by_id[c["user_id"]], f"mutual not before non-mutual: {ids}"

        # in_my_circle flags
        m_by_id = {m["user_id"]: m for m in members}
        assert m_by_id[d["user_id"]]["in_my_circle"] is True
        assert m_by_id[c["user_id"]]["in_my_circle"] is False
        # A's own row: is_me True; in_my_circle depends but should be a bool (already checked)

    def test_owner_view_shows_is_owner(self, actors):
        b_tok, b = actors["b"]
        r = requests.get(f"{API}/users/{b['user_id']}/circle", headers=_auth(b_tok), timeout=20)
        assert r.status_code == 200
        body = r.json()
        assert body.get("is_owner") is True
        for m in body.get("members", []):
            assert m.get("is_me") is False  # owner is not in own circle
            assert isinstance(m.get("in_my_circle"), bool)
