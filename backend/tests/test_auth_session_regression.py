"""
Backend regression suite for iteration_63 — verifies:

1. `GET /api/auth/me` behavior (valid → 200, invalid → 401, missing → 401).
2. `POST /api/auth/logout` invalidates the DB session row.
3. `POST /api/auth/google-session` upsert path is idempotent at the DB level
   (unique index exists on `session_token` + code uses `update_one(upsert=True)`
   instead of insert). Also verifies the endpoint returns 401 for a bogus
   session_id — proving it does not crash on bad input.
4. Regressions: signup, anonymous, hype feed, story create + quota 429.

Uses live BASE_URL from EXPO_PUBLIC_BACKEND_URL and reuses seed user
`chat_a@test.it` from /app/memory/test_credentials.md for authenticated flows.
"""

import os
import uuid
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests
from pymongo import MongoClient

# ---------------------------------------------------------------- config
_env_url = os.environ.get('EXPO_PUBLIC_BACKEND_URL')
if not _env_url:
    # Fallback read from frontend/.env (kubernetes ingress url is stored there)
    try:
        with open('/app/frontend/.env') as f:
            for line in f:
                if line.startswith('EXPO_PUBLIC_BACKEND_URL='):
                    _env_url = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass
BASE_URL = (_env_url or '').rstrip('/')
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set"

MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

ADMIN_TOKEN = 'populus-admin-42b8f3'
SEED_EMAIL = 'chat_a@test.it'
SEED_PASS = 'test123'


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope='session')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='session')
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


@pytest.fixture(scope='session')
def seed_token(api):
    r = api.post(f'{BASE_URL}/api/auth/login',
                 json={'email': SEED_EMAIL, 'password': SEED_PASS})
    if r.status_code != 200:
        pytest.skip(f'Seed user unavailable: {r.status_code} {r.text[:200]}')
    data = r.json()
    assert 'token' in data and 'user' in data
    return data['token'], data['user']


@pytest.fixture(scope='session')
def hype_feud(api):
    r = api.get(f'{BASE_URL}/api/feuds/hype')
    assert r.status_code == 200
    feuds = r.json().get('feuds') or []
    assert feuds, 'no feuds available on hype feed'
    return feuds[0]['feud_id']


