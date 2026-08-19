"""Backend tests for the onboarding PATCH /api/auth/me/profile endpoint and related auth/me behavior."""
import os
import uuid
import pytest
import requests

BASE_URL = "https://bot-burst-fix.preview.emergentagent.com"


@pytest.fixture
def anon_user():
    """Create a fresh anonymous user; return {token, user}."""
    nick = f"TEST_{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def auth_headers(anon_user):
    return {"Authorization": f"Bearer {anon_user['token']}", "Content-Type": "application/json"}


# --- GET /api/auth/me on a fresh anonymous user ---
def test_me_fresh_anon_has_onboarding_false_and_empty_favs(anon_user, auth_headers):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["onboarding_completed"] is False
    assert user.get("favorite_categories", []) == []


# --- PATCH profile: happy path ---
def test_patch_profile_valid_returns_200_and_persists(anon_user, auth_headers):
    body = {"age": 27, "sex": "M", "region": "Lombardia",
            "favorite_categories": ["politica", "sport"]}
    r = requests.patch(f"{BASE_URL}/api/auth/me/profile", json=body, headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["onboarding_completed"] is True
    assert user["age"] == 27
    assert user["sex"] == "M"
    assert user["region"] == "Lombardia"
    assert sorted(user["favorite_categories"]) == ["politica", "sport"]

    # Verify persistence via GET /me
    r2 = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=15)
    u2 = r2.json()["user"]
    assert u2["onboarding_completed"] is True
    assert sorted(u2["favorite_categories"]) == ["politica", "sport"]


# --- PATCH profile: age validation (Pydantic ge=13) ---
def test_patch_profile_age_too_low_returns_422(anon_user, auth_headers):
    body = {"age": 5, "sex": "M", "region": "Lombardia", "favorite_categories": ["sport"]}
    r = requests.patch(f"{BASE_URL}/api/auth/me/profile", json=body, headers=auth_headers, timeout=15)
    assert r.status_code == 422, r.text


# --- PATCH profile: invalid category ---
def test_patch_profile_invalid_category_returns_400(anon_user, auth_headers):
    body = {"age": 27, "sex": "M", "region": "Lombardia",
            "favorite_categories": ["politica", "not_a_real_cat"]}
    r = requests.patch(f"{BASE_URL}/api/auth/me/profile", json=body, headers=auth_headers, timeout=15)
    assert r.status_code == 400, r.text
    assert "Categorie non valide" in r.json().get("detail", "")


# --- PATCH profile: invalid region ---
def test_patch_profile_invalid_region_returns_400(anon_user, auth_headers):
    body = {"age": 27, "sex": "M", "region": "Wonderland", "favorite_categories": ["sport"]}
    r = requests.patch(f"{BASE_URL}/api/auth/me/profile", json=body, headers=auth_headers, timeout=15)
    assert r.status_code == 400, r.text
    assert "Regione non valida" in r.json().get("detail", "")


# --- PATCH profile: no auth ---
def test_patch_profile_without_token_returns_401():
    body = {"age": 27, "sex": "M", "region": "Lombardia", "favorite_categories": ["sport"]}
    r = requests.patch(f"{BASE_URL}/api/auth/me/profile", json=body,
                      headers={"Content-Type": "application/json"}, timeout=15)
    assert r.status_code == 401, r.text
