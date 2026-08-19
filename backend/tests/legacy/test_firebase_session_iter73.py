"""Backend tests for Firebase email/password integration (iter 73).

Covers:
- POST /api/auth/firebase-session with invalid token (bad segments) → 401
- POST /api/auth/firebase-session missing id_token → 422 validation
- firebase-admin app initialization does not crash imports/other endpoints
- Regression: /api/auth/login, /api/auth/signup, /api/auth/google-session, /api/auth/anonymous
  continue to work (no side-effects from firebase-admin import).
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["EXPO_BACKEND_URL"].rstrip("/") if os.environ.get("EXPO_BACKEND_URL") else "https://feud-governance.preview.emergentagent.com"


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ── Firebase session endpoint ──────────────────────────────────────────
class TestFirebaseSession:
    def test_invalid_token_returns_401(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/firebase-session",
                            json={"id_token": "fake"})
        assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"
        data = r.json()
        assert "detail" in data
        assert "Token Firebase non valido" in data["detail"], f"Detail was: {data['detail']}"

    def test_invalid_jwt_segments_returns_401(self, api_client):
        # Well-formed JWT-looking string but signature invalid
        r = api_client.post(f"{BASE_URL}/api/auth/firebase-session",
                            json={"id_token": "abc.def.ghi"})
        assert r.status_code == 401
        assert "Token Firebase non valido" in r.json().get("detail", "")

    def test_missing_id_token_returns_422(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/firebase-session", json={})
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    def test_empty_body_returns_422(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/firebase-session", data="")
        assert r.status_code == 422

    def test_endpoint_not_503(self, api_client):
        """If firebase-admin failed to init, endpoint returns 503. Making sure
        it correctly returned 401 instead confirms _get_firebase_app() worked."""
        r = api_client.post(f"{BASE_URL}/api/auth/firebase-session",
                            json={"id_token": "not-a-real-token"})
        assert r.status_code != 503, f"Firebase-admin not initialized: {r.text}"
        assert r.status_code == 401


# ── Regression: legacy auth endpoints still work ──────────────────────
class TestAuthRegression:
    def test_login_existing_verified_user(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login",
                            json={"email": "chat_a@test.it", "password": "test123"})
        assert r.status_code == 200, f"Login regression failed: {r.status_code} {r.text}"
        data = r.json()
        assert "token" in data and "user" in data
        assert data["user"]["email"] == "chat_a@test.it"

    def test_login_wrong_password(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login",
                            json={"email": "chat_a@test.it", "password": "wrongpw"})
        assert r.status_code in (400, 401), f"got {r.status_code}: {r.text}"

    def test_signup_creates_user_returns_verification(self, api_client):
        ts = int(time.time() * 1000)
        email = f"TEST_iter73_{ts}_{uuid.uuid4().hex[:6]}@test.it"
        nickname = f"iter73user{ts % 100000}"
        r = api_client.post(f"{BASE_URL}/api/auth/signup",
                            json={"email": email, "password": "test123456",
                                  "nickname": nickname})
        # Either legacy immediate session, OR requires_verification flag.
        assert r.status_code in (200, 201), f"Signup regression failed: {r.status_code} {r.text}"
        data = r.json()
        # Response must have either token+user (legacy) OR requires_verification
        has_token = "token" in data
        has_verify_flag = data.get("requires_verification") is True
        assert has_token or has_verify_flag, f"Unexpected signup response: {data}"

    def test_google_session_missing_session_id(self, api_client):
        # We can't do a real OAuth flow, but the endpoint should be reachable
        # and return a clean 4xx (not 5xx) when called with a bogus session_id.
        r = api_client.post(f"{BASE_URL}/api/auth/google-session",
                            json={"session_id": "bogus_session_id_iter73"})
        assert r.status_code < 500, f"google-session returned 5xx: {r.status_code} {r.text}"
        assert r.status_code in (400, 401, 403, 404, 422)

    def test_google_session_missing_body(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/google-session", json={})
        assert r.status_code == 422

    def test_anonymous_creates_user(self, api_client):
        ts = int(time.time() * 1000)
        nickname = f"iter73anon{ts % 100000}"
        r = api_client.post(f"{BASE_URL}/api/auth/anonymous",
                            json={"nickname": nickname})
        assert r.status_code == 200, f"Anonymous regression failed: {r.status_code} {r.text}"
        data = r.json()
        assert "token" in data and "user" in data
        assert data["user"].get("auth_provider") == "anonymous" or data["user"].get("is_anonymous") is True

    def test_auth_me_with_login_token(self, api_client):
        # Full round trip: login → /auth/me returns user
        login = api_client.post(f"{BASE_URL}/api/auth/login",
                                json={"email": "chat_a@test.it", "password": "test123"})
        assert login.status_code == 200
        token = login.json()["token"]
        me = api_client.get(f"{BASE_URL}/api/auth/me",
                            headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, f"/auth/me failed: {me.status_code} {me.text}"
        assert me.json()["user"]["email"] == "chat_a@test.it"


# ── Sanity: general API still up (firebase-admin import didn't break app) ──
class TestAppHealth:
    def test_root_or_docs_reachable(self, api_client):
        # Backend uses FastAPI docs at /docs by default
        r = api_client.get(f"{BASE_URL}/docs")
        assert r.status_code < 500

    def test_public_route_reachable(self, api_client):
        # Try a public listing endpoint to ensure the app started cleanly
        # after adding firebase-admin. Fall back gracefully if not present.
        candidates = ["/api/categories", "/api/feuds", "/api/stories"]
        ok = False
        for path in candidates:
            r = api_client.get(f"{BASE_URL}{path}")
            if r.status_code < 500:
                ok = True
                break
        assert ok, "All public endpoints returned 5xx — backend likely broken by firebase-admin import"
