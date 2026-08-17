"""Iter119 backend regression for the "Missing bearer token" logout crash.

The fix is client-side (frontend/src/api.ts) — this test ensures the
BACKEND contract used by that fix is intact:
  A. Authed endpoints still return 401 with detail "Missing bearer token"
     when no Authorization header is present (client-side short-circuit
     relies on knowing this is what would happen otherwise).
  B. Public endpoints remain accessible without a token.
  C. Authed endpoints work with a valid token.
  D. /api/feuds/hype supports both anon and auth (optional auth).
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

INTERNAL = "http://localhost:8001/api"

A_EMAIL = "chat_a@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"


@pytest.fixture(scope="module")
def sess():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(sess):
    r = sess.post(f"{INTERNAL}/auth/login",
                  json={"email": A_EMAIL, "password": PASS}, timeout=15)
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()["token"]


# ═════════════════ A) Authed endpoints WITHOUT token ═════════════════


class TestMissingBearerContract:
    """The frontend short-circuits so that the backend NEVER sees these
    requests during logout — but if it did, it must return the exact
    detail string ('Missing bearer token') the frontend used to surface
    as a red screen. This regression protects the assumption."""

    def test_photos_without_auth_401_missing_bearer(self, sess):
        r = sess.get(f"{INTERNAL}/auth/me/photos", timeout=10)
        assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text[:200]}"
        detail = (r.json() or {}).get("detail") or ""
        assert "Missing bearer token" in detail, (
            f"BUG: authed endpoint must reply 'Missing bearer token'; got {detail!r}"
        )

    def test_me_without_auth_401(self, sess):
        r = sess.get(f"{INTERNAL}/auth/me", timeout=10)
        assert r.status_code == 401
        detail = (r.json() or {}).get("detail") or ""
        assert "Missing bearer token" in detail, f"got {detail!r}"

    def test_history_without_auth_401(self, sess):
        r = sess.get(f"{INTERNAL}/users/me/history?filter=all", timeout=10)
        assert r.status_code == 401
        detail = (r.json() or {}).get("detail") or ""
        assert "Missing bearer token" in detail, f"got {detail!r}"

    def test_notifications_without_auth_401(self, sess):
        r = sess.get(f"{INTERNAL}/notifications", timeout=10)
        assert r.status_code == 401


# ═════════════════ B) Public endpoints stay open ═════════════════


class TestPublicEndpoints:

    def test_categories_no_token(self, sess):
        r = sess.get(f"{INTERNAL}/categories", timeout=10)
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert isinstance(body, (list, dict)), f"unexpected body type: {type(body)}"

    def test_professions_no_token(self, sess):
        r = sess.get(f"{INTERNAL}/professions", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, (list, dict))

    def test_login_no_token(self, sess):
        r = sess.post(f"{INTERNAL}/auth/login",
                      json={"email": A_EMAIL, "password": PASS}, timeout=15)
        assert r.status_code == 200
        assert "token" in r.json()


# ═════════════════ C) Authed endpoints WITH token ═════════════════


class TestAuthedWithToken:

    def test_photos_with_token_200(self, sess, token):
        r = sess.get(f"{INTERNAL}/auth/me/photos",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"

    def test_me_with_token_200(self, sess, token):
        r = sess.get(f"{INTERNAL}/auth/me",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        me = body.get("user") or body
        assert me.get("user_id") == A_ID


# ═════════════════ D) /feuds/hype optional auth ═════════════════


class TestHypeOptionalAuth:

    def test_hype_anon_200(self, sess):
        r = sess.get(f"{INTERNAL}/feuds/hype", timeout=10)
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
        body = r.json()
        # Body is a list of feuds (may be empty).
        assert isinstance(body, (list, dict))

    def test_hype_auth_200(self, sess, token):
        r = sess.get(f"{INTERNAL}/feuds/hype",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 200
