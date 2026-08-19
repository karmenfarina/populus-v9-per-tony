"""Tests for iter147:
- Task 1: cleanup verification (bot stories quota + no dup pairs)
- Task 1 regression: anti-dup + quota 1/24h enforced in _bot_create_story
- Task 3: _fanout_hot_news creates in-app notification when called on a
  feud with reset hot_notified flag
- Task 3 regression: bots and anonymous users excluded from fanout
- General regression: /api/feuds, /api/stories/feed, login page ok
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from dotenv import load_dotenv

# Load env
load_dotenv('/app/backend/.env')
load_dotenv('/app/frontend/.env')

# Add backend to path so we can import motor client
sys.path.insert(0, '/app/backend')
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/')
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'populus-admin-42b8f3')


@pytest.fixture(scope='module')
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope='module')
def db():
    cli = AsyncIOMotorClient(MONGO_URL)
    return cli[DB_NAME]


# ─────────── Task 1: cleanup verification ───────────
class TestTask1BotStoriesCleanup:
    def test_no_bot_has_more_than_one_active_story(self, db, event_loop):
        async def _run():
            now = datetime.now(timezone.utc)
            bots = await db.users.find({'is_bot': True}, {'user_id': 1, '_id': 0}).to_list(200)
            bot_ids = [b['user_id'] for b in bots]
            assert len(bot_ids) > 0, 'expected 100 bots seeded'
            pipeline = [
                {'$match': {'user_id': {'$in': bot_ids}, 'expires_at': {'$gt': now}}},
                {'$group': {'_id': '$user_id', 'n': {'$sum': 1}}},
                {'$match': {'n': {'$gt': 1}}},
            ]
            over = await db.stories.aggregate(pipeline).to_list(200)
            return over
        over = event_loop.run_until_complete(_run())
        assert len(over) == 0, f'bots with >1 active stories: {[o for o in over]}'

    def test_no_duplicate_bot_feud_active_pair(self, db, event_loop):
        async def _run():
            now = datetime.now(timezone.utc)
            bots = await db.users.find({'is_bot': True}, {'user_id': 1, '_id': 0}).to_list(200)
            bot_ids = [b['user_id'] for b in bots]
            pipeline = [
                {'$match': {'user_id': {'$in': bot_ids}, 'expires_at': {'$gt': now}}},
                {'$group': {'_id': {'u': '$user_id', 'f': '$feud_id'}, 'n': {'$sum': 1}}},
                {'$match': {'n': {'$gt': 1}}},
            ]
            return await db.stories.aggregate(pipeline).to_list(500)
        dup = event_loop.run_until_complete(_run())
        assert dup == [], f'duplicate (bot,feud) pairs found: {dup}'

    def test_active_bot_stories_within_bot_count(self, db, event_loop):
        async def _run():
            now = datetime.now(timezone.utc)
            bots = await db.users.find({'is_bot': True}, {'user_id': 1, '_id': 0}).to_list(200)
            bot_ids = [b['user_id'] for b in bots]
            total = await db.stories.count_documents(
                {'user_id': {'$in': bot_ids}, 'expires_at': {'$gt': now}}
            )
            return total, len(bot_ids)
        total, n_bots = event_loop.run_until_complete(_run())
        # At most 1 active story per bot => total must be <= n_bots
        assert total <= n_bots, f'active bot stories {total} > bots {n_bots}'


# ─────────── Task 1 regression: _bot_create_story guardrails ───────────
class TestTask1BotCreateStoryGuardrails:
    def test_anti_duplicate_same_bot_same_feud(self, db, event_loop):
        """Insert a fake bot story then call _bot_create_story: must be no-op."""
        async def _run():
            # pick any bot and any feud
            bot = await db.users.find_one({'is_bot': True}, {'_id': 0})
            feud = await db.feuds.find_one({'is_hidden': {'$ne': True}}, {'_id': 0})
            assert bot and feud
            now = datetime.now(timezone.utc)
            marker_sid = f"story_test_iter147_{uuid.uuid4().hex[:8]}"
            # Insert a live story that competes for anti-dup slot
            await db.stories.insert_one({
                'story_id': marker_sid,
                'user_id': bot['user_id'],
                'kind': 'feud',
                'feud_id': feud['feud_id'],
                'created_at': now,
                'expires_at': now + timedelta(hours=24),
                'viewers': [],
                '_iter147_test': True,
            })
            try:
                # Now call the real _bot_create_story
                import bot_engine
                if bot_engine._db is None:
                    bot_engine._db = db  # inject motor db
                from bot_engine import _bot_create_story
                before = await db.stories.count_documents(
                    {'user_id': bot['user_id'], 'feud_id': feud['feud_id'],
                     'expires_at': {'$gt': now}}
                )
                await _bot_create_story(bot, feud)
                after = await db.stories.count_documents(
                    {'user_id': bot['user_id'], 'feud_id': feud['feud_id'],
                     'expires_at': {'$gt': now}}
                )
                return before, after
            finally:
                await db.stories.delete_many({'_iter147_test': True})
        before, after = event_loop.run_until_complete(_run())
        assert after == before, f'anti-dup failed: before={before} after={after}'


# ─────────── Task 3: fanout hot news creates notifications ───────────
class TestTask3FanoutHotNews:
    def test_fanout_creates_notification_for_admin(self, db, event_loop):
        """Create a synthetic feud in a favourite category with fake
        engagement (bumped counters) and call _fanout_hot_news directly.
        Verify a hot_news notification is inserted for the admin
        (and hot_notified flag set)."""
        async def _run():
            admin = await db.users.find_one(
                {'email': 'carlofarinapayme@gmail.com'}, {'_id': 0}
            )
            assert admin, 'admin user missing'
            assert 'politica' in (admin.get('favorite_categories') or []), \
                'admin must have politica in favorites'
            now = datetime.now(timezone.utc)
            fid = f"feud_iter147_test_{uuid.uuid4().hex[:8]}"
            fake_feud = {
                'feud_id': fid,
                'title': 'ITER147_TEST hot news fanout',
                'category': 'politica',
                'party_a': 'A', 'party_b': 'B',
                'votes_a': 20, 'votes_b': 15,   # 35 votes
                'is_hidden': False,
                'created_at': now,
                'hot_notified': False,
                '_iter147_test': True,
            }
            await db.feuds.insert_one(fake_feud)
            # inject some comments to pass HOT_MIN_COMMENTS
            comment_docs = []
            for i in range(6):
                comment_docs.append({
                    'comment_id': f'c_iter147_{i}_{uuid.uuid4().hex[:6]}',
                    'feud_id': fid,
                    'user_id': admin['user_id'],
                    'text': f'test {i}',
                    'created_at': now,
                    '_iter147_test': True,
                })
            await db.comments.insert_many(comment_docs)
            # Clear any pre-existing daily lock for the admin so
            # _daily_lock returns True (fresh key).
            try:
                today = now.date().isoformat()
                await db.notification_locks.delete_many(
                    {'key': {'$regex': f':hot_news:{today}$'}}
                )
            except Exception:
                pass
            # Snapshot notifications count
            before = await db.notifications.count_documents(
                {'user_id': admin['user_id'], 'type': 'hot_news', 'feud_id': fid}
            )
            try:
                # Import & call the real fanout
                import server as srv
                await srv._fanout_hot_news(dict(fake_feud))
                # Give async ops a beat
                await asyncio.sleep(0.5)
                after = await db.notifications.count_documents(
                    {'user_id': admin['user_id'], 'type': 'hot_news', 'feud_id': fid}
                )
                # Verify flag was set on the feud
                doc = await db.feuds.find_one({'feud_id': fid}, {'_id': 0})
                return before, after, doc.get('hot_notified')
            finally:
                # Cleanup
                await db.feuds.delete_many({'_iter147_test': True})
                await db.comments.delete_many({'_iter147_test': True})
                await db.notifications.delete_many({'feud_id': fid})
        before, after, flag = event_loop.run_until_complete(_run())
        assert flag is True, f'hot_notified was not set on feud: {flag}'
        assert after == before + 1, f'expected 1 notification created (before={before}, after={after})'

    def test_fanout_excludes_bots_and_anonymous(self, db, event_loop):
        """Ensure users filter in fanout excludes is_bot and is_anonymous."""
        async def _run():
            admin = await db.users.find_one(
                {'email': 'carlofarinapayme@gmail.com'}, {'_id': 0}
            )
            now = datetime.now(timezone.utc)
            fid = f"feud_iter147_botfilter_{uuid.uuid4().hex[:8]}"
            fake_feud = {
                'feud_id': fid,
                'title': 'ITER147_TEST bot filter',
                'category': 'politica',
                'party_a': 'A', 'party_b': 'B',
                'votes_a': 20, 'votes_b': 15,
                'is_hidden': False,
                'created_at': now,
                'hot_notified': False,
                '_iter147_test': True,
            }
            await db.feuds.insert_one(fake_feud)
            for i in range(6):
                await db.comments.insert_one({
                    'comment_id': f'c_iter147bf_{i}_{uuid.uuid4().hex[:6]}',
                    'feud_id': fid,
                    'user_id': admin['user_id'],
                    'text': 'x',
                    'created_at': now,
                    '_iter147_test': True,
                })
            try:
                today = now.date().isoformat()
                await db.notification_locks.delete_many(
                    {'key': {'$regex': f':hot_news:{today}$'}}
                )
            except Exception:
                pass
            try:
                import server as srv
                await srv._fanout_hot_news(dict(fake_feud))
                await asyncio.sleep(0.5)
                # No bot should ever have a hot_news notification
                bot_notif = await db.notifications.count_documents({
                    'feud_id': fid,
                    'type': 'hot_news',
                })
                # For every notification, verify recipient is not bot/anon
                notifs = await db.notifications.find(
                    {'feud_id': fid, 'type': 'hot_news'}, {'_id': 0}
                ).to_list(500)
                bad_recipients = []
                for n in notifs:
                    u = await db.users.find_one(
                        {'user_id': n['user_id']},
                        {'_id': 0, 'is_bot': 1, 'is_anonymous': 1}
                    )
                    if u and (u.get('is_bot') or u.get('is_anonymous')):
                        bad_recipients.append(n['user_id'])
                return bot_notif, bad_recipients
            finally:
                await db.feuds.delete_many({'_iter147_test': True})
                await db.comments.delete_many({'_iter147_test': True})
                await db.notifications.delete_many({'feud_id': fid})
        total_notifs, bad = event_loop.run_until_complete(_run())
        assert bad == [], f'bots/anon received hot_news: {bad}'
        assert total_notifs >= 1, 'at least admin should have received'


# ─────────── General regression ───────────
class TestGeneralRegression:
    def test_feuds_endpoint_ok(self):
        r = requests.get(f'{BASE_URL}/api/feuds?limit=5', timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get('feuds', j) if isinstance(j, dict) else j, list) or \
            'feuds' in j or isinstance(j, list)

    def test_stories_feed_public_shape(self, db, event_loop):
        """Sanity: /api/stories/feed requires auth. Verify it 401s w/o token."""
        r = requests.get(f'{BASE_URL}/api/stories/feed', timeout=15)
        # unauthenticated → 401 (or 403). Not 500.
        assert r.status_code in (401, 403), f'unexpected: {r.status_code} {r.text[:200]}'

    def test_login_page_web_serves(self):
        # frontend is at root, no /api → served by expo
        r = requests.get(BASE_URL, timeout=15)
        assert r.status_code == 200

    def test_admin_bots_state(self):
        r = requests.get(
            f'{BASE_URL}/api/admin/bots/state',
            headers={'X-Admin-Key': ADMIN_TOKEN}, timeout=15,
        )
        assert r.status_code == 200, r.text[:200]
