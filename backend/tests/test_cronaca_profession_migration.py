"""Backend tests for iteration 32:

  A) Anonymous vote/data migration on upgrade (fresh signup + login onto
     existing account).
  B) New 'cronaca' category (9th category) & GET /api/categories.
  C) 3 collectible achievement badges for cronaca (curioso/cronista/segugio).
  D) `profession` field on profile — GET /api/professions + PATCH profile.

We hit the running FastAPI service through the public preview URL. A few
tests that need controlled fixtures (5+ cronaca feuds, resetting the
pre-seeded chat_a account) reach into MongoDB directly — this mirrors
what other tests in the repo already do and is documented per-fixture.
"""
import os
import asyncio
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load backend .env so MONGO_URL / DB_NAME resolve — mirrors backend startup.
load_dotenv(Path('/app/backend/.env'))

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/') if os.environ.get('EXPO_PUBLIC_BACKEND_URL') else 'https://faide-poll.preview.emergentagent.com'
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']

CHAT_A_EMAIL = 'chat_a@test.it'
CHAT_A_PASSWORD = 'test123'

# ---------- helpers ----------

def _post(path, json=None, headers=None, timeout=15):
    return requests.post(f"{BASE_URL}{path}", json=json, headers=headers, timeout=timeout)

def _get(path, headers=None, timeout=15):
    return requests.get(f"{BASE_URL}{path}", headers=headers, timeout=timeout)

def _patch(path, json=None, headers=None, timeout=15):
    return requests.patch(f"{BASE_URL}{path}", json=json, headers=headers, timeout=timeout)

