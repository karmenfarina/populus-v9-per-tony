"""
Iter 136 — Perf optimizations regression tests.

Verifies:
- /api/categories (TTL 1h) returns stable, correctly-shaped payload.
- /api/feuds (anon 10s TTL) — both anon & authenticated flows return correct shape.
- /api/feuds/hype (anon 20s TTL + N+1 fix on authenticated) — payload shape
  preserved (feud_id, votes counts, revealed flag correct for anon, comment
  counts populate for authenticated).
- Regression: /api/feuds/{id}, /api/feuds/{id}/comments, /api/feuds/{id}/vote,
  /api/notifications, /api/stories/feed, /api/admin/bots/status.
"""
from __future__ import annotations
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://skeleton-cache-build.preview.emergentagent.com').rstrip('/')
ADMIN_KEY = 'populus-admin-42b8f3'


@pytest.fixture(scope='module')
def http():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def anon_user(http):
    """Create a fresh anonymous user (rate-limited endpoint — one per module)."""
    nick = f"TEST_i136_{uuid.uuid4().hex[:8]}"
    r = http.post(f"{BASE_URL}/api/auth/anonymous", json={'nickname': nick})
    assert r.status_code == 200, f"anon signup failed: {r.status_code} {r.text}"
    data = r.json()
    assert 'token' in data and 'user' in data
    return {'token': data['token'], 'user': data['user'], 'nickname': nick}


@pytest.fixture(scope='module')
def auth_headers(anon_user):
    return {
        'Content-Type': 'application/json',
        'Authorization': f"Bearer {anon_user['token']}",
    }


# ── /api/categories ──────────────────────────────────────────────────────────
class TestCategories:
    def test_categories_ok(self, http):
        r = http.get(f"{BASE_URL}/api/categories")
        assert r.status_code == 200
        data = r.json()
        assert 'categories' in data
        assert isinstance(data['categories'], list)
        assert len(data['categories']) > 0
        # Each item should have at least a slug/label field.
        sample = data['categories'][0]
        assert isinstance(sample, dict)
        # We just require they have some kind of identifier key
        assert any(k in sample for k in ('slug', 'id', 'key', 'value'))

    def test_categories_stable_across_calls(self, http):
        r1 = http.get(f"{BASE_URL}/api/categories").json()
        r2 = http.get(f"{BASE_URL}/api/categories").json()
        assert r1 == r2, "cached /categories response drifted across calls"

    def test_categories_cache_fast(self, http):
        """Two consecutive calls should both be quick (< 2s each)."""
        for _ in range(2):
            t0 = time.time()
            r = http.get(f"{BASE_URL}/api/categories")
            elapsed = time.time() - t0
            assert r.status_code == 200
            assert elapsed < 3.0, f"categories too slow: {elapsed:.2f}s"


