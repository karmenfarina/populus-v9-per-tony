"""
Light backend regression smoke test — Iteration 138
Purpose: verify backend still responds after frontend-only ads library downgrade
(react-native-google-mobile-ads 16.4.0 -> 15.4.0). Backend was untouched but we
sanity-check the endpoints listed in the review request.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://bot-burst-fix.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- root/health ---------------------------------------------------------
def test_api_root_ok(api_client):
    """/api/health does not exist in this codebase; /api/ is the root health probe."""
    r = api_client.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert "Populus" in body.get("message", "")


# --- categories ----------------------------------------------------------
def test_categories_list(api_client):
    r = api_client.get(f"{BASE_URL}/api/categories", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    cats = body.get("categories")
    assert isinstance(cats, list) and len(cats) > 0
    # sanity — each entry has id + label
    for c in cats[:3]:
        assert "id" in c and "label" in c


# --- feuds hype ----------------------------------------------------------
def test_feuds_hype_limit3(api_client):
    r = api_client.get(f"{BASE_URL}/api/feuds/hype?limit=3", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    feuds = body.get("feuds")
    assert isinstance(feuds, list)
    assert len(feuds) <= 3
    if feuds:
        f0 = feuds[0]
        for k in ("feud_id", "title", "party_a", "party_b", "category"):
            assert k in f0, f"missing key {k} in hype feud"
