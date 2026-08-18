"""Regression tests for iteration 126 bugs:
- Bug 3: POST /api/auth/anonymous with valid nickname returns 200 + JWT
- Bug 4: POST /api/users/me/accept-terms accepts {version:'v1', nda_version:'v1'}
"""
import os
import uuid
import pytest
import requests

BASE_URL = (os.environ.get('EXPO_PUBLIC_BACKEND_URL') or os.environ.get('EXPO_BACKEND_URL') or '').rstrip('/')
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─── Bug 3: anonymous login ────────────────────────────────────────
class TestAnonymousLogin:
    def test_anonymous_login_valid_nickname_returns_token(self, api):
        nick = f"testanon{uuid.uuid4().hex[:6]}"
        r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 20
        assert "user" in data
        assert data["user"]["nickname"] == nick
        assert data["user"].get("auth_provider") == "anonymous" or data["user"].get("is_anonymous")

    def test_anonymous_login_empty_nickname_rejected(self, api):
        r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": ""})
        assert r.status_code in (400, 422), r.text

    def test_anonymous_login_token_works_for_me(self, api):
        nick = f"testanon{uuid.uuid4().hex[:6]}"
        r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick})
        assert r.status_code == 200
        token = r.json()["token"]
        me = api.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        body = me.json()
        user = body.get("user", body)
        assert user.get("nickname") == nick


# ─── Bug 4: accept-terms with terms + NDA in single request ────────
class TestAcceptTermsAndNda:
    @pytest.fixture
    def anon_token(self, api):
        nick = f"tanon{uuid.uuid4().hex[:6]}"
        r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick})
        assert r.status_code == 200
        return r.json()["token"]

    def test_accept_both_terms_and_nda(self, api, anon_token):
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "v1", "nda_version": "v1"},
            headers={"Authorization": f"Bearer {anon_token}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("terms_accepted") is True
        assert data.get("terms_accepted_version") == "v1"
        assert data.get("nda_accepted_version") == "v1"

        # Verify /auth/me reflects the acceptance
        me = api.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {anon_token}"})
        assert me.status_code == 200
        me_body = me.json()
        me_user = me_body.get("user", me_body)
        assert me_user.get("terms_accepted") is True

    def test_accept_terms_only_returns_terms_accepted_false(self, api, anon_token):
        # Legacy shape: only version → terms accepted, but combined flag is False
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "v1"},
            headers={"Authorization": f"Bearer {anon_token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("terms_accepted_version") == "v1"
        assert data.get("terms_accepted") is False  # NDA not yet accepted

    def test_wrong_nda_version_rejected(self, api, anon_token):
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "v1", "nda_version": "v999"},
            headers={"Authorization": f"Bearer {anon_token}"},
        )
        assert r.status_code == 400


# ─── Bug 4b: legal endpoints reachable ─────────────────────────────
class TestLegalEndpoints:
    def test_get_terms(self, api):
        r = api.get(f"{BASE_URL}/api/legal/terms")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data and "text" in data
        assert data["version"] == "v1"

    def test_get_nda(self, api):
        r = api.get(f"{BASE_URL}/api/legal/nda")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data and "text" in data
        assert data["version"] == "v1"
