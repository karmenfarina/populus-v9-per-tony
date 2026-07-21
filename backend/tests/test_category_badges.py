"""Backend tests for the CATEGORY_BADGES endpoint.

Covers:
 - `GET /api/users/{user_id}/category_badges` returns 9 categories × 3 tiers
 - Anonymous users return the full structure with count=0 everywhere
 - Specific tier names match the product spec (politica/gossip/tech)
 - Locked tiers report expected thresholds (100/250/500)
"""

import os
import pytest
import requests

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/')

EXPECTED_CATEGORIES = {
    'politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip', 'cronaca', 'tech',
}
EXPECTED_THRESHOLDS = [100, 250, 500]

# Product-spec tier names for a few key categories.
SPEC_NAMES = {
    'politica': ['Trombato di Provincia', 'Giampiero Mughini', 'Silvio Berlusconi'],
    'gossip': ['Vrenzola Napoletana', 'Alfonso Signorini', 'Fabrizio Corona'],
    'tech': ['Smanettone', 'Tecnocrate', 'Elon Musk'],
}


@pytest.fixture(scope='module')
def registered_user_id():
    # Login pre-seeded user A (see /app/memory/test_credentials.md)
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={'email': 'chat_a@test.it', 'password': 'test123'},
        timeout=10,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()['user']['user_id']


@pytest.fixture(scope='module')
def anonymous_user_id():
    r = requests.post(
        f"{BASE_URL}/api/auth/anonymous",
        json={'nickname': 'test_anon_badges_qa'},
        timeout=10,
    )
    assert r.status_code == 200
    return r.json()['user']['user_id']


def test_category_badges_registered_shape(registered_user_id):
    r = requests.get(f"{BASE_URL}/api/users/{registered_user_id}/category_badges", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['user_id'] == registered_user_id
    badges = body['badges']
    assert isinstance(badges, list)
    assert len(badges) == 9, f"expected 9 categories, got {len(badges)}"
    seen = {b['category_id'] for b in badges}
    assert seen == EXPECTED_CATEGORIES, f"unexpected categories: {seen}"
    for cat in badges:
        assert 'color' in cat and cat['color'].startswith('#')
        assert 'icon' in cat and isinstance(cat['icon'], str)
        assert isinstance(cat['count'], int)
        assert len(cat['tiers']) == 3
        for i, tier in enumerate(cat['tiers']):
            assert tier['tier'] == i + 1
            assert tier['threshold'] == EXPECTED_THRESHOLDS[i]
            assert isinstance(tier['unlocked'], bool)
            assert tier['name'] and tier['emoji']


def test_category_badges_spec_names(registered_user_id):
    r = requests.get(f"{BASE_URL}/api/users/{registered_user_id}/category_badges", timeout=10)
    assert r.status_code == 200
    by_cat = {c['category_id']: c for c in r.json()['badges']}
    for cat_id, expected in SPEC_NAMES.items():
        actual = [t['name'] for t in by_cat[cat_id]['tiers']]
        assert actual == expected, f"{cat_id}: expected {expected}, got {actual}"


def test_category_badges_anonymous_all_zero(anonymous_user_id):
    r = requests.get(f"{BASE_URL}/api/users/{anonymous_user_id}/category_badges", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert len(body['badges']) == 9
    for cat in body['badges']:
        assert cat['count'] == 0, f"{cat['category_id']} count!=0"
        for tier in cat['tiers']:
            assert tier['unlocked'] is False


def test_category_badges_not_found():
    r = requests.get(f"{BASE_URL}/api/users/does_not_exist_xxx/category_badges", timeout=10)
    assert r.status_code == 404
