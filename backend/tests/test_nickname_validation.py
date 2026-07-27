"""Tests for Instagram-style nickname validation applied to:
- POST /api/auth/anonymous
- POST /api/auth/signup (indirectly)
- PATCH /api/auth/me/profile
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://populus-gossip.preview.emergentagent.com").rstrip("/")

EXPECTED_MSG = "solo lettere, numeri, punti e underscore"


@pytest.fixture(scope="module")
def chat_a_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "chat_a@test.it", "password": "test123",
    }, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def chat_a_profile_base(chat_a_token):
    """Fetch current profile so PATCH keeps all required fields intact."""
    r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {chat_a_token}"}, timeout=15)
    assert r.status_code == 200, r.text
    me = r.json().get("user", r.json())
    return {
        "age": me.get("age") or 25,
        "sex": me.get("sex") or "na",
        "region": me.get("region") or "Lazio",
        "favorite_categories": me.get("favorite_categories") or ["politica"],
    }


def _me_nickname(token: str) -> str:
    r = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    user = data.get("user", data)
    return user["nickname"]


# --- Anonymous nickname endpoint ---
class TestAnonymousNickname:
    def test_space_rejected(self):
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "test user"}, timeout=15)
        assert r.status_code == 400, r.text
        assert EXPECTED_MSG in r.json().get("detail", ""), r.text

    def test_emoji_rejected(self):
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "👑queen"}, timeout=15)
        assert r.status_code == 400, r.text
        # message content should include the same italian sentence
        assert EXPECTED_MSG in r.json().get("detail", ""), r.text

    def test_hyphen_rejected(self):
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "test-user"}, timeout=15)
        assert r.status_code == 400, r.text
        assert EXPECTED_MSG in r.json().get("detail", ""), r.text

    def test_period_underscore_ok(self):
        nick = f"test.user_{uuid.uuid4().hex[:4]}"
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user"]["nickname"] == nick

    def test_leading_at_stripped(self):
        suffix = uuid.uuid4().hex[:4]
        nick_input = f"@Handle_{suffix}"
        expected = f"Handle_{suffix}"
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick_input}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["user"]["nickname"] == expected


# --- Profile PATCH endpoint ---
class TestProfileNicknamePatch:
    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def test_space_rejected(self, chat_a_token, chat_a_profile_base):
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            json={**chat_a_profile_base, "nickname": "has space"},
            headers=self._auth_headers(chat_a_token),
            timeout=15,
        )
        assert r.status_code == 400, r.text
        assert EXPECTED_MSG in r.json().get("detail", ""), r.text

    def test_same_nickname_ok(self, chat_a_token, chat_a_profile_base):
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            json={**chat_a_profile_base, "nickname": "chat_a"},
            headers=self._auth_headers(chat_a_token),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert _me_nickname(chat_a_token) == "chat_a"

    def test_valid_nickname_updates_and_reset(self, chat_a_token, chat_a_profile_base):
        new_nick = "chat.a_2"
        try:
            r = requests.patch(
                f"{BASE_URL}/api/auth/me/profile",
                json={**chat_a_profile_base, "nickname": new_nick},
                headers=self._auth_headers(chat_a_token),
                timeout=15,
            )
            assert r.status_code == 200, r.text
            assert _me_nickname(chat_a_token) == new_nick
        finally:
            reset = requests.patch(
                f"{BASE_URL}/api/auth/me/profile",
                json={**chat_a_profile_base, "nickname": "chat_a"},
                headers=self._auth_headers(chat_a_token),
                timeout=15,
            )
            assert reset.status_code == 200, reset.text
            assert _me_nickname(chat_a_token) == "chat_a"