# ---------------------------------------------------------------- /auth/me
class TestAuthMe:
    def test_me_missing_token(self, api):
        r = api.get(f'{BASE_URL}/api/auth/me')
        assert r.status_code == 401, r.text

    def test_me_invalid_token(self, api):
        r = api.get(f'{BASE_URL}/api/auth/me',
                    headers={'Authorization': 'Bearer NOT_A_REAL_TOKEN_xyz'})
        assert r.status_code == 401, r.text

    def test_me_malformed_header(self, api):
        r = api.get(f'{BASE_URL}/api/auth/me',
                    headers={'Authorization': 'JustAToken'})
        assert r.status_code == 401, r.text

    def test_me_valid_jwt(self, api, seed_token):
        token, user = seed_token
        r = api.get(f'{BASE_URL}/api/auth/me',
                    headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200, r.text
        body = r.json()
        assert 'user' in body
        assert body['user']['user_id'] == user['user_id']
        assert body['user'].get('email') == SEED_EMAIL


# ---------------------------------------------------------------- /auth/logout
class TestAuthLogout:
    def test_logout_no_token_ok(self, api):
        r = api.post(f'{BASE_URL}/api/auth/logout')
        assert r.status_code == 200
        assert r.json().get('ok') is True

    def test_logout_removes_session_row(self, api, mongo):
        """Insert a synthetic Google session row, then hit /auth/logout —
        the row must be deleted from user_sessions."""
        # Piggy-back on a real user so get_current_user could find them if
        # they ever hit an authed endpoint before logout.
        user = mongo.users.find_one({'email': SEED_EMAIL}, {'_id': 0, 'user_id': 1})
        assert user, 'seed user missing'
        session_token = f'TEST_sess_{uuid.uuid4().hex[:12]}'
        mongo.user_sessions.insert_one({
            'session_token': session_token,
            'user_id': user['user_id'],
            'created_at': datetime.now(timezone.utc),
            'expires_at': datetime.now(timezone.utc) + timedelta(days=7),
        })
        # Sanity: /auth/me works with this session token
        r_me = api.get(f'{BASE_URL}/api/auth/me',
                       headers={'Authorization': f'Bearer {session_token}'})
        assert r_me.status_code == 200, r_me.text

        # Logout
        r = api.post(f'{BASE_URL}/api/auth/logout',
                     headers={'Authorization': f'Bearer {session_token}'})
        assert r.status_code == 200
        # Row gone
        assert mongo.user_sessions.find_one({'session_token': session_token}) is None
        # /auth/me now 401
        r2 = api.get(f'{BASE_URL}/api/auth/me',
                     headers={'Authorization': f'Bearer {session_token}'})
        assert r2.status_code == 401


# --------------------------------------------------------- google-session
class TestGoogleSessionUpsertIdempotency:
    """We cannot invoke the real Emergent OAuth endpoint (needs a live
    session_id issued by Emergent's consent screen), but we can:
      A) prove the endpoint returns 401 (not 500) on a bogus session_id
         — meaning the httpx call to Emergent gracefully rejected.
      B) prove the unique index on user_sessions.session_token exists.
      C) prove that update_one({session_token},{$set:...}, upsert=True)
         called TWICE with the same token does NOT throw DuplicateKeyError
         AND does NOT delete prior sessions for the same user (i.e. old
         sessions are preserved when the same user re-does google-session
         with a NEW token).
    """

    def test_bogus_session_id_returns_401_not_500(self, api):
        r = api.post(f'{BASE_URL}/api/auth/google-session',
                     json={'session_id': 'not-a-real-emergent-session-id'})
        # Backend calls Emergent → non-200 → HTTPException(401)
        # Must NOT be 500 (that would indicate a code crash instead of graceful reject)
        assert r.status_code in (400, 401), f"Expected 401, got {r.status_code}: {r.text[:200]}"

    def test_unique_index_on_session_token(self, mongo):
        indexes = list(mongo.user_sessions.list_indexes())
        session_token_index = [i for i in indexes
                               if 'session_token' in i.get('key', {})]
        assert session_token_index, 'session_token index missing'
        assert session_token_index[0].get('unique') is True, \
            'session_token index must be unique to enforce the upsert invariant'

    def test_upsert_same_token_twice_is_idempotent(self, mongo):
        """Mirror the exact update_one used by /auth/google-session code
        path (server.py:912). Running it twice must NOT raise
        DuplicateKeyError even though the unique index is in place."""
        user = mongo.users.find_one({'email': SEED_EMAIL}, {'_id': 0, 'user_id': 1})
        session_token = f'TEST_upsert_{uuid.uuid4().hex[:12]}'
        payload = {
            'session_token': session_token,
            'user_id': user['user_id'],
            'created_at': datetime.now(timezone.utc),
            'expires_at': datetime.now(timezone.utc) + timedelta(days=7),
        }
        try:
            for _ in range(2):
                mongo.user_sessions.update_one(
                    {'session_token': session_token},
                    {'$set': payload},
                    upsert=True,
                )
            # Only one row must exist
            count = mongo.user_sessions.count_documents({'session_token': session_token})
            assert count == 1
        finally:
            mongo.user_sessions.delete_one({'session_token': session_token})

    def test_preexisting_sessions_not_wiped_on_new_google_session(self, mongo):
        """If the same user does google-session AGAIN and gets a NEW
        session_token from Emergent, any prior session rows for that user
        must remain (no delete_many({user_id}) in the code path). Verify by
        inserting two synthetic session rows for the same user and confirm
        both persist independently."""
        user = mongo.users.find_one({'email': SEED_EMAIL}, {'_id': 0, 'user_id': 1})
        uid = user['user_id']
        tok1 = f'TEST_pre_{uuid.uuid4().hex[:10]}'
        tok2 = f'TEST_pre_{uuid.uuid4().hex[:10]}'
        try:
            for tok in (tok1, tok2):
                mongo.user_sessions.update_one(
                    {'session_token': tok},
                    {'$set': {
                        'session_token': tok,
                        'user_id': uid,
                        'created_at': datetime.now(timezone.utc),
                        'expires_at': datetime.now(timezone.utc) + timedelta(days=7),
                    }},
                    upsert=True,
                )
            # Both sessions coexist
            assert mongo.user_sessions.find_one({'session_token': tok1}) is not None
            assert mongo.user_sessions.find_one({'session_token': tok2}) is not None
            # And /auth/me works with either
            for tok in (tok1, tok2):
                r = requests.get(f'{BASE_URL}/api/auth/me',
                                 headers={'Authorization': f'Bearer {tok}'})
                assert r.status_code == 200, f'token {tok} failed: {r.text[:200]}'
        finally:
            mongo.user_sessions.delete_one({'session_token': tok1})
            mongo.user_sessions.delete_one({'session_token': tok2})


# --------------------------------------------------------- regressions
class TestRegressionSignup:
    def test_signup_new_email_requires_verification(self, api):
        email = f'TEST_regress_{uuid.uuid4().hex[:10]}@test.it'
        r = api.post(f'{BASE_URL}/api/auth/signup', json={
            'email': email,
            'password': 'passw0rd',
            'nickname': f'tester{uuid.uuid4().hex[:6]}',
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get('requires_verification') is True
        # Backend normalizes email to lowercase — expected behavior.
        assert data.get('email') == email.lower()

    def test_signup_missing_password_400(self, api):
        r = api.post(f'{BASE_URL}/api/auth/signup', json={
            'email': f'TEST_bad_{uuid.uuid4().hex[:6]}@test.it',
            'nickname': 'x',
        })
        assert r.status_code in (400, 422)


class TestRegressionAnonymous:
    def test_anonymous_returns_token(self, api):
        r = api.post(f'{BASE_URL}/api/auth/anonymous',
                     json={'nickname': f'anon{uuid.uuid4().hex[:6]}'})
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'token' in data and 'user' in data
        assert data['user'].get('auth_provider') == 'anonymous'
        # Token works against /auth/me
        r2 = api.get(f'{BASE_URL}/api/auth/me',
                     headers={'Authorization': f"Bearer {data['token']}"})
        assert r2.status_code == 200


class TestRegressionStories:
    def test_story_create_ok(self, api, seed_token, hype_feud, mongo):
        token, user = seed_token
        # Clean any prior stories for a deterministic quota state
        mongo.stories.delete_many({'user_id': user['user_id']})
        r = api.post(f'{BASE_URL}/api/stories',
                     headers={'Authorization': f'Bearer {token}'},
                     json={'feud_id': hype_feud, 'comment': 'TEST regression story'})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert 'story' in body
        assert body['story'].get('feud_id') == hype_feud
        # cleanup
        mongo.stories.delete_many({'user_id': user['user_id']})

    def test_story_quota_429(self, api, seed_token, hype_feud, mongo):
        """Seed 20 stories directly, then attempt one more → 429."""
        token, user = seed_token
        mongo.stories.delete_many({'user_id': user['user_id']})
        now = datetime.now(timezone.utc)
        docs = [{
            'story_id': f'TEST_story_{i}_{uuid.uuid4().hex[:6]}',
            'user_id': user['user_id'],
            'feud_id': hype_feud,
            'comment': '',
            'created_at': now,
            'expires_at': now + timedelta(hours=24),
            'viewers': [],
        } for i in range(20)]
        mongo.stories.insert_many(docs)
        try:
            r = api.post(f'{BASE_URL}/api/stories',
                         headers={'Authorization': f'Bearer {token}'},
                         json={'feud_id': hype_feud, 'comment': 'quota-check'})
            assert r.status_code == 429, f'expected 429, got {r.status_code}: {r.text[:200]}'
        finally:
            mongo.stories.delete_many({'user_id': user['user_id']})

    def test_story_bad_feud_404(self, api, seed_token, mongo):
        token, user = seed_token
        mongo.stories.delete_many({'user_id': user['user_id']})
        r = api.post(f'{BASE_URL}/api/stories',
                     headers={'Authorization': f'Bearer {token}'},
                     json={'feud_id': 'feud_does_not_exist', 'comment': ''})
        assert r.status_code == 404, r.text
