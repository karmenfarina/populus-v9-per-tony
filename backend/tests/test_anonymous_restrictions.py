"""Backend tests for anonymous customization restrictions (Populus).

Verifies:
- POST /auth/anonymous → user.onboarding_completed=True, favorite_categories=[]
- Anonymous 403 on: PATCH /auth/me/details, POST /auth/me/photos,
  PATCH /auth/me/profile
- Registered email/password user can still customize (details + profile).
"""
import os
import base64
import uuid
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

TINY_B64 = base64.b64encode(b"P" * 512).decode()
ANON_MSG = "Personalizzazione riservata agli account registrati"


# --- Fixtures ---
@pytest.fixture(scope="module")
def anon_ctx():
    nick = f"anonR_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    return {
        "token": data["token"],
        "user": data["user"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


@pytest.fixture(scope="module")
def reg_ctx():
    tag = uuid.uuid4().hex[:6]
    payload = {
        "email": f"TEST_reg_{tag}@example.com",
        "password": "Passw0rd!",
        "nickname": f"reg_{tag}",
    }
    r = requests.post(f"{API}/auth/signup", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    return {
        "token": data["token"],
        "user": data["user"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


# --- Anonymous signup shape ---
class TestAnonymousSignup:
    def test_anonymous_user_bypasses_onboarding_and_categories(self, anon_ctx):
        u = anon_ctx["user"]
        assert u.get("auth_provider") == "anonymous"
        assert u.get("onboarding_completed") is True
        assert u.get("favorite_categories") == []

    def test_get_me_reflects_persisted_state(self, anon_ctx):
        r = requests.get(f"{API}/auth/me", headers=anon_ctx["headers"], timeout=15)
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["onboarding_completed"] is True
        assert u["favorite_categories"] == []


# --- Anonymous restriction: 403 on customization endpoints ---
class TestAnonymousBlocked:
    def test_patch_details_forbidden(self, anon_ctx):
        r = requests.patch(
            f"{API}/auth/me/details",
            headers=anon_ctx["headers"],
            json={"bio": "test"},
            timeout=15,
        )
        assert r.status_code == 403, r.text
        assert r.json().get("detail") == ANON_MSG

    def test_post_photo_forbidden(self, anon_ctx):
        r = requests.post(
            f"{API}/auth/me/photos",
            headers=anon_ctx["headers"],
            json={"data": TINY_B64},
            timeout=15,
        )
        assert r.status_code == 403, r.text
        assert r.json().get("detail") == ANON_MSG

    def test_patch_profile_forbidden(self, anon_ctx):
        body = {
            "age": 30,
            "sex": "M",
            "region": "Lombardia",
            "favorite_categories": ["sport"],
        }
        r = requests.patch(
            f"{API}/auth/me/profile",
            headers=anon_ctx["headers"],
            json=body,
            timeout=15,
        )
        assert r.status_code == 403, r.text
        assert r.json().get("detail") == ANON_MSG


# --- Regression: registered users can still customize ---
class TestRegisteredStillWorks:
    def test_patch_details_ok(self, reg_ctx):
        r = requests.patch(
            f"{API}/auth/me/details",
            headers=reg_ctx["headers"],
            json={"bio": "TEST bio ok"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["user"]["bio"] == "TEST bio ok"

    def test_patch_profile_ok(self, reg_ctx):
        body = {
            "age": 28,
            "sex": "F",
            "region": "Lombardia",
            "favorite_categories": ["sport", "tech"],
        }
        r = requests.patch(
            f"{API}/auth/me/profile",
            headers=reg_ctx["headers"],
            json=body,
            timeout=15,
        )
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["age"] == 28
        assert u["sex"] == "F"
        assert u["region"] == "Lombardia"
        assert set(u["favorite_categories"]) == {"sport", "tech"}
        assert u["onboarding_completed"] is True
