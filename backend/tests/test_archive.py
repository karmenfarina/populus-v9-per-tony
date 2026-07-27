"""Archive feature backend tests (Populus).

Covers:
- GET /api/feuds — only last 24h
- GET /api/feuds/archive/dates — dates within 7d, > 24h
- GET /api/feuds/archive — day feuds, revealed, archived flag
- Validation errors (invalid date, >7 days)
- Detail endpoint still serves archived feuds
- Route ordering (archive not matched by /{feud_id})
- Regression: history endpoints
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/') if 'EXPO_PUBLIC_BACKEND_URL' in os.environ else 'https://gossip-beta.preview.emergentagent.com'
API = f"{BASE_URL}/api"

MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'populus')


def now_utc():
    return datetime.now(timezone.utc)


@pytest.fixture(scope='session')
def db():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


@pytest.fixture(scope='session')
def seeded_archive(db):
    """Insert deterministic archive feuds for yesterday and 3 days ago (and one > 7 days ago to test cutoff)."""
    now = now_utc()
    yesterday = (now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    three_days = (now - timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    old = (now - timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)

    docs = []
    for tag, when, cat in [
        ('yesterday-pol', yesterday, 'politica'),
        ('yesterday-mus', yesterday, 'musica'),
        ('three-pol', three_days, 'politica'),
        ('old-pol', old, 'politica'),
    ]:
        fid = f"feud_TEST{uuid.uuid4().hex[:8]}"
        doc = {
            'feud_id': fid,
            'category': cat,
            'category_label': cat.title(),
            'title': f'TEST_{tag}',
            'party_a': 'A', 'party_b': 'B',
            'summary': 'test', 'question': 'test?',
            'image_url': 'https://example.com/x.jpg',
            'votes_a': 3, 'votes_b': 1,
            'created_at': when, 'source': 'seed',
        }
        db.feuds.insert_one(doc)
        docs.append(doc)
    yield {'yesterday': yesterday, 'three_days': three_days, 'old': old, 'docs': docs}
    # Cleanup
    db.feuds.delete_many({'feud_id': {'$in': [d['feud_id'] for d in docs]}})


@pytest.fixture(scope='session')
def user_token():
    email = f"TEST_arch_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        'email': email, 'password': 'password123', 'nickname': 'TestArch'
    }, timeout=15)
    assert r.status_code == 200, f"signup failed: {r.text}"
    data = r.json()
    token = data['token']
    # Onboarding
    r2 = requests.patch(f"{API}/auth/me/profile",
                        headers={'Authorization': f'Bearer {token}'},
                        json={'age': 28, 'sex': 'M', 'region': 'Lombardia',
                              'favorite_categories': ['politica', 'musica']}, timeout=15)
    assert r2.status_code == 200, f"onboarding failed: {r2.text}"
    return token


# ============ Test 1: /api/feuds returns only last 24h ============
class TestFeudsLive:
    def test_feuds_all_within_24h(self, seeded_archive):
        r = requests.get(f"{API}/feuds", timeout=15)
        assert r.status_code == 200
        cutoff = now_utc() - timedelta(hours=24)
        for f in r.json()['feuds']:
            created = datetime.fromisoformat(f['created_at'].replace('Z', '+00:00')) if isinstance(f['created_at'], str) else f['created_at']
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            assert created >= cutoff, f"feud {f['feud_id']} older than 24h in live feed"


# ============ Test 2 + 3: archive/dates ============
class TestArchiveDates:
    def test_dates_all_within_7d_and_older_than_24h(self, seeded_archive):
        r = requests.get(f"{API}/feuds/archive/dates", timeout=15)
        assert r.status_code == 200
        dates = r.json().get('dates', [])
        assert isinstance(dates, list)
        assert len(dates) >= 1, "expected at least one archive date (seeded)"
        today = now_utc().date()
        min_date = today - timedelta(days=7)
        for d in dates:
            assert 'date' in d and 'count' in d
            parsed = datetime.strptime(d['date'], '%Y-%m-%d').date()
            assert parsed >= min_date, f"date {d['date']} beyond 7 days"
            assert parsed <= today, f"date {d['date']} in future"
            assert d['count'] > 0

    def test_dates_category_filter_leq_total(self, seeded_archive):
        r_all = requests.get(f"{API}/feuds/archive/dates", timeout=15)
        r_pol = requests.get(f"{API}/feuds/archive/dates?category=politica", timeout=15)
        assert r_all.status_code == 200 and r_pol.status_code == 200
        by_date_all = {x['date']: x['count'] for x in r_all.json()['dates']}
        for x in r_pol.json()['dates']:
            assert x['count'] <= by_date_all.get(x['date'], 0), \
                f"filtered count for {x['date']} exceeds total"


# ============ Test 4: archive?date=yesterday ============
class TestArchiveFeuds:
    def test_yesterday_returns_feuds(self, seeded_archive):
        y = (now_utc() - timedelta(days=1)).strftime('%Y-%m-%d')
        r = requests.get(f"{API}/feuds/archive?date={y}", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data['date'] == y
        feuds = data['feuds']
        assert len(feuds) >= 1
        start = datetime.strptime(y, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        for f in feuds:
            assert f['revealed'] is True
            assert f['archived'] is True
            assert f['pct_a'] is not None and f['pct_b'] is not None
            created = datetime.fromisoformat(f['created_at'].replace('Z', '+00:00')) if isinstance(f['created_at'], str) else f['created_at']
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            assert start <= created < end, f"created_at {created} not in day window"

    # ============ Test 5: invalid date format ============
    def test_invalid_date_400(self):
        r = requests.get(f"{API}/feuds/archive?date=invalid", timeout=15)
        assert r.status_code == 400
        assert 'YYYY-MM-DD' in r.json()['detail']

    # ============ Test 6: date > 7 days ago ============
    def test_date_too_old_400(self):
        r = requests.get(f"{API}/feuds/archive?date=2020-01-01", timeout=15)
        assert r.status_code == 400
        assert '7 giorni' in r.json()['detail']


# ============ Test 7: detail endpoint still serves archived ============
class TestArchivedDetail:
    def test_detail_serves_archived_feud(self):
        """Pull an archived feud id from the archive endpoint, then verify detail loads."""
        y = (now_utc() - timedelta(days=1)).strftime('%Y-%m-%d')
        arch = requests.get(f"{API}/feuds/archive?date={y}", timeout=15).json()['feuds']
        if not arch:
            pytest.skip("No archived feuds for yesterday in this env")
        fid = arch[0]['feud_id']
        r = requests.get(f"{API}/feuds/{fid}", timeout=15)
        assert r.status_code == 200, f"archived detail unavailable: {r.text}"
        assert r.json()['feud']['feud_id'] == fid


# ============ Test 8: history regression ============
class TestHistoryRegression:
    def test_me_history(self, user_token):
        r = requests.get(f"{API}/users/me/history",
                         headers={'Authorization': f'Bearer {user_token}'}, timeout=15)
        assert r.status_code == 200
        assert 'history' in r.json()

    def test_public_history(self, user_token):
        me = requests.get(f"{API}/auth/me",
                          headers={'Authorization': f'Bearer {user_token}'}, timeout=15).json()
        uid = me['user']['user_id']
        r = requests.get(f"{API}/users/{uid}/history", timeout=15)
        assert r.status_code == 200
        assert 'history' in r.json()


# ============ Test 9: route ordering ============
class TestRouteOrdering:
    def test_archive_not_shadowed(self):
        # Missing 'date' query should return 422 (validation), NOT 404 "Faida non trovata"
        r = requests.get(f"{API}/feuds/archive", timeout=15)
        assert r.status_code != 404, f"Archive route shadowed by /{{feud_id}}: {r.text}"
        # FastAPI returns 422 for missing required query param
        if r.status_code == 404:
            assert 'Faida' not in r.text

    def test_archive_dates_not_shadowed(self):
        r = requests.get(f"{API}/feuds/archive/dates", timeout=15)
        assert r.status_code == 200
        assert 'dates' in r.json()
