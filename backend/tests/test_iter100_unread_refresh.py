"""Backend regression for iter 100 — verifica che l'endpoint
`GET /api/notifications/unread-count` restituisca il conteggio aggiornato
immediatamente dopo:

  Scenario A — voto che sblocca la spilla di allineamento `buon_senso`.
  Scenario B — commento che attraversa la soglia tier 1 (100) in una
               categoria.
  Scenario D — regressione: commenti successivi NON incrementano il
               counter finché non si attraversa un nuovo tier.
  Scenario E — bootstrap silenzioso: un utente con 100 commenti seed in DB
               DEVE avere unread-count invariato dopo il primo POST reale
               (marker `politica:1` scritto in silenzio).

Il fix frontend (setTimeout 800ms → refresh) fa affidamento sul fatto che
il backend risponda con il conteggio corretto ~1s dopo l'azione. Questo
test verifica proprio quel contratto.
"""

from __future__ import annotations

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
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope='module')
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


def _signup_and_verify(email: str, password: str, nickname: str, db):
    r = requests.post(
        f"{BASE_URL}/api/auth/signup",
        json={'email': email, 'password': password, 'nickname': nickname},
        timeout=15,
    )
    assert r.status_code == 200, f"signup: {r.status_code} {r.text}"
    db.users.update_one({'email': email}, {'$set': {'email_verified': True}})
    r2 = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={'email': email, 'password': password},
        timeout=15,
    )
    assert r2.status_code == 200, f"login: {r2.status_code} {r2.text}"
    body = r2.json()
    return body['user']['user_id'], body['token']


def _get_feud_id_for_category(category: str) -> str:
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
    assert r2.status_code in (200, 201), f"admin create feud: {r2.status_code} {r2.text}"
    return r2.json()['feud']['feud_id']


def _pick_feuds_majority_side(n: int) -> list[tuple[str, str]]:
    """Return `n` feuds each paired with the current majority side (A or B).

    Voting for the majority side keeps the user aligned with `buon_senso`.
    """
    out: list[tuple[str, str]] = []
    r = requests.get(f"{BASE_URL}/api/feuds", params={'limit': 200}, timeout=15)
    assert r.status_code == 200, r.text
    for f in r.json().get('feuds') or []:
        va = f.get('votes_a') or 0
        vb = f.get('votes_b') or 0
        # Prefer feuds with clear majority; ties → default to A
        side = 'A' if va >= vb else 'B'
        out.append((f['feud_id'], side))
        if len(out) >= n:
            break
    return out


