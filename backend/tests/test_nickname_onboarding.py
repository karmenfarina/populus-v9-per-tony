"""Backend tests for the new PATCH /api/auth/me/profile nickname field.

Feature: Google (external) users can pick a mandatory nickname during onboarding.
Existing accounts can also PATCH nickname; the '@' prefix is stripped server-side.
Endpoint rejects anonymous accounts (403); Pydantic enforces 2-24 chars.
"""
import uuid
import pytest
import requests

BASE_URL = "https://vote-ui-polish.preview.emergentagent.com"

# Pre-verified email users from /app/memory/test_credentials.md
USER_A = {"email": "chat_a@test.it", "password": "test123"}


# ---------- helpers ----------
def _login(email: str, password: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _me(token: str) -> dict:
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(token), timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def _signup_fresh_user() -> dict:
    """Create a brand new email-verified test user via signup + admin-verify.

    We can't verify email programmatically, but existing chat_a is verified —
    for cases where we don't want to touch chat_a we fall back to a fresh
    anonymous user for the 401/422 shape tests (which don't need registered).
    """
    nick = f"TEST{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def registered_token():
    """A real registered (non-anonymous) user token for nickname PATCH tests."""
    data = _login(**USER_A)
    yield data["token"]


@pytest.fixture(scope="module")
def original_nickname(registered_token):
    """Remember the original nickname so tests can restore it afterwards."""
    user = _me(registered_token)
    orig = user["nickname"]
    yield orig
    # Restore original nickname at the end of the module
    body = {
        "age": user.get("age") or 30,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lombardia",
        "favorite_categories": user.get("favorite_categories") or ["tech"],
        "nickname": orig,
    }
    if user.get("profession"):
        body["profession"] = user["profession"]
    requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body,
        headers=_headers(registered_token),
        timeout=15,
    )


# ---------- Feature 1: valid nickname updates and returns updated user ----------
def test_patch_profile_valid_nickname_updates(registered_token, original_nickname):
    user_before = _me(registered_token)
    new_nick = f"nick_{uuid.uuid4().hex[:6]}"
    body = {
        "age": user_before.get("age") or 30,
        "sex": user_before.get("sex") or "M",
        "region": user_before.get("region") or "Lombardia",
        "favorite_categories": user_before.get("favorite_categories") or ["tech"],
        "nickname": new_nick,
    }
    if user_before.get("profession"):
        body["profession"] = user_before["profession"]
    r = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body,
        headers=_headers(registered_token),
        timeout=15,
    )
    assert r.status_code == 200, r.text
    updated = r.json()["user"]
    assert updated["nickname"] == new_nick

    # Persistence check via GET /me
    u2 = _me(registered_token)
    assert u2["nickname"] == new_nick


# ---------- Feature 2: nickname too short (1 char) => 422 from Pydantic ----------
def test_patch_profile_nickname_one_char_returns_422(registered_token):
    user = _me(registered_token)
    body = {
        "age": user.get("age") or 30,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lombardia",
        "favorite_categories": user.get("favorite_categories") or ["tech"],
        "nickname": "a",
    }
    r = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body,
        headers=_headers(registered_token),
        timeout=15,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


# ---------- Feature 3: leading '@' is stripped ----------
def test_patch_profile_nickname_strips_leading_at(registered_token):
    user = _me(registered_token)
    raw_nick = f"@my_{uuid.uuid4().hex[:5]}"
    expected = raw_nick.lstrip("@")
    body = {
        "age": user.get("age") or 30,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lombardia",
        "favorite_categories": user.get("favorite_categories") or ["tech"],
        "nickname": raw_nick,
    }
    r = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body,
        headers=_headers(registered_token),
        timeout=15,
    )
    assert r.status_code == 200, r.text
    updated = r.json()["user"]
    assert updated["nickname"] == expected, (
        f"expected stripped nickname '{expected}', got '{updated['nickname']}'"
    )
    # Verify no leading @ persisted in DB
    u2 = _me(registered_token)
    assert not u2["nickname"].startswith("@")
    assert u2["nickname"] == expected


# ---------- Feature 4: PATCH without nickname does NOT modify existing nickname ----------
def test_patch_profile_without_nickname_preserves_existing(registered_token):
    # First, set a known nickname
    baseline_nick = f"keep_{uuid.uuid4().hex[:6]}"
    user = _me(registered_token)
    body_set = {
        "age": user.get("age") or 30,
        "sex": user.get("sex") or "M",
        "region": user.get("region") or "Lombardia",
        "favorite_categories": user.get("favorite_categories") or ["tech"],
        "nickname": baseline_nick,
    }
    r0 = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body_set,
        headers=_headers(registered_token),
        timeout=15,
    )
    assert r0.status_code == 200, r0.text
    assert r0.json()["user"]["nickname"] == baseline_nick

    # Now PATCH again WITHOUT nickname key — nickname should be untouched
    body_no_nick = {
        "age": 31,
        "sex": user.get("sex") or "M",
        "region": "Lazio",
        "favorite_categories": ["politica"],
    }
    r1 = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body_no_nick,
        headers=_headers(registered_token),
        timeout=15,
    )
    assert r1.status_code == 200, r1.text
    updated = r1.json()["user"]
    # Other fields updated correctly
    assert updated["age"] == 31
    assert updated["region"] == "Lazio"
    assert updated["favorite_categories"] == ["politica"]
    # But nickname unchanged
    assert updated["nickname"] == baseline_nick, (
        f"nickname changed unexpectedly: expected '{baseline_nick}', "
        f"got '{updated['nickname']}'"
    )


# ---------- Anonymous cannot PATCH profile (regression guard) ----------
def test_patch_profile_with_nickname_rejects_anonymous():
    anon = _signup_fresh_user()
    body = {
        "age": 20,
        "sex": "M",
        "region": "Lombardia",
        "favorite_categories": ["tech"],
        "nickname": "shouldNotWork",
    }
    r = requests.patch(
        f"{BASE_URL}/api/auth/me/profile",
        json=body,
        headers=_headers(anon["token"]),
        timeout=15,
    )
    assert r.status_code == 403, (
        f"expected 403 for anonymous nickname PATCH, got {r.status_code}: {r.text}"
    )