def _auth(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() else asyncio.run(coro)


@pytest.fixture(scope='module')
def mongo():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


@pytest.fixture
def anon_user():
    """Create a fresh anonymous account."""
    nick = f"TEST_{uuid.uuid4().hex[:8]}"
    r = _post('/api/auth/anonymous', json={'nickname': nick})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def chat_a_reset(mongo):
    """Reset chat_a to a clean baseline BEFORE every test that uses it:
    total_votes/majority/minority=0, no badges_ever_awarded, no profession/
    onboarding. Deletes all votes and any anon user artifacts pointing at
    a hypothetical merged history."""
    async def _reset():
        await mongo.users.update_one(
            {'email': CHAT_A_EMAIL},
            {'$set': {
                'total_votes': 0, 'majority_votes': 0, 'minority_votes': 0,
                'badges_ever_awarded': [], 'current_badge': None,
                'profession': None,
                'favorite_categories': [],
                'onboarding_completed': False,
            }, '$unset': {'last_alignment_check': ''}},
        )
        u = await mongo.users.find_one({'email': CHAT_A_EMAIL}, {'_id': 0, 'user_id': 1})
        assert u, 'chat_a@test.it seed user missing'
        await mongo.votes.delete_many({'user_id': u['user_id']})
        return u['user_id']
    uid = _run(_reset())
    return uid


@pytest.fixture
def cronaca_feuds(mongo):
    """Insert 6 TEST cronaca feuds (fresh, within the 24h live window).

    The badge tier 1 unlocks at 5 votes on distinct cronaca feuds; we
    insert 6 to leave one spare for any race/duplication safety net.
    """
    async def _insert():
        ids = []
        now = datetime.now(timezone.utc)
        for i in range(6):
            fid = f"feud_TEST_cronaca_{uuid.uuid4().hex[:10]}"
            doc = {
                'feud_id': fid,
                'category': 'cronaca',
                'category_label': 'Cronaca',
                'title': f'TEST cronaca feud {i}',
                'party_a': 'A side',
                'party_b': 'B side',
                'summary': 'test summary',
                'question': 'test question',
                'image_url': None,
                'media': None,
                'sources': [],
                'engagement_score': 1,
                'engagement_reason': 'test',
                'subject': 'test',
                'hashtag_subjects': [],
                'hashtag': 'test',
                'hashtag_display': '#Test',
                'votes_a': 0, 'votes_b': 0,
                'created_at': now - timedelta(minutes=i),
                'source': 'test',
            }
            await mongo.feuds.insert_one(doc)
            ids.append(fid)
        return ids
    ids = _run(_insert())
    yield ids
    _run(mongo.feuds.delete_many({'feud_id': {'$in': ids}}))
    _run(mongo.votes.delete_many({'feud_id': {'$in': ids}}))


# ---------- B) categories ----------

class TestCategories:
    def test_categories_include_cronaca_and_are_nine(self):
        r = _get('/api/categories')
        assert r.status_code == 200, r.text
        cats = r.json().get('categories') or []
        assert len(cats) == 9, f'expected 9 categories, got {len(cats)}: {cats}'
        ids = [c['id'] for c in cats]
        assert 'cronaca' in ids
        entry = next(c for c in cats if c['id'] == 'cronaca')
        assert entry['label'] == 'Cronaca'


# ---------- D) professions & profile ----------

class TestProfessions:
    def test_professions_returns_25_strings(self):
        r = _get('/api/professions')
        assert r.status_code == 200, r.text
        pros = r.json().get('professions') or []
        assert isinstance(pros, list) and len(pros) == 25
        assert all(isinstance(p, str) and p for p in pros)
        # Spot-check the expected canonical values
        assert 'Studente/Studentessa' in pros
        assert 'Preferisco non dirlo' in pros


class TestPatchProfile:
    def _login_chat_a(self):
        r = _post('/api/auth/login', json={'email': CHAT_A_EMAIL, 'password': CHAT_A_PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()['token']

    def test_profile_accepts_valid_profession_and_persists(self, chat_a_reset):
        token = self._login_chat_a()
        body = {
            'age': 28, 'sex': 'F', 'region': 'Lazio',
            'favorite_categories': ['politica', 'gossip'],
            'profession': 'Ingegnere / Architetto',
        }
        r = _patch('/api/auth/me/profile', json=body, headers=_auth(token))
        assert r.status_code == 200, r.text
        u = r.json()['user']
        assert u['profession'] == 'Ingegnere / Architetto'
        # GET /me confirms persistence
        r2 = _get('/api/auth/me', headers=_auth(token))
        assert r2.json()['user']['profession'] == 'Ingegnere / Architetto'

    def test_profile_rejects_invalid_profession(self, chat_a_reset):
        token = self._login_chat_a()
        body = {
            'age': 28, 'sex': 'F', 'region': 'Lazio',
            'favorite_categories': ['politica'],
            'profession': 'Pippo',
        }
        r = _patch('/api/auth/me/profile', json=body, headers=_auth(token))
        assert r.status_code == 400, r.text
        assert 'Professione non valida' in r.json().get('detail', '')

    def test_profile_accepts_cronaca_favorite_category(self, chat_a_reset):
        token = self._login_chat_a()
        body = {
            'age': 30, 'sex': 'M', 'region': 'Toscana',
            'favorite_categories': ['cronaca', 'politica'],
        }
        r = _patch('/api/auth/me/profile', json=body, headers=_auth(token))
        assert r.status_code == 200, r.text
        u = r.json()['user']
        assert 'cronaca' in u['favorite_categories']

    def test_profile_backward_compat_without_profession(self, chat_a_reset):
        """A body missing `profession` must still succeed (existing users)."""
        token = self._login_chat_a()
        body = {'age': 40, 'sex': 'M', 'region': 'Piemonte',
                'favorite_categories': ['sport']}
        r = _patch('/api/auth/me/profile', json=body, headers=_auth(token))
        assert r.status_code == 200, r.text
        u = r.json()['user']
        assert u['age'] == 40

    def test_profile_profession_max_length_60(self, chat_a_reset):
        token = self._login_chat_a()
        body = {
            'age': 28, 'sex': 'F', 'region': 'Lazio',
            'favorite_categories': ['politica'],
            'profession': 'X' * 61,
        }
        r = _patch('/api/auth/me/profile', json=body, headers=_auth(token))
        # Pydantic max_length=60 → 422 (validation error)
        assert r.status_code == 422, r.text


class TestAuthMeProfileShape:
    def test_me_returns_profession_and_locked_achievements_for_fresh_user(self):
        # Create a fresh anon and inspect achievement list shape.
        r = _post('/api/auth/anonymous', json={'nickname': f'TEST_{uuid.uuid4().hex[:6]}'})
        token = r.json()['token']
        r2 = _get('/api/auth/me', headers=_auth(token))
        assert r2.status_code == 200, r2.text
        user = r2.json()['user']
        assert 'profession' in user  # field exposed even if null
        achs = user.get('achievements')
        assert isinstance(achs, list) and len(achs) == 3
        types = [a['type'] for a in achs]
        assert types == ['cronaca_curioso', 'cronaca_cronista', 'cronaca_segugio']
        assert all(a['unlocked'] is False for a in achs)
        thresholds = [a['threshold'] for a in achs]
        assert thresholds == [5, 25, 75]


# ---------- A) anonymous migration ----------

class TestAnonMigrationFreshEmail:
    """Anon → signup with a BRAND-NEW email. The anon user_id must be
    upgraded in place (auth_provider→'email') and votes preserved. On
    verify-email the account activates and total_votes remains == 1."""

    def test_anon_signup_fresh_email_preserves_vote(self, mongo, anon_user):
        anon_token = anon_user['token']
        anon_uid = anon_user['user']['user_id']

        # Cast one vote as anon (use any live feud).
        feeds = _get('/api/feuds').json().get('feuds') or []
        assert feeds, 'no live feuds — cannot exercise anon vote'
        target = feeds[0]['feud_id']
        vr = _post(f'/api/feuds/{target}/vote', json={'side': 'A'}, headers=_auth(anon_token))
        assert vr.status_code == 200, vr.text

        me = _get('/api/auth/me', headers=_auth(anon_token)).json()['user']
        assert me['total_votes'] == 1

        # Signup with a fresh email while presenting the anon token.
        fresh_email = f'test_anon_{uuid.uuid4().hex[:10]}@example.com'
        signup_res = _post('/api/auth/signup', json={
            'email': fresh_email, 'password': 'testpass123', 'nickname': 'TESTanon',
        }, headers=_auth(anon_token))
        assert signup_res.status_code == 200, signup_res.text
        assert signup_res.json().get('requires_verification') is True

        # DB spot-check: anon uid still exists, but is now an email user.
        async def _check():
            u = await mongo.users.find_one({'user_id': anon_uid}, {'_id': 0})
            return u
        u = _run(_check())
        assert u is not None, 'anon uid was deleted; expected in-place upgrade'
        assert u.get('auth_provider') == 'email'
        assert u.get('email') == fresh_email
        assert u.get('email_verified') is False
        assert u.get('total_votes') == 1  # vote preserved

        # Fetch the raw verification token from the DB (email delivery is
        # mocked out here) and verify.
        async def _get_token_hash():
            doc = await mongo.verification_tokens.find_one({'user_id': anon_uid})
            return doc
        doc = _run(_get_token_hash())
        assert doc, 'verification_tokens doc missing for upgraded user'
        # We can't recover the raw token (it's hashed) — verify via the
        # verify-email endpoint by resending & capturing: instead, use the
        # `token_hash` field to confirm freshness, then simulate verification
        # by directly marking verified through the resend endpoint's happy
        # path. To exercise the actual verify-email code path we generate a
        # NEW token via a controlled route: PATCH DB with a known raw token.
        raw = uuid.uuid4().hex + uuid.uuid4().hex
        new_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        async def _replace_token():
            await mongo.verification_tokens.update_one(
                {'user_id': anon_uid},
                {'$set': {'token_hash': new_hash,
                          'expires_at': datetime.now(timezone.utc) + timedelta(hours=1)}},
            )
        _run(_replace_token())

        vr = _post('/api/auth/verify-email', json={'token': raw})
        assert vr.status_code == 200, vr.text
        payload = vr.json()
        assert payload.get('ok') is True
        # After verify-email, total_votes remains 1 (the pre-migration count).
        me_after = payload['user']
        assert me_after['total_votes'] == 1
        assert me_after['user_id'] == anon_uid


class TestAnonMigrationToExistingAccount:
    """Anon → login with the credentials of an EXISTING verified account
    (chat_a). The anon vote should move to chat_a and the anon user_id
    should be deleted from the users collection."""

    def test_anon_vote_migrates_on_login_to_existing_user(self, mongo, chat_a_reset, anon_user):
        anon_token = anon_user['token']
        anon_uid = anon_user['user']['user_id']

        # Cast one vote as anon.
        feeds = _get('/api/feuds').json().get('feuds') or []
        assert feeds
        target = feeds[0]['feud_id']
        vr = _post(f'/api/feuds/{target}/vote', json={'side': 'B'}, headers=_auth(anon_token))
        assert vr.status_code == 200, vr.text

        # Login as chat_a WITH the anon token in the Authorization header.
        r = _post('/api/auth/login',
                  json={'email': CHAT_A_EMAIL, 'password': CHAT_A_PASSWORD},
                  headers=_auth(anon_token))
        assert r.status_code == 200, r.text
        new_token = r.json()['token']
        u = r.json()['user']
        # chat_a's total_votes must now reflect the migrated anon vote (was 0)
        assert u['total_votes'] == 1, f'expected total_votes=1 after migration, got {u["total_votes"]}'

        # The anon user_id should be deleted from the users collection.
        async def _check():
            gone = await mongo.users.find_one({'user_id': anon_uid})
            v = await mongo.votes.find_one({'user_id': chat_a_reset, 'feud_id': target})
            return gone, v
        gone, v = _run(_check())
        assert gone is None, 'anon user_id should be deleted after migration'
        assert v is not None, 'vote should be reassigned to chat_a user_id'

        # Fresh GET /me on the new token also confirms count.
        me = _get('/api/auth/me', headers=_auth(new_token)).json()['user']
        assert me['total_votes'] == 1


class TestPendingMigrationFromToken:
    """Anon → signup with an email that ALREADY belongs to an existing
    (unverified) account. Backend must NOT upgrade the anon in place —
    instead, defer via `pending_migration_from` on the verification token.
    """

    def test_pending_migration_from_stored_on_token(self, mongo, anon_user):
        anon_uid = anon_user['user']['user_id']
        anon_token = anon_user['token']

        # First create an UNVERIFIED account for a fresh email (no anon token).
        fresh_email = f'test_pending_{uuid.uuid4().hex[:8]}@example.com'
        r1 = _post('/api/auth/signup', json={
            'email': fresh_email, 'password': 'pass1234', 'nickname': 'TESTfirst',
        })
        assert r1.status_code == 200, r1.text
        existing = _run(mongo.users.find_one({'email': fresh_email}, {'_id': 0, 'user_id': 1}))
        assert existing
        existing_uid = existing['user_id']

        # Second signup: anon token + same email → should trigger pending_migration_from.
        r2 = _post('/api/auth/signup', json={
            'email': fresh_email, 'password': 'newpass9', 'nickname': 'TESTsecond',
        }, headers=_auth(anon_token))
        assert r2.status_code == 200, r2.text
        assert r2.json().get('requires_verification') is True

        # Inspect the verification token doc: pending_migration_from == anon_uid
        doc = _run(mongo.verification_tokens.find_one({'user_id': existing_uid}))
        assert doc is not None, 'verification token missing on existing account'
        assert doc.get('pending_migration_from') == anon_uid, doc


# ---------- C) achievement badge cronaca_curioso ----------

class TestCronacaAchievementBadges:
    def test_curioso_unlocks_after_5_cronaca_votes(self, mongo, chat_a_reset, cronaca_feuds):
        # Login as chat_a (freshly reset — 0 votes, no badges).
        r = _post('/api/auth/login', json={'email': CHAT_A_EMAIL, 'password': CHAT_A_PASSWORD})
        assert r.status_code == 200, r.text
        token = r.json()['token']

        # Cast 5 votes on 5 distinct cronaca feuds.
        for fid in cronaca_feuds[:5]:
            vr = _post(f'/api/feuds/{fid}/vote', json={'side': 'A'}, headers=_auth(token))
            assert vr.status_code == 200, vr.text

        # /auth/me achievements should reflect cronaca_curioso.unlocked=true
        me = _get('/api/auth/me', headers=_auth(token)).json()['user']
        curioso = next(a for a in me['achievements'] if a['type'] == 'cronaca_curioso')
        assert curioso['unlocked'] is True, me['achievements']
        # Other tiers still locked
        cronista = next(a for a in me['achievements'] if a['type'] == 'cronaca_cronista')
        segugio = next(a for a in me['achievements'] if a['type'] == 'cronaca_segugio')
        assert cronista['unlocked'] is False
        assert segugio['unlocked'] is False

        # DB spot-check: badges_ever_awarded contains cronaca_curioso.
        u = _run(mongo.users.find_one({'user_id': chat_a_reset}, {'_id': 0, 'badges_ever_awarded': 1}))
        assert 'cronaca_curioso' in (u.get('badges_ever_awarded') or [])
