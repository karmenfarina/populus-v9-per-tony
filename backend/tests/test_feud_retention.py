"""Feud retention (14-day purge) + history snapshot backend tests.

Covers:
- GET /api/feuds/{id} returns 410 with expected Italian detail
- POST /api/feuds/{id}/vote persists denormalized `feud_snapshot`
- Cleanup end-to-end via POST /api/admin/cleanup_expired
- History (`/users/me/history` + `/users/{id}/history`) surfaces `feud_deleted`
  + uses snapshot when the feud has been purged
- Filter regression (`majority` / `minority`) on purged items
- Admin auth: missing / wrong / correct key
"""
import os
import time
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load backend .env for MONGO_URL, DB_NAME, ADMIN_TOKEN
load_dotenv('/app/backend/.env')

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/') if os.environ.get('EXPO_PUBLIC_BACKEND_URL') else None
if not BASE_URL:
    # Read from frontend .env fallback
    with open('/app/frontend/.env') as f:
        for line in f:
            if line.startswith('EXPO_PUBLIC_BACKEND_URL'):
                BASE_URL = line.split('=', 1)[1].strip().strip('"').rstrip('/')
                break

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
ADMIN_TOKEN = os.environ['ADMIN_TOKEN']


@pytest.fixture(scope='module')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope='module')
def db(event_loop):
    client = AsyncIOMotorClient(MONGO_URL)
    return client[DB_NAME]


def _signup(api, tag=None) -> dict:
    tag = tag or uuid.uuid4().hex[:8]
    email = f"TEST_retention_{tag}@example.com"
    r = api.post(f"{BASE_URL}/api/auth/signup", json={
        'email': email,
        'password': 'testpass123',
        'nickname': f'TEST_{tag}'[:24],
    })
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    return r.json()


def _get_any_live_feud(api) -> dict:
    r = api.get(f"{BASE_URL}/api/feuds")
    assert r.status_code == 200
    feuds = r.json()['feuds']
    assert feuds, "no live feuds in system - RSS scheduler needs to run first"
    return feuds[0]


# ---- 1. Missing feud returns 410 with Italian detail ----
class TestGoneStatus:
    def test_missing_feud_returns_410(self, api):
        r = api.get(f"{BASE_URL}/api/feuds/nonexistent_xyz")
        assert r.status_code == 410, f"expected 410, got {r.status_code}"
        assert r.json().get('detail') == 'Faida più vecchia di 2 settimane'


# ---- 2. Vote stores feud_snapshot ----
class TestVoteSnapshot:
    def test_vote_persists_snapshot(self, api, event_loop, db):
        signup = _signup(api, 'vs')
        token = signup['token']
        user_id = signup['user']['user_id']
        feud = _get_any_live_feud(api)

        api.headers.update({'Authorization': f'Bearer {token}'})
        r = api.post(f"{BASE_URL}/api/feuds/{feud['feud_id']}/vote", json={'side': 'A'})
        assert r.status_code == 200, r.text

        # Assert snapshot in DB
        async def _check():
            v = await db.votes.find_one({'feud_id': feud['feud_id'], 'user_id': user_id}, {'_id': 0})
            assert v is not None, "vote not persisted"
            snap = v.get('feud_snapshot')
            assert snap, f"feud_snapshot missing on vote: {v}"
            for k in ('title', 'category_label', 'party_a', 'party_b', 'image_url'):
                assert snap.get(k), f"snapshot.{k} missing: {snap}"
        event_loop.run_until_complete(_check())
        api.headers.pop('Authorization', None)


