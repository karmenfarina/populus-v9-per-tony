"""Backend tests for anonymous-user public profile privacy + registered-user badge payload.

Covers:
 - POST /api/auth/anonymous (setup)
 - GET  /api/users/{id}            (anonymous + registered)
 - GET  /api/users/{id}/history    (anonymous + registered)
 - POST /api/feuds/{id}/vote       (as anonymous, to ensure history still hidden)
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'http://localhost:8001').rstrip('/') + '/api'
SEEDED_REGISTERED_USER = 'user_34c2f036bb2e'  # pre-seeded, 5 maj / 7 min => bastian_contrario


@pytest.fixture(scope='module')
def api_client():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def anon_user(api_client):
    nick = f'TestAnon{int(time.time())}'
    r = api_client.post(f'{BASE_URL}/auth/anonymous', json={'nickname': nick})
    assert r.status_code == 200, r.text
    data = r.json()
    assert 'token' in data and 'user' in data
    return {'token': data['token'], 'user_id': data['user']['user_id'], 'nickname': nick}


# ------------------ Anonymous /users/{id} shape ------------------

def test_anon_public_user_shape(api_client, anon_user):
    r = api_client.get(f"{BASE_URL}/users/{anon_user['user_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {'user_id', 'nickname', 'auth_provider', 'is_anonymous'}, body
    assert body['user_id'] == anon_user['user_id']
    assert body['nickname'] == anon_user['nickname']
    assert body['auth_provider'] == 'anonymous'
    assert body['is_anonymous'] is True
    # Explicit forbidden keys
    for k in ['photos', 'bio', 'social_links', 'badge', 'total_votes',
              'majority_votes', 'minority_votes', 'primary_photo_id', 'email']:
        assert k not in body, f'forbidden key {k} leaked in anon payload'


def test_anon_public_history_empty(api_client, anon_user):
    r = api_client.get(f"{BASE_URL}/users/{anon_user['user_id']}/history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {'history': [], 'is_anonymous': True}


def test_anon_public_history_after_vote_still_empty(api_client, anon_user):
    # Find any open feud to cast a vote as this anon user.
    r = api_client.get(f'{BASE_URL}/feuds')
    assert r.status_code == 200, r.text
    feuds = r.json().get('feuds') or r.json() if isinstance(r.json(), dict) else r.json()
    if isinstance(feuds, dict):
        feuds = feuds.get('items') or feuds.get('feuds') or []
    assert isinstance(feuds, list) and len(feuds) > 0, 'no feuds available to vote on'
    feud_id = feuds[0]['feud_id']

    vote_res = api_client.post(
        f'{BASE_URL}/feuds/{feud_id}/vote',
        json={'side': 'A'},
        headers={'Authorization': f"Bearer {anon_user['token']}"},
    )
    assert vote_res.status_code == 200, vote_res.text

    # Now the anon user's own /me/history should have 1 item
    my_hist = api_client.get(
        f'{BASE_URL}/users/me/history',
        headers={'Authorization': f"Bearer {anon_user['token']}"},
    )
    assert my_hist.status_code == 200
    assert len(my_hist.json().get('history', [])) >= 1

    # Public view of the anon user's history must still be hidden
    r2 = api_client.get(f"{BASE_URL}/users/{anon_user['user_id']}/history")
    assert r2.status_code == 200
    assert r2.json() == {'history': [], 'is_anonymous': True}

    # Filter param must not bypass the guard
    for f in ('all', 'majority', 'minority'):
        rf = api_client.get(f"{BASE_URL}/users/{anon_user['user_id']}/history?filter={f}")
        assert rf.status_code == 200
        assert rf.json() == {'history': [], 'is_anonymous': True}


# ------------------ Registered /users/{id} regression ------------------

def test_registered_public_user_full_payload(api_client):
    r = api_client.get(f'{BASE_URL}/users/{SEEDED_REGISTERED_USER}')
    assert r.status_code == 200, r.text
    body = r.json()
    # Required keys present
    for k in ['user_id', 'nickname', 'auth_provider', 'is_anonymous', 'bio',
              'social_links', 'primary_photo_id', 'photos',
              'total_votes', 'majority_votes', 'minority_votes', 'badge']:
        assert k in body, f'missing key {k}'
    assert body['auth_provider'] == 'email'
    assert body['is_anonymous'] is False
    assert body['total_votes'] == 12
    assert body['majority_votes'] == 5
    assert body['minority_votes'] == 7
    # Badge unlocked, bastian_contrario (min > maj)
    assert body['badge']['unlocked'] is True
    assert body['badge']['type'] == 'bastian_contrario'
    assert body['badge']['majority'] == 5
    assert body['badge']['minority'] == 7


def test_registered_public_history_regression(api_client):
    r = api_client.get(f'{BASE_URL}/users/{SEEDED_REGISTERED_USER}/history')
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'history' in body
    # Should NOT contain is_anonymous flag for non-anonymous users
    assert 'is_anonymous' not in body
    assert isinstance(body['history'], list)
    # Seeded user has 12 votes — expect at least 1 in default 'all' filter
    assert len(body['history']) >= 1, 'expected non-empty history for seeded registered user'
    item = body['history'][0]
    for k in ['feud_id', 'title', 'side_voted', 'aligned', 'voted_at']:
        assert k in item, f'history item missing key {k}'


def test_registered_public_user_not_found(api_client):
    r = api_client.get(f'{BASE_URL}/users/user_does_not_exist_xyz')
    assert r.status_code == 404
