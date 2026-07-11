"""Tests for the newly added 'Tech' category on Populus/Faide backend."""

import os
import random
import string
import time

import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL') or os.environ.get('EXPO_BACKEND_URL')
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL must be set in env"
BASE_URL = BASE_URL.rstrip('/')

EXPECTED_ORDER = ['politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip', 'tech']


@pytest.fixture(scope='module')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def anon_token(api):
    nick = 'TEST_tech_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = api.post(f"{BASE_URL}/api/auth/anonymous", json={'nickname': nick}, timeout=15)
    assert r.status_code == 200, f"anonymous auth failed: {r.status_code} {r.text}"
    data = r.json()
    assert 'token' in data and 'user' in data
    return data['token'], data['user']


# ---------- Categories ----------

class TestCategories:
    def test_categories_order_and_tech(self, api):
        r = api.get(f"{BASE_URL}/api/categories", timeout=15)
        assert r.status_code == 200
        cats = r.json().get('categories')
        assert isinstance(cats, list)
        ids = [c['id'] for c in cats]
        assert ids == EXPECTED_ORDER, f"unexpected order: {ids}"
        tech = next(c for c in cats if c['id'] == 'tech')
        assert tech['label'] == 'Tech'


# ---------- Feuds for tech ----------

class TestTechFeuds:
    def test_tech_feuds_returns_at_least_one(self, api):
        last = None
        for _ in range(6):
            r = api.get(f"{BASE_URL}/api/feuds", params={'category': 'tech'}, timeout=30)
            last = r
            if r.status_code == 200:
                body = r.json()
                arr = body.get('feuds') if isinstance(body, dict) else body
                if isinstance(arr, list) and len(arr) >= 1:
                    break
            time.sleep(2)
        assert last.status_code == 200, f"{last.status_code} {last.text}"
        body = last.json()
        feuds = body.get('feuds') if isinstance(body, dict) else body
        assert isinstance(feuds, list)
        assert len(feuds) >= 1, "expected at least 1 tech feud"

        f = feuds[0]
        # source should be 'ai' for the scheduler-generated feud
        assert f.get('source') == 'ai', f"expected source='ai' got {f.get('source')}"
        assert f.get('category') == 'tech'
        sources = f.get('sources') or []
        assert len(sources) >= 1 and sources[0], "sources[0] must be populated"

        image_url = f.get('image_url')
        assert image_url and image_url.startswith('http'), f"bad image_url {image_url}"

        # image must be reachable (2xx)
        img = requests.get(image_url, timeout=15, allow_redirects=True)
        assert 200 <= img.status_code < 300, f"image_url returned {img.status_code}"


# ---------- Sponsors for tech ----------

class TestTechSponsor:
    def test_tech_sponsor(self, api):
        r = api.get(f"{BASE_URL}/api/sponsors", params={'category': 'tech'}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        sponsors = body.get('sponsors') if isinstance(body, dict) else body
        assert isinstance(sponsors, list)
        assert len(sponsors) == 1, f"expected exactly 1 tech sponsor, got {len(sponsors)}: {sponsors}"
        s = sponsors[0]
        assert s.get('sponsor') == 'Amazon Prime Day'
        assert s.get('cta') == 'SCOPRI'


# ---------- Profile PATCH accepts tech ----------

class TestProfilePatchTech:
    def test_patch_profile_accepts_tech(self, api, anon_token):
        token, _ = anon_token
        headers = {'Authorization': f'Bearer {token}'}
        payload = {
            'age': 30,
            'sex': 'M',
            'region': 'Lazio',
            'favorite_categories': ['tech', 'politica'],
        }
        r = api.patch(f"{BASE_URL}/api/auth/me/profile", json=payload, headers=headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        # verify persisted via GET /auth/me
        me = api.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=15)
        assert me.status_code == 200
        me_body = me.json()
        user = me_body.get('user') if isinstance(me_body, dict) else {}
        fav = (user or {}).get('favorite_categories') or []
        assert 'tech' in fav, f"tech not in persisted favorite_categories: {fav}"

    def test_patch_profile_rejects_invalid(self, api, anon_token):
        token, _ = anon_token
        headers = {'Authorization': f'Bearer {token}'}
        payload = {
            'age': 30,
            'sex': 'M',
            'region': 'Lazio',
            'favorite_categories': ['tech', 'not_a_cat'],
        }
        r = api.patch(f"{BASE_URL}/api/auth/me/profile", json=payload, headers=headers, timeout=15)
        assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text}"


# ---------- Regression: every other category has >=1 feud ----------

class TestFeudsRegression:
    @pytest.mark.parametrize('cat', ['politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip'])
    def test_category_has_at_least_one_feud(self, api, cat):
        r = api.get(f"{BASE_URL}/api/feuds", params={'category': cat}, timeout=30)
        assert r.status_code == 200, f"{cat}: {r.status_code} {r.text}"
        body = r.json()
        feuds = body.get('feuds') if isinstance(body, dict) else body
        assert isinstance(feuds, list)
        assert len(feuds) >= 1, f"category {cat} has 0 feuds"
