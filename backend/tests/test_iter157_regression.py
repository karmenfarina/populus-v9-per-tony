"""Iter157 regression: stories viewer backend endpoints + anonymous auth smoke."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://feud-governance.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def anon_token():
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": f"TEST_iter157_{os.getpid()}"}, timeout=15)
    assert r.status_code == 200, f"anonymous auth failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("token") or body.get("access_token") or body.get("session_token")
    assert tok, f"no token in response: {body}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(anon_token):
    return {"Authorization": f"Bearer {anon_token}", "Content-Type": "application/json"}


class TestStoriesEndpoints:
    def test_stories_feed_ok(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=auth_headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert "groups" in data or isinstance(data, dict), f"unexpected: {data}"

    def test_stories_by_user_current_user(self, auth_headers):
        # Get self user_id from /api/auth/me
        me_r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=15)
        assert me_r.status_code == 200, f"me failed: {me_r.status_code} {me_r.text}"
        body = me_r.json()
        user = body.get("user", body)
        uid = user.get("user_id") or user.get("id")
        assert uid, f"no user_id: {me_r.json()}"
        r = requests.get(f"{BASE_URL}/api/stories/user/{uid}", headers=auth_headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert "stories" in data, f"unexpected: {data}"
        assert isinstance(data["stories"], list)

    def test_stories_view_invalid_id_404_or_400(self, auth_headers):
        # Non-existent story ID should return 404 (or 400), not 500
        r = requests.post(f"{BASE_URL}/api/stories/nonexistent-id-iter157/view", headers=auth_headers, timeout=15)
        assert r.status_code in (400, 404, 422), f"expected 4xx, got {r.status_code}: {r.text[:200]}"


class TestAuthSmoke:
    def test_anonymous_signup(self):
        r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": f"TEST_iter157smoke_{os.getpid()}"}, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert "token" in data or "access_token" in data or "session_token" in data