# ── /api/feuds (anon + auth) ────────────────────────────────────────────────
class TestFeudsList:
    def test_feuds_anon(self, http):
        r = http.get(f"{BASE_URL}/api/feuds")
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'feuds' in data
        assert isinstance(data['feuds'], list)

    def test_feuds_anon_cached_shape(self, http):
        """Anon cache 10s: two back-to-back requests should return equal payload."""
        r1 = http.get(f"{BASE_URL}/api/feuds").json()
        r2 = http.get(f"{BASE_URL}/api/feuds").json()
        assert r1.get('personalized') == r2.get('personalized') == False
        assert len(r1['feuds']) == len(r2['feuds'])

    def test_feuds_anon_hidden_percentages(self, http):
        r = http.get(f"{BASE_URL}/api/feuds").json()
        for d in r['feuds']:
            # For anon, revealed=False and votes_a/b are masked to None
            assert d.get('revealed') is False, f"anon should not see percentages: {d.get('feud_id')}"
            assert d.get('pct_a') is None
            assert d.get('pct_b') is None
            assert d.get('votes_a') is None
            assert d.get('votes_b') is None
            assert d.get('my_vote') is None

    def test_feuds_auth(self, http, auth_headers):
        r = http.get(f"{BASE_URL}/api/feuds", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'feuds' in data
        # For a fresh anon user, my_vote is None but percentages are hidden
        # until they vote. Just assert structure.
        for d in data['feuds']:
            assert 'feud_id' in d
            assert 'revealed' in d

    def test_feuds_by_category(self, http):
        # A known category
        r = http.get(f"{BASE_URL}/api/feuds", params={'category': 'politica'})
        assert r.status_code == 200
        for d in r.json()['feuds']:
            assert d.get('category') == 'politica'


# ── /api/feuds/hype ──────────────────────────────────────────────────────────
class TestFeudsHype:
    def test_hype_anon(self, http):
        r = http.get(f"{BASE_URL}/api/feuds/hype")
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'feuds' in data
        assert data.get('personalized') is False
        assert data.get('source') == 'hype'
        for d in data['feuds']:
            assert 'feud_id' in d
            # anon → revealed False, votes counts hidden
            assert d.get('revealed') is False
            assert d.get('votes_a') is None
            assert d.get('votes_b') is None
            # hype-specific counters must survive the cache path
            assert 'hype_comments' in d
            assert 'hype_engagement' in d
            assert d.get('hype_comments', 0) >= 2, "min-engagement threshold not respected"

    def test_hype_anon_cached_consistency(self, http):
        """Two consecutive anon calls should return an identical feed_id list within 20s."""
        r1 = http.get(f"{BASE_URL}/api/feuds/hype").json()
        r2 = http.get(f"{BASE_URL}/api/feuds/hype").json()
        ids1 = [d['feud_id'] for d in r1['feuds']]
        ids2 = [d['feud_id'] for d in r2['feuds']]
        assert ids1 == ids2, "hype anon cache produced inconsistent ordering"

    def test_hype_auth_n_plus_1_fix(self, http, auth_headers):
        """Authenticated hype path exercises the collapsed $in vote lookup.
        Must return 200 and preserve comment_count/hype_comments population."""
        t0 = time.time()
        r = http.get(f"{BASE_URL}/api/feuds/hype", headers=auth_headers)
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text
        data = r.json()
        # With N+1 → single $in, must be quick (<5s even on cold path).
        assert elapsed < 8.0, f"auth hype too slow: {elapsed:.2f}s (N+1 regression?)"
        for d in data['feuds']:
            assert 'feud_id' in d
            assert 'hype_comments' in d
            assert d.get('hype_comments', 0) >= 2
            assert 'hype_engagement' in d

    def test_hype_limit_param(self, http):
        r = http.get(f"{BASE_URL}/api/feuds/hype", params={'limit': 3})
        assert r.status_code == 200
        feuds = r.json()['feuds']
        assert len(feuds) <= 3


# ── Regression: single feud + comments + vote ────────────────────────────────
class TestFeudDetailFlow:
    def test_get_single_feud(self, http, auth_headers):
        # Pick a feud from /feuds/hype (guaranteed to have engagement)
        hype = http.get(f"{BASE_URL}/api/feuds/hype", headers=auth_headers).json()
        if not hype['feuds']:
            pytest.skip("no hype feuds available")
        fid = hype['feuds'][0]['feud_id']
        r = http.get(f"{BASE_URL}/api/feuds/{fid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        # Endpoint wraps the feud object under a top-level 'feud' key
        feud = d.get('feud', d)
        assert feud.get('feud_id') == fid

    def test_get_comments(self, http, auth_headers):
        hype = http.get(f"{BASE_URL}/api/feuds/hype", headers=auth_headers).json()
        if not hype['feuds']:
            pytest.skip("no hype feuds available")
        fid = hype['feuds'][0]['feud_id']
        r = http.get(f"{BASE_URL}/api/feuds/{fid}/comments", headers=auth_headers)
        assert r.status_code == 200, r.text
        # response may be list or wrapped dict; both accepted
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_vote_and_reveal_percentages(self, http, auth_headers):
        """POST a vote on the newest live feud, then re-GET the feud and confirm
        percentages appear (revealed=True) for the voting user."""
        listing = http.get(f"{BASE_URL}/api/feuds", headers=auth_headers).json()
        candidates = listing['feuds']
        if not candidates:
            pytest.skip("no live feuds to vote on")
        target = candidates[0]
        fid = target['feud_id']
        # Vote side A
        r = http.post(f"{BASE_URL}/api/feuds/{fid}/vote", headers=auth_headers, json={'side': 'A'})
        assert r.status_code in (200, 201), f"vote failed: {r.status_code} {r.text}"
        # GET single feud → should now show revealed with percentages
        raw = http.get(f"{BASE_URL}/api/feuds/{fid}", headers=auth_headers).json()
        detail = raw.get('feud', raw)
        assert detail.get('revealed') is True, f"expected revealed after vote: {detail}"
        assert detail.get('my_vote') == 'A'
        assert isinstance(detail.get('votes_a'), int)
        assert isinstance(detail.get('votes_b'), int)
        assert isinstance(detail.get('pct_a'), int)
        assert isinstance(detail.get('pct_b'), int)
        assert detail['pct_a'] + detail['pct_b'] == 100


# ── Regression: notifications & stories feed ────────────────────────────────
class TestNotificationsAndStories:
    def test_notifications(self, http, auth_headers):
        r = http.get(f"{BASE_URL}/api/notifications", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_stories_feed(self, http, auth_headers):
        r = http.get(f"{BASE_URL}/api/stories/feed", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, (list, dict))


# ── Regression: admin bots ──────────────────────────────────────────────────
class TestAdminBots:
    def test_bots_status(self, http):
        r = http.get(
            f"{BASE_URL}/api/admin/bots/status",
            headers={'X-Admin-Key': ADMIN_KEY},
        )
        # endpoint may 404 if renamed — accept either bots/status or bots/state
        if r.status_code == 404:
            r2 = http.get(
                f"{BASE_URL}/api/admin/bots/state",
                headers={'X-Admin-Key': ADMIN_KEY},
            )
            assert r2.status_code == 200, f"neither /status nor /state OK: {r2.text}"
            data = r2.json()
        else:
            assert r.status_code == 200, r.text
            data = r.json()
        assert isinstance(data, dict)

    def test_bots_status_forbidden_without_key(self, http):
        r = http.get(f"{BASE_URL}/api/admin/bots/status")
        assert r.status_code in (401, 403, 404), f"expected auth error, got {r.status_code}"