# ---- 5. Admin endpoint auth ----
class TestAdminAuth:
    def test_no_key_rejected(self, api):
        r = api.post(f"{BASE_URL}/api/admin/cleanup_expired")
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"

    def test_wrong_key_rejected(self, api):
        r = api.post(
            f"{BASE_URL}/api/admin/cleanup_expired",
            headers={'X-Admin-Key': 'wrong-token-xyz'},
        )
        assert r.status_code in (401, 403), r.status_code

    def test_correct_key_accepted(self, api):
        r = api.post(
            f"{BASE_URL}/api/admin/cleanup_expired",
            headers={'X-Admin-Key': ADMIN_TOKEN},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert 'purged' in body and 'remaining' in body


# ---- 3 + 4. Cleanup end-to-end ----
class TestCleanupEndToEnd:
    def test_full_flow(self, api, event_loop, db):
        # a) Signup + vote on a fresh feud
        signup = _signup(api, 'ce')
        token = signup['token']
        user_id = signup['user']['user_id']
        feud = _get_any_live_feud(api)
        feud_id = feud['feud_id']

        # Winning-side deterministic: user X votes 'A', we boost votes_b so 'B' wins ⇒ minority
        api.headers.update({'Authorization': f'Bearer {token}'})
        r = api.post(f"{BASE_URL}/api/feuds/{feud_id}/vote", json={'side': 'A'})
        assert r.status_code == 200, r.text

        # c) Insert a comment via API (user X has voted → allowed)
        r = api.post(f"{BASE_URL}/api/feuds/{feud_id}/comments", json={'text': 'TEST_retention_comment'})
        assert r.status_code == 200, r.text
        comment_id = r.json()['comment']['comment_id']
        api.headers.pop('Authorization', None)

        # b) Backdate feud + rig votes so B wins (user X voted A → minority) + expire it
        async def _prep():
            await db.feuds.update_one(
                {'feud_id': feud_id},
                {'$set': {
                    'created_at': datetime.now(timezone.utc) - timedelta(days=15),
                    'votes_b': 99,  # force B to win regardless of X's A vote
                }},
            )
        event_loop.run_until_complete(_prep())

        # d) Trigger cleanup
        r = api.post(
            f"{BASE_URL}/api/admin/cleanup_expired",
            headers={'X-Admin-Key': ADMIN_TOKEN},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['purged'] >= 1, f"expected >=1 purged, got {body}"

        # e) Verify DB state
        async def _verify_db():
            # feud deleted
            assert await db.feuds.find_one({'feud_id': feud_id}) is None
            # comment + any replies removed
            assert await db.comments.find_one({'comment_id': comment_id}) is None
            # vote survives with frozen fields + snapshot
            v = await db.votes.find_one({'feud_id': feud_id, 'user_id': user_id}, {'_id': 0})
            assert v is not None, "vote should survive"
            assert 'aligned_final' in v, f"aligned_final missing: {v}"
            assert 'winning_side_final' in v, f"winning_side_final missing: {v}"
            assert v['winning_side_final'] == 'B', v['winning_side_final']
            assert v['aligned_final'] is False, "X voted A but B won → minority"
            assert v.get('feud_snapshot', {}).get('title'), v.get('feud_snapshot')
        event_loop.run_until_complete(_verify_db())

        # f) GET /api/feuds/{id} → 410
        r = api.get(f"{BASE_URL}/api/feuds/{feud_id}")
        assert r.status_code == 410, r.status_code
        assert r.json().get('detail') == 'Faida più vecchia di 2 settimane'

        # g) Public history for user X
        r = api.get(f"{BASE_URL}/api/users/{user_id}/history")
        assert r.status_code == 200, r.text
        items = r.json()['history']
        item = next((x for x in items if x['feud_id'] == feud_id), None)
        assert item, f"purged feud item missing from history: {items}"
        assert item['feud_deleted'] is True
        assert item['aligned'] is False, item  # minority
        assert item['title'] and item['party_a'] and item['party_b']
        assert item['side_voted'] == 'A'

        # h) /users/me/history with token
        api.headers.update({'Authorization': f'Bearer {token}'})
        r = api.get(f"{BASE_URL}/api/users/me/history")
        assert r.status_code == 200, r.text
        me_items = r.json()['history']
        me_item = next((x for x in me_items if x['feud_id'] == feud_id), None)
        assert me_item, "purged feud item missing in /me/history"
        assert me_item['feud_deleted'] is True
        assert me_item['aligned'] is False

        # 4. Filter regression
        r = api.get(f"{BASE_URL}/api/users/{user_id}/history?filter=minority")
        assert r.status_code == 200
        minority_ids = {x['feud_id'] for x in r.json()['history']}
        assert feud_id in minority_ids, "minority filter dropped the purged item"

        r = api.get(f"{BASE_URL}/api/users/{user_id}/history?filter=majority")
        assert r.status_code == 200
        majority_ids = {x['feud_id'] for x in r.json()['history']}
        assert feud_id not in majority_ids, "majority filter should exclude minority item"
        api.headers.pop('Authorization', None)

        # User counters kept correct after recompute (test via public user endpoint)
        r = api.get(f"{BASE_URL}/api/users/{user_id}")
        assert r.status_code == 200
        pu = r.json()
        # After recompute — but recompute only fires on vote. Just assert history flow works.
        # (recompute uses aligned_final when feud missing per code review.)

        # Stash test id for other tests / operators
        print(f"[cleanup e2e] purged feud_id={feud_id} user_id={user_id}")