def _unread_count(token: str) -> int:
    r = requests.get(
        f"{BASE_URL}/api/notifications/unread-count",
        headers={'Authorization': f'Bearer {token}'},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return int(r.json().get('count') or 0)


def _insert_bulk_comments(db, user_id: str, feud_id: str, nickname: str,
                          side: str, n: int) -> None:
    docs = [
        {
            'comment_id': f"cmt_{uuid.uuid4().hex[:12]}",
            'feud_id': feud_id,
            'user_id': user_id,
            'nickname': nickname,
            'side': side,
            'text': f'seed {i}',
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


def _cleanup(db, user_id: str):
    db.comments.delete_many({'user_id': user_id})
    db.notifications.delete_many({'user_id': user_id})
    db.votes.delete_many({'user_id': user_id})
    db.users.delete_one({'user_id': user_id})


# --------------------------------------------------------------------------- #
# Scenario A — voto sblocca spilla allineamento                               #
# --------------------------------------------------------------------------- #

def test_A_vote_unlocks_buon_senso_badge_and_unread_count_increments(mongo):
    rand = uuid.uuid4().hex[:8]
    email = f"voter_test_{rand}@test.it"
    nickname = f"voter{rand}"
    uid, token = _signup_and_verify(email, 'test1234', nickname, mongo)
    try:
        # 1. Initial unread-count MUST be 0.
        assert _unread_count(token) == 0

        # 2. Vote in 10 feuds on the majority side → threshold total_votes >= 10
        #    with majority>=minority ⇒ `buon_senso` unlocks.
        pairs = _pick_feuds_majority_side(10)
        assert len(pairs) >= 10, f"not enough feuds to vote on: got {len(pairs)}"
        for feud_id, side in pairs:
            r = requests.post(
                f"{BASE_URL}/api/feuds/{feud_id}/vote",
                headers={'Authorization': f'Bearer {token}'},
                json={'side': side},
                timeout=15,
            )
            assert r.status_code in (200, 400), f"vote {feud_id}: {r.status_code} {r.text}"

        # 3. Fire-and-forget task: wait a bit.
        time.sleep(2.0)

        # 4. unread-count must be >= 1
        count = _unread_count(token)
        assert count >= 1, (
            f"expected >=1 unread notif after 10 majority votes, got {count}. "
            f"Backend contract broken → frontend refresh() will still see 0."
        )

        # 5. Verify one of them is a badge notification with 'NUOVA SPILLA' /
        #    buon senso in title/body.
        notifs = list(mongo.notifications.find(
            {'user_id': uid, 'type': 'badge'}, {'_id': 0}))
        assert notifs, f"no type=badge notif emitted, got {notifs}"
        matching = [
            n for n in notifs
            if 'NUOVA SPILLA' in (n.get('title') or '')
            or 'Buon Senso' in (n.get('body') or '')
            or 'buon_senso' in (n.get('body') or '').lower()
        ]
        assert matching, f"expected NUOVA SPILLA / Buon Senso in notif, got {notifs}"
    finally:
        _cleanup(mongo, uid)


# --------------------------------------------------------------------------- #
# Scenario B — commento sblocca spilla categoria (livello 1 di Politica)      #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope='module')
def commenter_ctx(mongo):
    rand = uuid.uuid4().hex[:8]
    email = f"commenter_test_{rand}@test.it"
    nickname = f"cmnt{rand}"
    uid, token = _signup_and_verify(email, 'test1234', nickname, mongo)
    feud_id = _get_feud_id_for_category('politica')
    # Must vote before commenting (backend rule "Devi prima votare").
    requests.post(
        f"{BASE_URL}/api/feuds/{feud_id}/vote",
        headers={'Authorization': f'Bearer {token}'},
        json={'side': 'A'},
        timeout=15,
    )
    yield {'user_id': uid, 'token': token, 'nickname': nickname, 'feud_id': feud_id}
    _cleanup(mongo, uid)


def test_B_comment_unlocks_category_badge_and_unread_count_increments(
        commenter_ctx, mongo):
    uid = commenter_ctx['user_id']
    token = commenter_ctx['token']
    nickname = commenter_ctx['nickname']
    feud_id = commenter_ctx['feud_id']

    # Priming call — the evaluator has never run for this user, so the
    # FIRST endpoint hit performs the silent bootstrap (marks whatever
    # tiers are already crossed as notified, without pushing). After
    # this, the flag `category_badges_notified` exists on the user doc
    # and subsequent tier crossings will actually push a notification.
    r0 = _post_real_comment(token, feud_id, 'priming (bootstrap)')
    assert r0.status_code == 200, r0.text
    time.sleep(1.5)
    user_doc = mongo.users.find_one({'user_id': uid})
    assert 'category_badges_notified' in user_doc, (
        f"bootstrap should have written category_badges_notified, "
        f"got {user_doc}"
    )
    # No badge notif yet (user had only 1 comment ⇒ nothing to bootstrap).
    assert mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'}) == 0

    # Seed 98 more comments so the total is 99, then the next real POST
    # becomes #100 and CROSSES tier 1 (post-bootstrap → real notification).
    _insert_bulk_comments(mongo, uid, feud_id, nickname, 'A', 98)

    count_before = _unread_count(token)

    # Real comment #100 → threshold 100 crossed.
    r = _post_real_comment(token, feud_id, 'test 100')
    assert r.status_code == 200, r.text

    # Wait for the asyncio task.
    time.sleep(2.0)

    count_after = _unread_count(token)
    assert count_after >= count_before + 1, (
        f"expected unread-count to grow after tier 1 unlock: "
        f"before={count_before} after={count_after}"
    )

    # Notification body must contain 'livello 1 di Politica'.
    notifs = list(mongo.notifications.find(
        {'user_id': uid, 'type': 'badge'}, {'_id': 0}))
    matching = [
        n for n in notifs
        if 'NUOVA SPILLA' in (n.get('title') or '')
        and 'livello 1 di Politica' in (n.get('body') or '')
    ]
    assert matching, (
        f"expected 'NUOVA SPILLA' + 'livello 1 di Politica' in body, "
        f"got {notifs}"
    )


# --------------------------------------------------------------------------- #
# Scenario D — regressione: nessun duplicato per commenti in-tier             #
# --------------------------------------------------------------------------- #

def test_D_no_duplicate_notifs_between_tiers(commenter_ctx, mongo):
    uid = commenter_ctx['user_id']
    token = commenter_ctx['token']
    feud_id = commenter_ctx['feud_id']

    # After scenario B we're at 100 politica comments and 'politica:1' notified.
    count_before = _unread_count(token)
    badge_before = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})

    # Post 2 more real comments — both should stay INSIDE tier 1 (100→102).
    for i in range(2):
        r = _post_real_comment(token, feud_id, f'in-tier {i}')
        assert r.status_code == 200, r.text
        time.sleep(1.2)

    count_after = _unread_count(token)
    badge_after = mongo.notifications.count_documents(
        {'user_id': uid, 'type': 'badge'})

    assert count_after == count_before, (
        f"unread-count changed unexpectedly for in-tier comments: "
        f"{count_before} → {count_after}"
    )
    assert badge_after == badge_before, (
        f"duplicate badge notif emitted: {badge_before} → {badge_after}"
    )


# --------------------------------------------------------------------------- #
# Scenario E — bootstrap silenzioso                                           #
# --------------------------------------------------------------------------- #

def test_E_bootstrap_silent_for_preexisting_users(mongo):
    rand = uuid.uuid4().hex[:8]
    email = f"bootstrap_test_{rand}@test.it"
    nickname = f"boot{rand}"
    uid, token = _signup_and_verify(email, 'test1234', nickname, mongo)
    try:
        feud_id = _get_feud_id_for_category('politica')

        # Cast a vote first (backend requires it before commenting).
        requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/vote",
            headers={'Authorization': f'Bearer {token}'},
            json={'side': 'A'},
            timeout=15,
        )

        # Seed 100 politica comments directly in DB — no endpoint means no
        # evaluator has run yet, so `category_badges_notified` is absent.
        _insert_bulk_comments(mongo, uid, feud_id, nickname, 'A', 100)

        user_doc = mongo.users.find_one({'user_id': uid})
        assert 'category_badges_notified' not in user_doc, (
            f"expected no category_badges_notified before first real post, "
            f"got {user_doc.get('category_badges_notified')}"
        )
        assert mongo.notifications.count_documents(
            {'user_id': uid, 'type': 'badge'}) == 0

        count_before = _unread_count(token)

        # First REAL comment → evaluator triggers bootstrap: marks
        # 'politica:1' silently, NO notification, unread-count unchanged.
        r = _post_real_comment(token, feud_id, 'bootstrap trigger')
        assert r.status_code == 200, r.text

        time.sleep(2.0)

        user_doc = mongo.users.find_one({'user_id': uid})
        marked = user_doc.get('category_badges_notified') or []
        assert 'politica:1' in marked, (
            f"bootstrap should mark 'politica:1', got {marked}"
        )

        notif_after = mongo.notifications.count_documents(
            {'user_id': uid, 'type': 'badge'})
        assert notif_after == 0, (
            f"bootstrap MUST NOT emit notif, found {notif_after}"
        )
        count_after = _unread_count(token)
        assert count_after == count_before, (
            f"unread-count changed during bootstrap: "
            f"{count_before} → {count_after}"
        )
    finally:
        _cleanup(mongo, uid)
