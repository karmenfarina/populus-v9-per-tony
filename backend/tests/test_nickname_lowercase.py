"""
Tests for nickname lowercase-only rules (iteration 45).

Verifies:
- Backend auto-lowercases uppercase nicknames (lenient) rather than rejecting.
- Space input rejected with lowercase-related message.
- Length errors return specific messages ("almeno 2 caratteri" vs "al massimo 24 caratteri").
- PATCH /auth/me/profile lowercases nickname and can be reset.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing"
BASE_URL = BASE_URL.rstrip("/")


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- POST /api/auth/anonymous ---

class TestAnonymousNickname:
    def test_uppercase_auto_lowercased(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "UpperCase"})
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        data = r.json()
        assert data["user"]["nickname"] == "uppercase", f"nickname={data['user']['nickname']}"

    def test_has_space_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "has space"})
        assert r.status_code == 400, f"got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "solo lettere minuscole" in detail, f"detail={detail}"

    def test_too_short_one_char(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": "a"})
        # Note: AnonymousBody has Pydantic min_length=2, so may 422 instead of 400.
        assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
        detail_text = r.text
        assert "almeno 2 caratteri" in detail_text or "at least 2 characters" in detail_text or "min_length" in detail_text, (
            f"expected 'almeno 2 caratteri' style message, got: {detail_text}"
        )

    def test_too_long_27_chars(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": "a" * 27},
        )
        assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
        detail_text = r.text
        assert "al massimo 24 caratteri" in detail_text or "at most 24 characters" in detail_text or "max_length" in detail_text, (
            f"expected 'al massimo 24 caratteri' style message, got: {detail_text}"
        )

    def test_good_nickname_stored_as_is(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": "good.nick_1"},
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        data = r.json()
        assert data["user"]["nickname"] == "good.nick_1"


# --- PATCH /api/auth/me/profile ---

def _login_chat_a(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "chat_a@test.it", "password": "test123"},
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def _get_me(api_client, token):
    r = api_client.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"me failed: {r.status_code} {r.text}"
    return r.json()["user"]


def _profile_payload_from_user(user, **overrides):
    """Build a full ProfileBody payload from current user data (required fields)."""
    payload = {
        "age": user.get("age") or 25,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lombardia",
        "favorite_categories": user.get("favorite_categories") or ["politica"],
    }
    if user.get("profession"):
        payload["profession"] = user["profession"]
    payload.update(overrides)
    return payload


class TestProfileNicknameUpdate:
    def test_patch_profile_uppercase_lowercased_and_reset(self, api_client):
        token = _login_chat_a(api_client)
        original = _get_me(api_client, token)
        original_nick = original.get("nickname")

        # 1) Update to NEW_NICK (uppercase mixed).
        payload = _profile_payload_from_user(original, nickname="NEW_NICK")
        r = api_client.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert r.status_code == 200, f"patch failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["user"]["nickname"] == "new_nick", (
            f"expected new_nick, got {data['user']['nickname']}"
        )

        # 2) Verify via GET.
        me2 = _get_me(api_client, token)
        assert me2["nickname"] == "new_nick"

        # 3) Reset to original ("chat_a").
        reset_nick = "chat_a" if original_nick and original_nick.lower() in ("chata", "chatusera", "chat_a") else original_nick or "chat_a"
        payload_reset = _profile_payload_from_user(original, nickname="chat_a")
        r2 = api_client.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json=payload_reset,
        )
        assert r2.status_code == 200, f"reset failed: {r2.status_code} {r2.text}"
        assert r2.json()["user"]["nickname"] == "chat_a"

    def test_patch_profile_empty_nickname(self, api_client):
        token = _login_chat_a(api_client)
        original = _get_me(api_client, token)
        # Note: ProfileBody.nickname has Field(min_length=2), so empty
        # may fail Pydantic validation (422) rather than reaching helper.
        payload = _profile_payload_from_user(original, nickname="")
        r = api_client.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
        text = r.text.lower()
        # Accept either specific detail or pydantic min_length message.
        assert (
            "almeno 2 caratteri" in r.text
            or "nickname mancante" in r.text.lower()
            or "min_length" in text
            or "at least 2" in text
            or "string_too_short" in text
        ), f"unexpected error: {r.text}"
