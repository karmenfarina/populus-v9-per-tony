"""Backend tests for PATCH /api/auth/me/profile — identity (nickname, display_name).

Covers the six scenarios from the review request:
  1. Same nickname (self) -> 200
  2. Nickname already taken (case-insensitive) -> 409
  3. display_name set to "Mario Rossi" -> user.display_name == "Mario Rossi"
  4. display_name cleared with "" -> user.display_name is None
  5. nickname length 1 -> 400/422 (validation error)
  6. nickname length 25 -> 400/422 (validation error)
"""
import os

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/") or os.environ.get(
    "EXPO_BACKEND_URL", ""
).rstrip("/")


def _login(email: str, password: str):
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed {email}: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def user_a():
    return _login("chat_a@test.it", "test123")


@pytest.fixture(scope="module")
def user_b():
    return _login("chat_b@test.it", "test123")


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _base_payload(user: dict) -> dict:
    """Build a valid ProfileBody payload preserving existing values."""
    return {
        "age": user.get("age") or 25,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lazio",
        "favorite_categories": user.get("favorite_categories") or ["politica"],
    }


# ---------- Scenario 1 : same nickname keeps returning 200 ----------
class TestSameNickname:
    def test_save_with_own_nickname(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["nickname"] = user_a["user"]["nickname"]  # "chatUserA"
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code} - {r.text}"
        data = r.json()
        assert data["user"]["nickname"] == user_a["user"]["nickname"]


# ---------- Scenario 2 : conflict with another user's nickname ----------
class TestNicknameConflict:
    def test_conflict_exact_case(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["nickname"] = "chatUserB"
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code} - {r.text}"
        assert r.json().get("detail") == "Questo nickname è già in uso"

    def test_conflict_case_insensitive(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["nickname"] = "CHATUSERB"
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code} - {r.text}"
        assert r.json().get("detail") == "Questo nickname è già in uso"


# ---------- Scenario 3 & 4 : display_name set + clear ----------
class TestDisplayName:
    def test_set_display_name(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["display_name"] = "Mario Rossi"
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user"].get("display_name") == "Mario Rossi", body

    def test_clear_display_name(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["display_name"] = ""
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # backend clears to None when empty string sent
        assert body["user"].get("display_name") in (None, ""), body
        assert body["user"].get("display_name") is None, (
            f"display_name should be cleared to None, got: {body['user'].get('display_name')!r}"
        )


# ---------- Scenario 5 & 6 : nickname length validation ----------
class TestNicknameLength:
    def test_nickname_too_short(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["nickname"] = "a"
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        # Pydantic returns 422 by default; the handler also has a manual 400.
        assert r.status_code in (400, 422), f"Expected 400/422, got {r.status_code}: {r.text}"

    def test_nickname_too_long(self, user_a):
        payload = _base_payload(user_a["user"])
        payload["nickname"] = "a" * 25
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(user_a["token"]),
            json=payload,
            timeout=15,
        )
        assert r.status_code in (400, 422), f"Expected 400/422, got {r.status_code}: {r.text}"
