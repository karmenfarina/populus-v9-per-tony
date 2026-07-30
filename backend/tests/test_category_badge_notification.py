"""Backend regression tests for the new auto-notification of category badge
unlocks (Populus iter 99).

Covers the four scenarios from the review request:
  A. Silent bootstrap for existing users (>= tier reached BEFORE the fix).
  B. Fresh tier unlock triggers a `type='badge'` notification.
  C. Idempotency: subsequent comments do NOT create duplicate notifications.
  D. Category isolation: sport unlocks a sport-badge, not politica.
  E. `/api/notifications/unread-count` increments after a badge unlock.

The tests inject the bulk of comments directly into Mongo (to bypass the
moderation / rate-limit overhead) and then hit the real POST /comments
endpoint for the ONE comment that must trigger the evaluator, exactly as
the review request instructs.
"""

import os
import uuid
import time
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv('/app/backend/.env')
load_dotenv('/app/frontend/.env')

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/')
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
ADMIN_KEY = os.environ.get('ADMIN_TOKEN', 'populus-admin-42b8f3')


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope='module')
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


def _signup_and_verify(email: str, password: str, nickname: str, db) -> tuple[str, str]:
    """Signup via the real endpoint, flip `email_verified=True` and login."""
    r = requests.post(
        f"{BASE_URL}/api/auth/signup",
        json={'email': email, 'password': password, 'nickname': nickname},
        timeout=15,
    )
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    db.users.update_one({'email': email}, {'$set': {'email_verified': True}})
    r2 = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={'email': email, 'password': password},
        timeout=15,
    )
    assert r2.status_code == 200, f"login failed: {r2.status_code} {r2.text}"
    body = r2.json()
    return body['user']['user_id'], body['token']


def _get_feud_id_for_category(category: str) -> str:
    """Return an existing feud_id for the given category or create one via
    the admin endpoint."""
    r = requests.get(f"{BASE_URL}/api/feuds", params={'category': category}, timeout=15)
    assert r.status_code == 200, r.text
    feuds = r.json().get('feuds') or []
    if feuds:
        return feuds[0]['feud_id']
    r2 = requests.post(
        f"{BASE_URL}/api/admin/feuds",
        headers={'X-Admin-Key': ADMIN_KEY},
        json={
            'title': f'TEST feud {category}',
            'side_a': 'TeamA', 'side_b': 'TeamB',
            'category': category,
        },
        timeout=15,
    )
    assert r2.status_code in (200, 201), f"admin create feud failed: {r2.status_code} {r2.text}"
    return r2.json()['feud']['feud_id']


def _insert_bulk_comments(db, user_id: str, feud_id: str, nickname: str,
                          side: str, n: int) -> None:
    """Inject `n` comments straight into Mongo — bypasses moderation."""
    docs = [
        {
            'comment_id': f"cmt_{uuid.uuid4().hex[:12]}",
            'feud_id': feud_id,
            'user_id': user_id,
            'nickname': nickname,
            'side': side,
            'text': f'seed comment {i}',
            'created_at': datetime.now(timezone.utc),
        }
        for i in range(n)
    ]
    if docs:
        db.comments.insert_many(docs)


def _post_real_comment(token: str, feud_id: str, text: str) -> requests.Response:
    return requests.post(
        f"{BASE_URL}/api/feuds/{feud_id}/comments",
        headers={'Authorization': f'Bearer {token}'},
        json={'text': text},
        timeout=15,
    )


def _cast_vote(token: str, feud_id: str, side: str = 'A') -> None:
    r = requests.post(
        f"{BASE_URL}/api/feuds/{feud_id}/vote",
        headers={'Authorization': f'Bearer {token}'},
        json={'side': side},
        timeout=15,
    )
    assert r.status_code in (200, 400), f"vote failed: {r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
# Setup: one fresh user reused across scenarios A→B→C→D→E
# --------------------------------------------------------------------------- #

@pytest.fixture(scope='module')
def user_ctx(mongo):
    rand = uuid.uuid4().hex[:8]
    email = f"cat_badge_test_{rand}@test.it"
    password = 'test1234'
    nickname = f"catbadge{rand}"
    user_id, token = _signup_and_verify(email, password, nickname, mongo)

    politica_feud = _get_feud_id_for_category('politica')
    sport_feud = _get_feud_id_for_category('sport')

    _cast_vote(token, politica_feud, 'A')
    _cast_vote(token, sport_feud, 'A')

    ctx = {
        'user_id': user_id,
        'token': token,
        'nickname': nickname,
        'politica_feud': politica_feud,
        'sport_feud': sport_feud,
    }
    yield ctx

    # Teardown
    mongo.comments.delete_many({'user_id': user_id})
    mongo.notifications.delete_many({'user_id': user_id})
    mongo.votes.delete_many({'user_id': user_id})
    mongo.users.delete_one({'user_id': user_id})


# --------------------------------------------------------------------------- #
# Scenario A — Silent bootstrap
# --------------------------------------------------------------------------- #

