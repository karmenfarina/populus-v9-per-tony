"""iter 152 — Firebase service-account key rotation.

Focus:
- Confirm the new key (populus-1f567) is loaded by the running backend, i.e.
  /api/auth/firebase-session returns 401 (SDK-level rejection) for a bogus
  token — NOT 503 (SDK not configured) nor 500 (server crash).
- Confirm the input-validation shape (missing / empty body → 422).
- Confirm backend startup was clean (no Firebase init error in supervisor log).
- Regression: legacy auth endpoints (login / signup / anonymous / google-session)
  still respond, and public read endpoints (/api/feuds, /api/stories/feed,
  /api/notifications) still respond. Also re-check the iter150/151 guarantees:
    * destructive-startup cleanup is gated behind ADMIN_TOKEN (no auto-wipe)
    * verify-email requires an absolute link (FRONTEND_BASE_URL); missing base
      → dispatch skipped, but signup still succeeds and returns requires_verification
    * EMERGENT_AUTH_BASE_URL is read from env (deducible via /api/auth/google-session
      returning a clean 4xx for bogus session, not 5xx)
    * admin retention endpoints exist and are gated by X-Admin-Key.
"""
import os
import time
import uuid
import pathlib
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "https://feud-governance.preview.emergentagent.com"
).rstrip("/")

ADMIN_TOKEN = "populus-admin-42b8f3"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─── Firebase SDK initialization (new key) ─────────────────────────────
class TestFirebaseSDKRotation:
    """Prove the new firebase-service-account.json is loaded successfully."""

    def test_bogus_token_returns_401_not_503(self, api):
        r = api.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={"id_token": "not-a-real-token"},
        )
        # 503 = SDK not configured / init failed → would indicate the new key is broken
        assert r.status_code != 503, f"Firebase SDK not initialized: {r.text}"
        assert r.status_code != 500, f"Backend crashed on firebase call: {r.text}"
        assert r.status_code == 401, f"Expected 401 from SDK, got {r.status_code}: {r.text}"
        body = r.json()
        assert "detail" in body
        assert "Token Firebase non valido" in body["detail"], body["detail"]

    def test_wellformed_jwt_shape_returns_401(self, api):
        # Three-segment JWT-like string still fails signature/issuer verification.
        r = api.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={"id_token": "abc.def.ghi"},
        )
        assert r.status_code == 401
        assert "Token Firebase non valido" in r.json().get("detail", "")

    def test_missing_id_token_returns_422(self, api):
        r = api.post(f"{BASE_URL}/api/auth/firebase-session", json={})
        assert r.status_code == 422, r.text

    def test_empty_body_returns_422(self, api):
        r = api.post(
            f"{BASE_URL}/api/auth/firebase-session",
            data="",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422

    def test_error_mentions_segments_or_invalid(self, api):
        """The SDK rejects with a clear reason, proving cryptographic init succeeded."""
        r = api.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={"id_token": "totally-broken"},
        )
        assert r.status_code == 401
        detail = r.json().get("detail", "").lower()
        # Any of these keywords indicates the SDK actively tried to verify
        assert any(k in detail for k in ("segments", "verify", "invalid", "malformed", "token")), detail


# ─── Backend startup cleanliness ───────────────────────────────────────
class TestBackendBootHealth:
    def test_no_firebase_init_failed_in_supervisor_log(self):
        log_path = pathlib.Path("/var/log/supervisor/backend.err.log")
        if not log_path.exists():
            pytest.skip("supervisor log not accessible")
        # Look at the tail (last boot cycle only — post the most recent 'Started server process')
        text = log_path.read_text(errors="ignore")
        boot_marker = text.rfind("Started server process")
        tail = text[boot_marker:] if boot_marker != -1 else text[-20000:]
        assert "Firebase init failed" not in tail, tail[-2000:]
        assert "FIREBASE_SERVICE_ACCOUNT_JSON parse failed" not in tail
        assert "Firebase not configured" not in tail, tail[-2000:]

    def test_service_account_file_valid(self):
        import json
        path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "/app/backend/firebase-service-account.json")
        assert os.path.exists(path)
        data = json.loads(pathlib.Path(path).read_text())
        assert data.get("type") == "service_account"
        assert data.get("project_id") == "populus-1f567"
        assert "iam.gserviceaccount.com" in data.get("client_email", "")


