"""Iteration 57 — Tests for `/api/circle/suggestions` and `/api/search/users`
(display_name OR match).

Covers the FIX + FEATURE described in the review request:
- GET /api/circle/suggestions returns hydrated user rows with `reasons`.
- Self / anonymous / already-in-circle / blocked users are excluded.
- DM contacts surface with "chat" reason.
- GET /api/search/users?q= matches nickname OR display_name (case-insensitive)
  and returns display_name in payload; empty q returns {users: []}.
"""
import os
import uuid
import time
import requests
import pytest

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

CHAT_A = {"email": "chat_a@test.it", "password": "test123"}
CHAT_B = {"email": "chat_b@test.it", "password": "test123"}


def _login(sess: requests.Session, creds):
    r = sess.post(f"{API}/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def tokens():
    s = requests.Session()
    ta, ua = _login(s, CHAT_A)
    tb, ub = _login(s, CHAT_B)
    return {"a": (ta, ua), "b": (tb, ub)}


@pytest.fixture(scope="module")
def ensure_dm(tokens):
    """Make sure chat_a and chat_b have exchanged at least one message so
    chat_b shows up under the "chat" reason in suggestions."""
    ta, ua = tokens["a"]
    tb, ub = tokens["b"]
    # A -> B
    r = requests.post(
        f"{API}/messages/send",
        json={"recipient_id": ub["user_id"], "text": f"TEST_ping_{uuid.uuid4().hex[:6]}"},
        headers=_auth(ta),
        timeout=15,
    )
    assert r.status_code in (200, 201), f"send failed: {r.status_code} {r.text}"
    # Ensure chat_b is NOT in chat_a's circle at start of this module.
    requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)
    return True


# ---------------- Backend: /api/circle/suggestions ----------------

