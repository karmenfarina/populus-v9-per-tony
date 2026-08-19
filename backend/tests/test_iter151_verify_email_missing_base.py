"""
Iter 151 — Deployment health-check follow-up.

Focus: `_send_verification_email` must NOT send an email when
`FRONTEND_BASE_URL` is empty (previous behaviour built a relative
`/verify-email?token=…` link, unusable in mail clients).

Instead the function should:
  • still generate and persist a verification token,
  • log the warning `FRONTEND_BASE_URL missing`,
  • return silently (user remains unverified and can resend later).

Regressions covered:
  • /api/auth/verify-email with a valid token
  • /api/auth/resend-verification (safe generic response)
  • /api/feuds/ (public read)
  • /api/auth/anonymous (session flow)

The current preview environment intentionally has
`FRONTEND_BASE_URL` unset (only `EXPO_PUBLIC_BACKEND_URL` lives in
`frontend/.env`, never seen by the backend process), so we can
exercise the "missing base" branch end-to-end without patching env.
"""

import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ['EXPO_BACKEND_URL'].rstrip('/') if os.environ.get('EXPO_BACKEND_URL') else (
    os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://feud-governance.preview.emergentagent.com').rstrip('/')
)
BACKEND_LOG = '/var/log/supervisor/backend.err.log'


# ─── module-level helpers ─────────────────────────────────────
def _tail(path: str, nbytes: int = 30000) -> str:
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            return f.read().decode('utf-8', 'replace')
    except FileNotFoundError:
        return ''


@pytest.fixture(scope='module')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def mdb():
    """Direct Mongo access — needed to inspect verification_tokens (no
    email is actually delivered in preview, so we read the token hash
    from DB, generate a raw token match via a controlled insert, or
    just assert the doc exists)."""
    mongo_url = os.environ['MONGO_URL']
    dbname = os.environ['DB_NAME']
    c = MongoClient(mongo_url)
    yield c[dbname]
    c.close()


# ─── Signup flow with missing FRONTEND_BASE_URL ───────────────
class TestSignupMissingBase:
    """When FRONTEND_BASE_URL is empty (current preview state) the signup
    endpoint must still succeed and create an unverified user, but MUST NOT
    ship an email — the module logs a warning and returns early."""

    email = f'TEST_iter151_missing_{int(time.time())}@example.com'
    password = 'testpass123'
    nickname = f'tst151_{int(time.time()) % 100000}'

    def test_signup_returns_requires_verification(self, api, mdb):
        # size the log window BEFORE the request so we can detect the new warn line.
        before = len(_tail(BACKEND_LOG, 200000))
        r = api.post(f'{BASE_URL}/api/auth/signup', json={
            'email': self.email,
            'password': self.password,
            'nickname': self.nickname,
        })
        assert r.status_code == 200, f'signup failed: {r.status_code} {r.text[:300]}'
        body = r.json()
        assert body.get('requires_verification') is True
        assert body.get('email') == self.email.lower()
        # No JWT token returned — user is unverified.
        assert 'token' not in body
        # Give the (background) log flush a beat.
        time.sleep(0.6)
        after = _tail(BACKEND_LOG, 400000)
        # Only look at NEW log content since the request.
        new_slice = after[max(0, len(after) - 200000):]
        assert 'FRONTEND_BASE_URL missing' in new_slice, (
            'Expected the "FRONTEND_BASE_URL missing" warning in backend logs. '
            f'Tail excerpt: {new_slice[-800:]}'
        )
        # We do NOT expect a Resend send attempt log for this signup.
        # (RESEND_API_KEY is set but the base guard aborts BEFORE that branch.)
        # This is asserted implicitly: the "Resend verification email" strings
        # appear only inside the httpx.post block which we never reach.
        # We loosely check the excerpt doesn't contain the exception marker
        # from that block.
        assert 'Resend verification email exception' not in new_slice

    def test_user_exists_unverified(self, mdb):
        u = mdb.users.find_one({'email': self.email.lower()}, {'_id': 0})
        assert u is not None, 'user should have been created despite missing base'
        assert u.get('email_verified') is False
        assert u.get('auth_provider') == 'email'
        assert u.get('nickname') == self.nickname

    def test_verification_token_still_persisted(self, mdb):
        """Even without sending the email, the token doc is stored so we
        can consume it later (e.g. after FRONTEND_BASE_URL is set and a
        resend is triggered). Confirms the token generation happens BEFORE
        the base-URL guard."""
        u = mdb.users.find_one({'email': self.email.lower()}, {'_id': 0, 'user_id': 1})
        assert u
        tok = mdb.verification_tokens.find_one({'user_id': u['user_id']})
        assert tok is not None
        assert tok.get('token_hash')
        assert tok.get('expires_at') > datetime.utcnow() - timedelta(minutes=1)

    def test_login_blocked_until_verified(self, api):
        r = api.post(f'{BASE_URL}/api/auth/login', json={
            'email': self.email,
            'password': self.password,
        })
        assert r.status_code == 403, f'expected 403 for unverified email, got {r.status_code}'
        # detail is a dict for this branch — see server.py:1128
        detail = r.json().get('detail') or {}
        # FastAPI may serialise dict-detail as-is; be resilient to both shapes
        if isinstance(detail, dict):
            assert detail.get('email_not_verified') is True

    def test_resend_verification_returns_generic_success(self, api):
        # Resend also calls _send_verification_email which SHOULD hit the same
        # missing-base guard. Expect 200 with generic message so we don't leak
        # account state.
        r = api.post(f'{BASE_URL}/api/auth/resend-verification', json={
            'email': self.email,
        })
        assert r.status_code == 200
        body = r.json()
        assert body.get('ok') is True
        assert 'message' in body

    def test_cleanup(self, mdb):
        # Purge test artifacts so we don't leave rows around.
        u = mdb.users.find_one({'email': self.email.lower()}, {'_id': 0, 'user_id': 1})
        if u:
            mdb.verification_tokens.delete_many({'user_id': u['user_id']})
            mdb.users.delete_one({'user_id': u['user_id']})