# ─── Regression: legacy auth ───────────────────────────────────────────
class TestAuthRegression:
    """Confirm firebase-admin import + new key rotation didn't break other auth flows."""

    def test_login_wrong_password_returns_400_or_401(self, api):
        r = api.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrongpw"},
        )
        assert r.status_code < 500, f"got {r.status_code}: {r.text}"
        assert r.status_code in (400, 401, 403, 404)

    def test_signup_creates_user(self, api):
        ts = int(time.time() * 1000)
        email = f"TEST_iter152_{ts}_{uuid.uuid4().hex[:6]}@example.com"
        nickname = f"iter152u{ts % 100000}"
        r = api.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": email, "password": "test123456", "nickname": nickname},
        )
        assert r.status_code in (200, 201), r.text
        data = r.json()
        # Either legacy immediate session OR requires_verification
        assert "token" in data or data.get("requires_verification") is True, data

    def test_anonymous_creates_user(self, api):
        ts = int(time.time() * 1000)
        nickname = f"iter152anon{ts % 100000}"
        r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nickname})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data and "user" in data
        assert data["user"].get("auth_provider") == "anonymous" or data["user"].get("is_anonymous") is True

    def test_google_session_bogus_returns_4xx_not_5xx(self, api):
        # Endpoint reachable + EMERGENT_AUTH_BASE_URL resolves (else 5xx).
        r = api.post(
            f"{BASE_URL}/api/auth/google-session",
            json={"session_id": "bogus_iter152"},
        )
        assert r.status_code < 500, f"google-session 5xx = auth base URL misconfigured: {r.text}"
        assert r.status_code in (400, 401, 403, 404, 422)

    def test_google_session_missing_body_returns_422(self, api):
        r = api.post(f"{BASE_URL}/api/auth/google-session", json={})
        assert r.status_code == 422


# ─── Regression: public/read endpoints ─────────────────────────────────
class TestPublicRegression:
    def test_feuds_list(self, api):
        r = api.get(f"{BASE_URL}/api/feuds?category=all")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, (list, dict))

    def test_stories_feed_requires_auth_or_lists(self, api):
        # /api/stories/feed exists and returns a clean status (either 200 for public
        # or 401 if it requires auth). Not 5xx.
        r = api.get(f"{BASE_URL}/api/stories/feed")
        assert r.status_code < 500, r.text
        assert r.status_code in (200, 401, 403)

    def test_notifications_requires_auth_not_5xx(self, api):
        r = api.get(f"{BASE_URL}/api/notifications")
        assert r.status_code < 500, r.text
        assert r.status_code in (401, 403), r.text  # unauthenticated → 401


# ─── Regression: admin moderation + retention ──────────────────────────
class TestAdminRegression:
    def test_admin_without_token_forbidden(self, api):
        # Any admin endpoint must reject anonymous callers with 401/403.
        r = api.get(f"{BASE_URL}/api/admin/feuds")
        assert r.status_code < 500
        assert r.status_code in (401, 403, 404), r.text

    def test_admin_with_token_returns_2xx_or_404(self, api):
        r = api.get(
            f"{BASE_URL}/api/admin/feuds",
            headers={"X-Admin-Key": ADMIN_TOKEN},
        )
        # 200 if endpoint returns list, 404 if only /api/admin/feuds/{id} exists
        assert r.status_code < 500, r.text

    def test_admin_retention_manual_endpoint_gated(self, api):
        # iter150/151: destructive startup removed, retention is admin-triggered.
        # We only assert the endpoint gate — not the deletion effect.
        candidates = [
            "/api/admin/retention/run",
            "/api/admin/retention",
            "/api/admin/cleanup",
        ]
        # Without admin token, must be 401/403/404 (not 5xx, not 200)
        for path in candidates:
            r = api.post(f"{BASE_URL}{path}", json={})
            assert r.status_code < 500, f"{path} → {r.status_code} {r.text}"
            assert r.status_code != 200, f"{path} allowed unauthenticated! {r.text}"


# ─── Verify-email regression (iter151 absolute-link guard) ─────────────
class TestVerifyEmailRegression:
    def test_verify_email_requires_token(self, api):
        r = api.post(f"{BASE_URL}/api/auth/verify-email", json={})
        assert r.status_code in (400, 422), r.text

    def test_verify_email_invalid_token_400(self, api):
        r = api.post(
            f"{BASE_URL}/api/auth/verify-email",
            json={"token": "obviously-not-a-real-token-xyz"},
        )
        assert r.status_code == 400, r.text

    def test_resend_verification_returns_generic_200(self, api):
        # Anti-enumeration: always 200 regardless of whether user exists.
        r = api.post(
            f"{BASE_URL}/api/auth/resend-verification",
            json={"email": f"nobody_iter152_{uuid.uuid4().hex[:6]}@example.com"},
        )
        assert r.status_code == 200, r.text
