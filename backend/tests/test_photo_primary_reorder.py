"""Backend tests for the 'primary photo first' feature.

Coverage:
- Startup backfill log line present in backend.err.log.
- DB integrity: every user with primary_photo_id has that photo at position 0.
- Public GET /api/users/{uid} returns photos[0].photo_id == primary_photo_id.
- Swap test: create fresh user with 2 photos, set second as primary,
  verify reorder happened both via the DB and via the public endpoint.
- Idempotency: PATCH primary on the current primary must not shift positions.
"""

import os
import re
import uuid
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', '').rstrip('/')
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

assert BASE_URL, 'EXPO_PUBLIC_BACKEND_URL must be set'

CHAT_A_ID = 'user_6e65e19525d5'
CHAT_A_EMAIL = 'chat_a@test.it'
CHAT_A_PWD = 'test123'

# 1x1 transparent PNG (base64) — reused for the swap test to keep the payload tiny.
TINY_PNG = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
)


@pytest.fixture(scope='session')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='session')
def mongo():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


# ---------- (1) Startup backfill log ----------

def test_startup_backfill_log_present():
    """The startup migration must have logged its result on the most recent boot."""
    path = '/var/log/supervisor/backend.err.log'
    assert os.path.exists(path), f'{path} missing'
    with open(path, 'r', errors='ignore') as f:
        content = f.read()
    matches = re.findall(r'photo position backfill: reordered (\d+) users', content)
    assert matches, "No 'photo position backfill: reordered N users' log line found"
    # Latest match must reflect >0 users (we know DB has 5 users with primaries).
    assert int(matches[-1]) > 0, f'Latest backfill reported 0 users: {matches}'


# ---------- (2) DB integrity across ALL users ----------

def test_db_integrity_primary_at_position_zero(mongo):
    """Every user with primary_photo_id must have that photo at user_photos.position==0."""
    users = list(mongo.users.find(
        {'primary_photo_id': {'$ne': None}},
        {'_id': 0, 'user_id': 1, 'primary_photo_id': 1},
    ))
    assert users, 'No users with a primary_photo_id — cannot verify integrity'
    violations = []
    for u in users:
        pos0 = mongo.user_photos.find_one(
            {'user_id': u['user_id'], 'position': 0},
            {'_id': 0, 'photo_id': 1},
        )
        if not pos0:
            # A user with a primary_photo_id must have at least the primary in user_photos.
            # If the photo itself is missing, that's a data-integrity issue.
            photo = mongo.user_photos.find_one(
                {'user_id': u['user_id'], 'photo_id': u['primary_photo_id']}
            )
            if photo is None:
                # Skip — the primary points at a deleted photo (orphan). Not
                # something this feature can fix.
                continue
            violations.append(f"{u['user_id']}: no photo at position 0 despite having photos")
            continue
        if pos0['photo_id'] != u['primary_photo_id']:
            violations.append(
                f"{u['user_id']}: pos0={pos0['photo_id']} != primary={u['primary_photo_id']}"
            )
    assert not violations, 'Backfill integrity violations:\n' + '\n'.join(violations)


# ---------- (3) chat_a public endpoint check ----------

def test_public_user_chat_a_primary_first(api):
    r = api.get(f'{BASE_URL}/api/users/{CHAT_A_ID}')
    assert r.status_code == 200, r.text
    body = r.json()
    photos = body.get('photos') or []
    assert len(photos) >= 2, f'chat_a should have >=2 photos, got {len(photos)}'
    assert body.get('primary_photo_id'), 'chat_a missing primary_photo_id'
    assert photos[0]['photo_id'] == body['primary_photo_id'], (
        f"photos[0]={photos[0]['photo_id']} vs primary={body['primary_photo_id']}"
    )
    # Positions must be a contiguous 0..N sequence in order.
    for i, p in enumerate(photos):
        assert p.get('position') == i, f'photo {i} has position={p.get("position")}'


# ---------- (4) Swap test with a freshly created user ----------

@pytest.fixture(scope='module')
def swap_user(api, mongo):
    """Create a fresh email-verified user with 2 photos. Yields (token, user_id, [ph1, ph2])."""
    email = f'TEST_reorder_{uuid.uuid4().hex[:8]}@test.it'
    pwd = 'testreorder123'
    nick = f'reorderQA_{uuid.uuid4().hex[:4]}'
    r = api.post(f'{BASE_URL}/api/auth/signup', json={
        'email': email, 'password': pwd, 'nickname': nick,
    })
    assert r.status_code == 200, f'signup failed: {r.status_code} {r.text}'
    # Directly flip email_verified=true in the DB (bypasses email link).
    # NOTE: /auth/signup stores emails lower-cased, so match by lower().
    res = mongo.users.update_one({'email': email.lower()}, {'$set': {'email_verified': True}})
    assert res.modified_count == 1, f'Could not mark user as verified (email={email.lower()!r})'
    # Login to get the session token.
    r = api.post(f'{BASE_URL}/api/auth/login', json={'email': email.lower(), 'password': pwd})
    assert r.status_code == 200, f'login failed: {r.status_code} {r.text}'
    data = r.json()
    token = data['token']
    user_id = data['user']['user_id']
    auth_headers = {'Authorization': f'Bearer {token}'}
    # Upload 2 photos.
    photo_ids = []
    for _ in range(2):
        rp = api.post(
            f'{BASE_URL}/api/auth/me/photos',
            json={'data': TINY_PNG},
            headers=auth_headers,
        )
        assert rp.status_code == 200, f'photo upload failed: {rp.status_code} {rp.text}'
        photo_ids.append(rp.json()['photo_id'])
    yield {'token': token, 'user_id': user_id, 'photo_ids': photo_ids, 'headers': auth_headers}
    # Cleanup
    try:
        mongo.user_photos.delete_many({'user_id': user_id})
        mongo.users.delete_one({'user_id': user_id})
        mongo.user_sessions.delete_many({'user_id': user_id})
    except Exception:
        pass