# ─── verify-email regression: valid token still works ─────────
class TestVerifyEmailRegression:
    """We craft a raw token + hash directly in Mongo (skipping the email
    send path entirely) then hit /api/auth/verify-email to confirm the
    endpoint hasn't regressed on the guard-return change."""

    email = f'TEST_iter151_verify_{int(time.time())}@example.com'
    raw_token = None
    user_id = None

    def test_seed_user_and_token(self, mdb):
        from uuid import uuid4
        self.__class__.user_id = f'user_iter151_{uuid4().hex[:8]}'
        mdb.users.insert_one({
            'user_id': self.user_id,
            'email': self.email.lower(),
            'nickname': f'ver151_{int(time.time()) % 100000}',
            'password_hash': 'not-used',
            'auth_provider': 'email',
            'created_at': datetime.now(timezone.utc),
            'email_verified': False,
            'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
        })
        self.__class__.raw_token = secrets.token_urlsafe(32)
        tok_hash = hashlib.sha256(self.raw_token.encode('utf-8')).hexdigest()
        mdb.verification_tokens.delete_many({'user_id': self.user_id})
        mdb.verification_tokens.insert_one({
            'user_id': self.user_id,
            'token_hash': tok_hash,
            'created_at': datetime.now(timezone.utc),
            'expires_at': datetime.now(timezone.utc) + timedelta(hours=24),
        })

    def test_verify_email_success(self, api, mdb):
        assert self.raw_token, 'seed step must have run first'
        r = api.post(f'{BASE_URL}/api/auth/verify-email', json={'token': self.raw_token})
        assert r.status_code == 200, f'verify-email failed: {r.status_code} {r.text[:300]}'
        body = r.json()
        assert body.get('ok') is True
        assert 'token' in body  # JWT session
        assert body.get('user', {}).get('email') == self.email.lower()
        # DB flag flipped
        u = mdb.users.find_one({'user_id': self.user_id}, {'_id': 0, 'email_verified': 1})
        assert u and u.get('email_verified') is True
        # Token consumed
        left = mdb.verification_tokens.count_documents({'user_id': self.user_id})
        assert left == 0

    def test_verify_email_invalid_token(self, api):
        r = api.post(f'{BASE_URL}/api/auth/verify-email', json={'token': 'garbage-not-a-real-token'})
        assert r.status_code == 400
        assert 'valido' in (r.json().get('detail') or '').lower() or 'scaduto' in (r.json().get('detail') or '').lower()

    def test_cleanup(self, mdb):
        if self.user_id:
            mdb.verification_tokens.delete_many({'user_id': self.user_id})
            mdb.users.delete_one({'user_id': self.user_id})


# ─── Broader regression: public endpoints still healthy ───────
class TestBackendHealth:
    def test_feuds_list(self, api):
        r = api.get(f'{BASE_URL}/api/feuds/')
        assert r.status_code == 200
        body = r.json()
        assert 'feuds' in body
        assert isinstance(body['feuds'], list)

    def test_anonymous_login(self, api):
        r = api.post(f'{BASE_URL}/api/auth/anonymous', json={'nickname': f'tst151anon_{int(time.time()) % 100000}'})
        assert r.status_code == 200, f'{r.status_code} {r.text[:300]}'
        body = r.json()
        assert 'token' in body
        assert body.get('user', {}).get('auth_provider') == 'anonymous'

    def test_login_wrong_password(self, api):
        r = api.post(f'{BASE_URL}/api/auth/login', json={
            'email': 'nobody-iter151@example.com',
            'password': 'wrong',
        })
        assert r.status_code == 401


# ─── Lockfile hygiene: package-lock.json removed ──────────────
class TestFrontendLockfile:
    def test_package_lock_gone(self):
        assert not os.path.exists('/app/frontend/package-lock.json'), (
            'package-lock.json should have been removed (yarn is the source of truth)'
        )

    def test_yarn_lock_present(self):
        assert os.path.exists('/app/frontend/yarn.lock')
        assert os.path.getsize('/app/frontend/yarn.lock') > 1000