class TestCircleSuggestions:
    def test_200_and_shape(self, tokens, ensure_dm):
        ta, _ = tokens["a"]
        r = requests.get(f"{API}/circle/suggestions?limit=15", headers=_auth(ta), timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "users" in body and isinstance(body["users"], list)
        for u in body["users"]:
            assert set(["user_id", "nickname", "display_name", "photo_data", "reasons"]).issubset(u.keys()), u
            assert isinstance(u["reasons"], list)
            for reason in u["reasons"]:
                assert reason in ("chat", "amici_di_amici", "commenti_in_comune"), reason

    def test_self_excluded(self, tokens, ensure_dm):
        ta, ua = tokens["a"]
        r = requests.get(f"{API}/circle/suggestions?limit=40", headers=_auth(ta), timeout=15)
        users = r.json().get("users", [])
        ids = [u["user_id"] for u in users]
        assert ua["user_id"] not in ids, "self must not appear in suggestions"

    def test_dm_contact_surfaces_with_chat_reason(self, tokens, ensure_dm):
        ta, _ = tokens["a"]
        _, ub = tokens["b"]
        r = requests.get(f"{API}/circle/suggestions?limit=40", headers=_auth(ta), timeout=15)
        users = r.json().get("users", [])
        b_row = next((u for u in users if u["user_id"] == ub["user_id"]), None)
        assert b_row is not None, f"chat_b should surface as DM contact. Got: {[u['user_id'] for u in users]}"
        assert "chat" in b_row["reasons"], b_row

    def test_users_in_circle_excluded(self, tokens, ensure_dm):
        ta, _ = tokens["a"]
        _, ub = tokens["b"]
        # Add chat_b to chat_a's circle then assert it disappears.
        add = requests.post(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)
        assert add.status_code in (200, 201), add.text
        try:
            r = requests.get(f"{API}/circle/suggestions?limit=40", headers=_auth(ta), timeout=15)
            ids = [u["user_id"] for u in r.json().get("users", [])]
            assert ub["user_id"] not in ids, "circle member must be excluded from suggestions"
        finally:
            # Cleanup
            requests.delete(f"{API}/circle/{ub['user_id']}", headers=_auth(ta), timeout=10)

    def test_anonymous_returns_empty(self):
        # Fresh anonymous session
        anon_nick = f"anonSug_{uuid.uuid4().hex[:5]}"
        r = requests.post(f"{API}/auth/anonymous", json={"nickname": anon_nick}, timeout=15)
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        s = requests.get(f"{API}/circle/suggestions", headers=_auth(token), timeout=15)
        assert s.status_code == 200
        assert s.json() == {"users": []}


# ---------------- Backend: /api/search/users (display_name OR match) ----------------

class TestSearchUsersDisplayName:
    """Uses pre-seeded chat_b as the display_name target: temporarily set
    a unique display_name on chat_b, search from chat_a, then reset."""
    _display = f"TEST_Mario_Rossi_{uuid.uuid4().hex[:5]}"

    @classmethod
    def _patch_display(cls, token, value):
        # Fetch current profile to preserve required fields.
        me = requests.get(f"{API}/auth/me", headers=_auth(token), timeout=15).json()
        payload = {
            "age": me.get("age") or 30,
            "sex": me.get("sex") or "M",
            "region": me.get("region") or "Lombardia",
            "favorite_categories": me.get("favorite_categories") or ["politica"],
            "display_name": value,
        }
        r = requests.patch(f"{API}/auth/me/profile", json=payload, headers=_auth(token), timeout=15)
        assert r.status_code == 200, f"PATCH profile failed: {r.status_code} {r.text}"

    @classmethod
    def setup_class(cls):
        # Login chat_b and set the display_name.
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json=CHAT_B, timeout=15)
        assert r.status_code == 200
        cls._b_token = r.json()["token"]
        # Save original display_name to restore later.
        me = requests.get(f"{API}/auth/me", headers=_auth(cls._b_token), timeout=15).json()
        cls._original_display = me.get("display_name")
        cls._patch_display(cls._b_token, cls._display)

    @classmethod
    def teardown_class(cls):
        try:
            cls._patch_display(cls._b_token, cls._original_display)
        except Exception:
            pass

    def test_display_name_case_insensitive_match(self, tokens):
        ta, _ = tokens["a"]
        # Search using "mario" (lowercase) — must find via display_name.
        q = "mario_rossi"
        r = requests.get(f"{API}/search/users?q={q}", headers=_auth(ta), timeout=15)
        assert r.status_code == 200, r.text
        users = r.json().get("users", [])
        hit = next((u for u in users if u.get("display_name") == self._display), None)
        assert hit is not None, f"expected user with display_name {self._display} in results (q={q}). Got: {users[:5]}"
        assert "display_name" in hit and "nickname" in hit and "user_id" in hit

    def test_search_partial_second_token(self, tokens):
        ta, _ = tokens["a"]
        # Search "rossi" — must match via display_name only (nickname of chat_b is chatUserB).
        q = "rossi"
        r = requests.get(f"{API}/search/users?q={q}", headers=_auth(ta), timeout=15)
        assert r.status_code == 200
        users = r.json().get("users", [])
        assert any(u.get("display_name") == self._display for u in users), \
            f"expected display_name hit for q={q}. Got: {[u.get('display_name') for u in users]}"

    def test_empty_query_returns_empty(self, tokens):
        ta, _ = tokens["a"]
        r = requests.get(f"{API}/search/users?q=", headers=_auth(ta), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == {"users": []}

    def test_search_users_payload_shape(self, tokens):
        ta, _ = tokens["a"]
        r = requests.get(f"{API}/search/users?q=chat", headers=_auth(ta), timeout=15)
        assert r.status_code == 200
        users = r.json().get("users", [])
        assert len(users) >= 1, "expected at least one match for 'chat'"
        u = users[0]
        for k in ("user_id", "nickname", "display_name", "photo_data"):
            assert k in u, f"missing key {k} in {u}"