def test_swap_primary_moves_photo_to_position_zero(api, mongo, swap_user):
    user_id = swap_user['user_id']
    ph1, ph2 = swap_user['photo_ids']
    # After upload, primary is the first photo (server sets primary on first upload).
    u = mongo.users.find_one({'user_id': user_id}, {'_id': 0, 'primary_photo_id': 1})
    assert u['primary_photo_id'] == ph1, f'expected primary={ph1}, got {u.get("primary_photo_id")}'
    # Baseline: ph1 at 0, ph2 at 1.
    before = list(mongo.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    assert [p['photo_id'] for p in before] == [ph1, ph2], f'baseline order wrong: {before}'
    # Swap: set ph2 as primary.
    r = api.patch(
        f'{BASE_URL}/api/auth/me/photos/{ph2}/primary',
        headers=swap_user['headers'],
    )
    assert r.status_code == 200, r.text
    assert r.json().get('primary_photo_id') == ph2
    # DB should reflect the rewrite immediately.
    after = list(mongo.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    assert [p['photo_id'] for p in after] == [ph2, ph1], (
        f'expected [{ph2}, {ph1}], got {[p["photo_id"] for p in after]}'
    )
    # Public GET must show the new primary at index 0.
    rg = api.get(f'{BASE_URL}/api/users/{user_id}')
    assert rg.status_code == 200, rg.text
    body = rg.json()
    assert body['primary_photo_id'] == ph2
    assert body['photos'][0]['photo_id'] == ph2
    assert body['photos'][1]['photo_id'] == ph1
    # Contiguous positions.
    for i, p in enumerate(body['photos']):
        assert p['position'] == i


# ---------- (5) Idempotency ----------

def test_set_primary_is_idempotent(api, mongo, swap_user):
    """Calling PATCH primary on the CURRENT primary must not shift positions."""
    user_id = swap_user['user_id']
    # State from previous test: ph2 is primary.
    u = mongo.users.find_one({'user_id': user_id}, {'_id': 0, 'primary_photo_id': 1})
    current_primary = u['primary_photo_id']
    before = list(mongo.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    before_snapshot = [(p['photo_id'], p['position']) for p in before]

    r = api.patch(
        f'{BASE_URL}/api/auth/me/photos/{current_primary}/primary',
        headers=swap_user['headers'],
    )
    assert r.status_code == 200, r.text
    assert r.json().get('primary_photo_id') == current_primary

    after = list(mongo.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    after_snapshot = [(p['photo_id'], p['position']) for p in after]
    assert before_snapshot == after_snapshot, (
        f'positions drifted on idempotent primary set: before={before_snapshot} after={after_snapshot}'
    )
    # Public endpoint stays consistent too.
    rg = api.get(f'{BASE_URL}/api/users/{user_id}')
    assert rg.status_code == 200
    body = rg.json()
    assert body['primary_photo_id'] == current_primary
    assert body['photos'][0]['photo_id'] == current_primary


# ---------- (6) chat_a idempotency smoke ----------

def test_chat_a_primary_idempotency(api, mongo):
    """PATCH primary on chat_a's current primary must not shift positions
    (real-user smoke — belt & suspenders vs the freshly-created swap_user)."""
    r = api.post(f'{BASE_URL}/api/auth/login', json={
        'email': CHAT_A_EMAIL, 'password': CHAT_A_PWD,
    })
    assert r.status_code == 200, r.text
    token = r.json()['token']
    before = list(mongo.user_photos.find(
        {'user_id': CHAT_A_ID}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    before_snapshot = [(p['photo_id'], p['position']) for p in before]
    current_primary = before_snapshot[0][0]
    r2 = api.patch(
        f'{BASE_URL}/api/auth/me/photos/{current_primary}/primary',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r2.status_code == 200, r2.text
    after = list(mongo.user_photos.find(
        {'user_id': CHAT_A_ID}, {'_id': 0, 'photo_id': 1, 'position': 1},
    ).sort('position', 1))
    after_snapshot = [(p['photo_id'], p['position']) for p in after]
    assert before_snapshot == after_snapshot, (
        f'chat_a positions drifted: {before_snapshot} -> {after_snapshot}'
    )