def test_A_silent_bootstrap_marks_but_no_notification(user_ctx, mongo):
    uid = user_ctx['user_id']
    feud_id = user_ctx['politica_feud']
    nickname = user_ctx['nickname']

    user_doc = mongo.users.find_one({'user_id': uid})
    assert 'category_badges_notified' not in user_doc, \
        "user should NOT have category_badges_notified before bootstrap"
    notif_before = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})
    assert notif_before == 0

    _insert_bulk_comments(mongo, uid, feud_id, nickname, 'A', 100)

    r = _post_real_comment(user_ctx['token'], feud_id, 'first real comment')
    assert r.status_code == 200, r.text

    # Wait for the fire-and-forget evaluator
    time.sleep(2.0)

    user_doc = mongo.users.find_one({'user_id': uid})
    marked = user_doc.get('category_badges_notified') or []
    assert 'politica:1' in marked, \
        f"expected 'politica:1' marked as bootstrapped, got {marked}"

    notif_after = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})
    assert notif_after == 0, \
        f"bootstrap must NOT create retroactive badge notifications (got {notif_after})"


# --------------------------------------------------------------------------- #
# Scenario B — Fresh tier unlock post-bootstrap
# --------------------------------------------------------------------------- #

def test_B_new_tier_unlocked_creates_notification(user_ctx, mongo):
    uid = user_ctx['user_id']
    feud_id = user_ctx['politica_feud']
    nickname = user_ctx['nickname']

    # After Scenario A we're at 101 politica comments (100 seeded + 1 real).
    # Seed 149 more so the next REAL comment crosses the 250 threshold.
    _insert_bulk_comments(mongo, uid, feud_id, nickname, 'A', 149)

    r = _post_real_comment(user_ctx['token'], feud_id, 'crosses tier 2')
    assert r.status_code == 200, r.text

    time.sleep(2.5)

    user_doc = mongo.users.find_one({'user_id': uid})
    marked = user_doc.get('category_badges_notified') or []
    assert 'politica:2' in marked, f"expected 'politica:2' notified, got {marked}"

    notifs = list(mongo.notifications.find(
        {'user_id': uid, 'type': 'badge'}, {'_id': 0}))
    assert len(notifs) >= 1, "expected at least one badge notification"
    matching = [n for n in notifs
                if 'NUOVA SPILLA' in (n.get('title') or '')
                and 'livello 2 di Politica' in (n.get('body') or '')]
    assert matching, \
        f"expected NUOVA SPILLA title + 'livello 2 di Politica' body, got {notifs}"


# --------------------------------------------------------------------------- #
# Scenario C — Idempotency
# --------------------------------------------------------------------------- #

def test_C_idempotent_no_duplicate_notification(user_ctx, mongo):
    uid = user_ctx['user_id']
    feud_id = user_ctx['politica_feud']

    marked_before = set(
        mongo.users.find_one({'user_id': uid}).get('category_badges_notified') or [])
    count_before = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})

    r = _post_real_comment(user_ctx['token'], feud_id, 'idempotent check')
    assert r.status_code == 200, r.text
    time.sleep(2.0)

    marked_after = set(
        mongo.users.find_one({'user_id': uid}).get('category_badges_notified') or [])
    count_after = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})

    assert marked_after == marked_before, \
        f"category_badges_notified changed unexpectedly: {marked_before} -> {marked_after}"
    assert count_after == count_before, \
        f"duplicate notification created: {count_before} -> {count_after}"


# --------------------------------------------------------------------------- #
# Scenario D — Different category unlock
# --------------------------------------------------------------------------- #

def test_D_sport_category_isolated(user_ctx, mongo):
    uid = user_ctx['user_id']
    feud_id = user_ctx['sport_feud']
    nickname = user_ctx['nickname']

    marked_before = set(
        mongo.users.find_one({'user_id': uid}).get('category_badges_notified') or [])
    assert not any(k.startswith('sport:') for k in marked_before), \
        f"sport should not be marked yet, got {marked_before}"

    # First sport comment (count becomes 1) — nothing should fire
    r = _post_real_comment(user_ctx['token'], feud_id, 'first sport comment')
    assert r.status_code == 200
    time.sleep(1.5)

    notif_after_first = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})

    # Seed 99 more sport comments -> total 100 -> next real crosses tier 1
    _insert_bulk_comments(mongo, uid, feud_id, nickname, 'A', 99)

    r2 = _post_real_comment(user_ctx['token'], feud_id, 'sport tier1 unlock')
    assert r2.status_code == 200
    time.sleep(2.5)

    marked_after = set(
        mongo.users.find_one({'user_id': uid}).get('category_badges_notified') or [])
    assert 'sport:1' in marked_after, \
        f"expected 'sport:1' notified, got {marked_after}"

    sport_notifs = list(mongo.notifications.find(
        {'user_id': uid, 'type': 'badge'}, {'_id': 0}))
    matching = [n for n in sport_notifs
                if 'NUOVA SPILLA' in (n.get('title') or '')
                and 'livello 1 di Sport' in (n.get('body') or '')]
    assert matching, \
        f"expected 'livello 1 di Sport' notification, got {sport_notifs}"

    assert notif_after_first < len(sport_notifs), \
        "sport:1 notification should have been created only after crossing 100"


# --------------------------------------------------------------------------- #
# Scenario E — unread-count endpoint reflects the badge push
# --------------------------------------------------------------------------- #

def test_E_unread_count_reflects_badge_notifications(user_ctx, mongo):
    uid = user_ctx['user_id']
    token = user_ctx['token']

    unread_notifs = mongo.notifications.count_documents(
        {'user_id': uid, 'read': False})

    r = requests.get(
        f"{BASE_URL}/api/notifications/unread-count",
        headers={'Authorization': f'Bearer {token}'},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'count' in body, body
    assert body['count'] == unread_notifs, \
        f"unread-count mismatch: endpoint={body['count']} db={unread_notifs}"
    assert body['count'] >= 2, \
        f"expected at least 2 unread badge notifs (politica:2 + sport:1), got {body['count']}"
