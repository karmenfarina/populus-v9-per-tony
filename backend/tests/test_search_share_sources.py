"""Backend tests for iteration 3: /api/search, /api/share/{id}, and RSS-sourced feuds."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://cerchia-app.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


@pytest.fixture(scope='module')
def anon_token(s):
    r = s.post(f"{API}/auth/anonymous", json={'nickname': f'TEST_SR_{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.text
    return r.json()['token']


# --- Search endpoint ---
def test_search_empty_returns_empty(s):
    r = s.get(f"{API}/search", params={'q': ''})
    assert r.status_code == 200
    assert r.json()['feuds'] == []


def test_search_ferragni_returns_hit(s):
    r = s.get(f"{API}/search", params={'q': 'Ferragni'})
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert len(feuds) >= 1, "expected at least 1 feud matching Ferragni (seed has one)"
    assert any('ferragni' in (f.get('title', '') + ' ' + f.get('party_a', '') + ' ' + f.get('party_b', '')).lower()
               for f in feuds)


def test_search_hides_votes_when_not_voted(s, anon_token):
    r = s.get(f"{API}/search", params={'q': 'Ferragni'}, headers={'Authorization': f'Bearer {anon_token}'})
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert len(feuds) >= 1
    # anon user hasn't voted, so results should be unrevealed
    for f in feuds:
        assert f['revealed'] is False
        assert f['pct_a'] is None
        assert f['votes_a'] is None


# --- Share endpoint ---
def test_share_public_returns_revealed(s):
    # get any feud_id from the list
    r0 = s.get(f"{API}/feuds")
    assert r0.status_code == 200
    feuds = r0.json()['feuds']
    assert feuds
    fid = feuds[0]['feud_id']

    r = s.get(f"{API}/share/{fid}")  # NO auth header
    assert r.status_code == 200
    body = r.json()['feud']
    assert body['revealed'] is True
    assert body['my_vote'] is None
    assert body['pct_a'] is not None and body['pct_b'] is not None
    assert body['pct_a'] + body['pct_b'] == 100


def test_share_404_for_unknown(s):
    r = s.get(f"{API}/share/feud_deadbeef")
    assert r.status_code == 404


# --- Existing AI feud with sources (Intercettazioni) ---
def test_existing_intercettazioni_feud_has_sources(s):
    r = s.get(f"{API}/search", params={'q': 'Intercettazioni'})
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert len(feuds) >= 1, "expected 'Intercettazioni' feud to exist"
    match = next((f for f in feuds if 'intercettazioni' in f.get('title', '').lower()), feuds[0])
    sources = match.get('sources') or []
    assert len(sources) >= 1, f"expected sources array on existing AI feud, got: {match}"
    # sources must be dicts with title/link/source
    for src in sources:
        assert isinstance(src.get('title'), str) and src['title']
        assert isinstance(src.get('link'), str) and src['link'].startswith('http')
        assert isinstance(src.get('source'), str) and src['source']
    # at least one Repubblica-linked source per problem statement
    assert any('repubblica' in (src.get('link', '') + src.get('source', '')).lower() for src in sources), \
        f"expected at least one Repubblica link in sources: {sources}"


# --- Admin generate-daily produces sources ---
def test_admin_generate_daily_creates_feud_with_sources(s):
    r = s.post(f"{API}/admin/generate-daily", params={'count': 1}, timeout=90)
    assert r.status_code == 200, r.text
    created = r.json().get('created') or []
    if not created:
        pytest.skip("AI generation returned no feuds (external RSS/LLM may have failed)")
    feud = created[0]
    assert feud.get('title')
    assert feud.get('party_a') and feud.get('party_b')
    sources = feud.get('sources') or []
    assert len(sources) >= 1, f"generated feud has no sources: {feud}"
    for src in sources:
        assert isinstance(src.get('title'), str) and src['title']
        assert isinstance(src.get('link'), str) and src['link'].startswith('http')
        assert isinstance(src.get('source'), str) and src['source']


# --- Regressions: anon login, list, vote, comment ---
def test_regression_anon_home_vote_comment(s):
    tok = s.post(f"{API}/auth/anonymous", json={'nickname': f'TEST_R_{uuid.uuid4().hex[:6]}'}).json()['token']
    hdr = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}

    # list feuds (pre-vote): revealed False, votes hidden
    r = s.get(f"{API}/feuds", headers=hdr)
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert len(feuds) >= 1
    unvoted = [f for f in feuds if f.get('my_vote') is None]
    assert unvoted, "expected at least one unvoted feud for fresh user"
    for f in unvoted[:3]:
        assert f['revealed'] is False
        assert f['pct_a'] is None and f['pct_b'] is None

    fid = unvoted[0]['feud_id']

    # vote
    r = s.post(f"{API}/feuds/{fid}/vote", json={'side': 'A'}, headers=hdr)
    assert r.status_code == 200, r.text
    updated = r.json()['feud']
    assert updated['my_vote'] == 'A'
    assert updated['revealed'] is True

    # comment
    r = s.post(f"{API}/feuds/{fid}/comments", json={'text': 'TEST regression comment'}, headers=hdr)
    assert r.status_code == 200
    assert r.json()['comment']['side'] == 'A'
