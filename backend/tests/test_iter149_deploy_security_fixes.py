"""
Iteration 149 — Deploy-security fix regressions
===============================================
Validates the two backend hygiene changes:

1. `_get_firebase_app()` accepts either
   - FIREBASE_SERVICE_ACCOUNT_JSON (inline JSON, prod)
   - FIREBASE_SERVICE_ACCOUNT_PATH (path on disk, dev — current env)
   and returns None (→ 503) gracefully when neither is set. When path IS
   set (current preview) firebase-session must return 401 for an invalid
   id_token (not 500, no crash).

2. `/auth/google-session` reads EMERGENT_AUTH_BASE_URL from env with a
   safe default. With a bogus session_id it must return 401 (the outbound
   HTTP call to Emergent returns non-200), not 500.

3. Regressions: login / signup / anonymous / /api/feuds / /api/stories/feed
   continue to work.
"""
from __future__ import annotations
import os
import uuid
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or ""
).rstrip("/")
assert BASE_URL, "EXPO_BACKEND_URL/EXPO_PUBLIC_BACKEND_URL missing"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ── 1. Firebase graceful handling ──────────────────────────────────────

class TestFirebaseSessionGraceful:
    def test_firebase_session_invalid_token_no_crash(self, session):
        """With SA configured (path OR json env), an invalid id_token must
        NOT crash the server. Backend must return 401 (invalid token) or
        503 (not configured) — never 500."""
        r = session.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={"id_token": "definitely.not.a.valid.jwt"},
            timeout=30,
        )
        assert r.status_code in (401, 403, 503), (
            f"firebase-session with invalid token returned {r.status_code}: {r.text}"
        )
        # Payload must include detail (no stack trace leaked)
        body = r.json()
        assert "detail" in body
        assert isinstance(body["detail"], str)

    def test_firebase_session_missing_field_422(self, session):
        """Pydantic validation still works — missing id_token → 422."""
        r = session.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={},
            timeout=15,
        )
        assert r.status_code == 422, r.text

    def test_firebase_session_empty_token_no_crash(self, session):
        """Empty id_token should be handled cleanly (not 500)."""
        r = session.post(
            f"{BASE_URL}/api/auth/firebase-session",
            json={"id_token": ""},
            timeout=15,
        )
        # Either 401 (invalid) or 422 (validation) — never 500.
        assert r.status_code in (401, 403, 422, 503), (
            f"firebase-session empty token returned {r.status_code}: {r.text}"
        )


# ── 2. Google-session (EMERGENT_AUTH_BASE_URL) ─────────────────────────

class TestGoogleSessionEnvBase:
    def test_google_session_invalid_session_id_returns_401(self, session):
        """With a bogus session_id the upstream call to
        `${EMERGENT_AUTH_BASE_URL}/auth/v1/env/oauth/session-data` must
        return non-200 → backend translates that to 401 (not 500)."""
        r = session.post(
            f"{BASE_URL}/api/auth/google-session",
            json={"session_id": f"invalid_{uuid.uuid4().hex}"},
            timeout=30,
        )
        # Accept 401 (bad session) or 502/504 if Emergent upstream is down.
        # 500 would indicate a URL-related crash (regression).
        assert r.status_code in (401, 502, 503, 504), (
            f"google-session bogus id returned {r.status_code}: {r.text}"
        )

    def test_google_session_missing_field_422(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/google-session",
            json={},
            timeout=15,
        )
        assert r.status_code == 422, r.text


# ── 3. Auth regressions (email / anonymous / login) ────────────────────

@pytest.fixture(scope="module")
def anon_token(session):
    nick = f"tst_{uuid.uuid4().hex[:8]}"
    r = session.post(
        f"{BASE_URL}/api/auth/anonymous",
        json={"nickname": nick},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


class TestAuthRegression:
    def test_anonymous_signup(self, anon_token):
        assert isinstance(anon_token, str) and len(anon_token) > 20

    def test_auth_me_with_anon(self, session, anon_token):
        r = session.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {anon_token}"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        u = r.json().get("user", {})
        assert u.get("is_anonymous") is True

    def test_email_signup_requires_verification(self, session):
        email = f"iter149_{uuid.uuid4().hex[:10]}@example.com"
        nick = f"i149_{uuid.uuid4().hex[:6]}"
        r = session.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": email, "password": "Passw0rd!", "nickname": nick},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("requires_verification") is True

    def test_login_wrong_password_401(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrong"},
            timeout=30,
        )
        assert r.status_code in (401, 429), r.text


# ── 4. /api/feuds still healthy (iter148 dedupe regression) ────────────

class TestFeedRegression:
    def test_feuds_returns_list(self, session):
        r = session.get(f"{BASE_URL}/api/feuds", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        feuds = data.get("feuds")
        assert isinstance(feuds, list)
        assert len(feuds) <= 200
        # Basic payload shape retained
        if feuds:
            f = feuds[0]
            for k in ("feud_id", "title", "category"):
                assert k in f, f"missing {k} in feud payload"


# ── 5. /api/stories/feed still healthy (iter145 bot bucket) ────────────

class TestStoriesFeedRegression:
    def test_stories_feed_anon(self, session, anon_token):
        r = session.get(
            f"{BASE_URL}/api/stories/feed",
            headers={"Authorization": f"Bearer {anon_token}"},
            timeout=30,
        )
        # Anonymous users may be gated (403) or served (200). Never 500.
        assert r.status_code in (200, 403), r.text
        if r.status_code == 200:
            assert isinstance(r.json(), dict)
