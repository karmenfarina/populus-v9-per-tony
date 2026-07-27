"""Backend regression tests for App di faide gossip."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://populus-gossip.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


@pytest.fixture(scope='module')
def user_a(s):
    r = s.post(f"{API}/auth/anonymous", json={'nickname': f'TEST_A_{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope='module')
def user_b(s):
    r = s.post(f"{API}/auth/anonymous", json={'nickname': f'TEST_B_{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.text
    return r.json()


def auth(tok):
    return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}


# --- Categories ---
def test_categories_returns_7(s):
    r = s.get(f"{API}/categories")
    assert r.status_code == 200
    cats = r.json()['categories']
    assert len(cats) == 7
    ids = {c['id'] for c in cats}
    assert ids == {'politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip'}


# --- Feuds ---
def test_feuds_list_has_seed(s):
    r = s.get(f"{API}/feuds")
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert len(feuds) >= 7
    for f in feuds:
        assert 'pct_a' in f and 'pct_b' in f
        assert f['pct_a'] + f['pct_b'] == 100
        assert 'feud_id' in f and 'title' in f


def test_feuds_filter_by_category(s):
    r = s.get(f"{API}/feuds", params={'category': 'gossip'})
    assert r.status_code == 200
    for f in r.json()['feuds']:
        assert f['category'] == 'gossip'


# --- Auth ---
def test_anonymous_and_me(s, user_a):
    assert 'token' in user_a and 'user' in user_a
    r = s.get(f"{API}/auth/me", headers=auth(user_a['token']))
    assert r.status_code == 200
    assert r.json()['user']['user_id'] == user_a['user']['user_id']


def test_me_requires_bearer(s):
    r = s.get(f"{API}/auth/me")
    assert r.status_code == 401


def test_signup_and_login(s):
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    pwd = 'passw0rd!'
    r = s.post(f"{API}/auth/signup", json={'email': email, 'password': pwd, 'nickname': 'TEST_signup'})
    assert r.status_code == 200, r.text
    assert 'token' in r.json()
    # duplicate email
    r2 = s.post(f"{API}/auth/signup", json={'email': email, 'password': pwd, 'nickname': 'TEST_signup'})
    assert r2.status_code == 400
    # login
    r3 = s.post(f"{API}/auth/login", json={'email': email, 'password': pwd})
    assert r3.status_code == 200
    assert r3.json()['user']['email'] == email
    # wrong password
    r4 = s.post(f"{API}/auth/login", json={'email': email, 'password': 'wrong'})
    assert r4.status_code == 401


# --- Voting / Comments / Replies / Badge ---
def test_full_vote_comment_reply_badge_flow(s, user_a, user_b):
    feuds = s.get(f"{API}/feuds").json()['feuds']
    assert len(feuds) >= 5
    ta, tb = user_a['token'], user_b['token']

    # user_a votes A on first 5 feuds
    for i, f in enumerate(feuds[:5]):
        r = s.post(f"{API}/feuds/{f['feud_id']}/vote", headers=auth(ta), json={'side': 'A'})
        assert r.status_code == 200, r.text
        updated = r.json()['feud']
        assert updated['votes_a'] >= 1
        assert updated['my_vote'] == 'A'

    # double-vote prevented
    r = s.post(f"{API}/feuds/{feuds[0]['feud_id']}/vote", headers=auth(ta), json={'side': 'A'})
    assert r.status_code == 400

    # comment requires vote — user_b hasn't voted on feuds[0]
    fid = feuds[0]['feud_id']
    r = s.post(f"{API}/feuds/{fid}/comments", headers=auth(tb), json={'text': 'TEST no-vote'})
    assert r.status_code == 400

    # user_a comments — should be side A
    r = s.post(f"{API}/feuds/{fid}/comments", headers=auth(ta), json={'text': 'TEST commento A'})
    assert r.status_code == 200
    cmt = r.json()['comment']
    assert cmt['side'] == 'A'
    cid = cmt['comment_id']

    # user_b votes B then comments
    r = s.post(f"{API}/feuds/{fid}/vote", headers=auth(tb), json={'side': 'B'})
    assert r.status_code == 200
    r = s.post(f"{API}/feuds/{fid}/comments", headers=auth(tb), json={'text': 'TEST commento B'})
    assert r.status_code == 200
    assert r.json()['comment']['side'] == 'B'

    # list comments split
    r = s.get(f"{API}/feuds/{fid}/comments")
    assert r.status_code == 200
    data = r.json()
    assert 'side_a' in data and 'side_b' in data
    assert any(c['comment_id'] == cid for c in data['side_a'])
    assert all(c['side'] == 'A' for c in data['side_a'])
    assert all(c['side'] == 'B' for c in data['side_b'])

    # reply
    r = s.post(f"{API}/comments/{cid}/replies", headers=auth(tb), json={'text': 'TEST reply'})
    assert r.status_code == 200
    assert r.json()['reply']['side'] == 'B'
    r = s.get(f"{API}/comments/{cid}/replies")
    assert r.status_code == 200
    assert len(r.json()['replies']) >= 1

    # badge unlocked after 5 votes for user_a
    r = s.get(f"{API}/auth/me", headers=auth(ta))
    u = r.json()['user']
    assert u['total_votes'] >= 5
    badge = u['badge']
    assert badge['unlocked'] is True
    assert badge['type'] in ('buon_senso', 'bastian_contrario')

    # badge locked for user_b (only 1 vote)
    r = s.get(f"{API}/auth/me", headers=auth(tb))
    ub = r.json()['user']
    assert ub['badge']['unlocked'] is False
    assert ub['total_votes'] < 5


def test_get_feud_with_my_vote(s, user_a):
    feuds = s.get(f"{API}/feuds").json()['feuds']
    fid = feuds[0]['feud_id']
    r = s.get(f"{API}/feuds/{fid}", headers=auth(user_a['token']))
    assert r.status_code == 200
    assert r.json()['feud']['my_vote'] in ('A', 'B')
