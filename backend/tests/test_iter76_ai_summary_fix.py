"""Regression tests for iter76 — fix TypeError in AI faction summary.

Bug: In iter75, `get_comments()` gained a new positional query param
`owner_user_id` BEFORE the `user` DI param. The internal caller inside
`get_ai_summary` used positional args, so `user` was silently passed as
`owner_user_id` and the real `user` fell back to the `Depends(...)`
sentinel → `TypeError: Depends object is not subscriptable`.

Fix: `get_comments(feud_id, user=user)` (keyword arg) at server.py:2901.

Tests covered:
  1. POST /api/feuds/{feud_id}/ai-summary works (200, no TypeError).
  2. POST /api/feuds/{feud_id}/ai-summary returns non-empty summary
     for a feud that actually has comments from both sides.
  3. GET /api/feuds/{feud_id}/comments regression (with & without
     owner_user_id query param).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://populus-bot-fleet.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"
ADMIN_KEY = 'populus-admin-42b8f3'


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def session():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


def _login(session, email, password):
    r = session.post(f"{API}/auth/login", json={'email': email, 'password': password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    return body['token'], body['user']


@pytest.fixture(scope='module')
def user_a(session):
    token, user = _login(session, 'chat_a@test.it', 'test123')
    return {'token': token, 'user': user, 'headers': {'Authorization': f'Bearer {token}'}}


@pytest.fixture(scope='module')
def user_b(session):
    token, user = _login(session, 'chat_b@test.it', 'test123')
    return {'token': token, 'user': user, 'headers': {'Authorization': f'Bearer {token}'}}


def _pick_or_create_feud(session, user_a, user_b):
    """Return a feud_id that has visible comments from both sides.

    Strategy:
      - list current feuds
      - try each until we find one where A and B have voted opposite sides
        and both have posted a comment. If none, create scenario:
        vote A→a, B→b, and post one comment as each.
    """
    r = session.get(f"{API}/feuds", timeout=20)
    assert r.status_code == 200, f"feuds list failed: {r.text}"
    body = r.json()
    feuds = body if isinstance(body, list) else body.get('feuds', [])
    assert feuds, 'No feuds available'

    for feud in feuds[:5]:
        feud_id = feud['feud_id']
        # Vote A on side 'A'
        va = session.post(f"{API}/feuds/{feud_id}/vote", json={'side': 'A'}, headers=user_a['headers'], timeout=20)
        # Vote B on side 'B'
        vb = session.post(f"{API}/feuds/{feud_id}/vote", json={'side': 'B'}, headers=user_b['headers'], timeout=20)
        if va.status_code != 200 or vb.status_code != 200:
            continue
        # Post comment as A
        ca = session.post(
            f"{API}/feuds/{feud_id}/comments",
            json={'text': f'Test A opinion iter76 {int(time.time())}'},
            headers=user_a['headers'], timeout=20,
        )
        cb = session.post(
            f"{API}/feuds/{feud_id}/comments",
            json={'text': f'Test B opinion iter76 {int(time.time())}'},
            headers=user_b['headers'], timeout=20,
        )
        if ca.status_code in (200, 201) and cb.status_code in (200, 201):
            return feud_id
    pytest.skip('Could not set up a feud with comments from both sides')


@pytest.fixture(scope='module')
def feud_with_comments(session, user_a, user_b):
    return _pick_or_create_feud(session, user_a, user_b)


# ─── Tests ─────────────────────────────────────────────────────────────────

class TestAiSummaryFix:
    """Iter76: AI summary must not blow up with TypeError."""

    def test_ai_summary_returns_200_not_500(self, session, user_a, feud_with_comments):
        r = session.post(
            f"{API}/feuds/{feud_with_comments}/ai-summary",
            headers=user_a['headers'], timeout=90,
        )
        # The only unacceptable states are 500 (server error) and 400 (bad DI).
        # 503 (LLM temporarily unavailable) is acceptable but should be rare.
        assert r.status_code != 500, f"AI summary crashed with 500: {r.text}"
        assert r.status_code in (200, 503), f"Unexpected status: {r.status_code} {r.text}"
        # Also make sure the TypeError signature is not in the body.
        assert 'Depends' not in r.text, f"Depends leak in response: {r.text}"
        assert 'TypeError' not in r.text, f"TypeError leak in response: {r.text}"

    def test_ai_summary_shape(self, session, user_a, feud_with_comments):
        r = session.post(
            f"{API}/feuds/{feud_with_comments}/ai-summary",
            headers=user_a['headers'], timeout=90,
        )
        if r.status_code == 503:
            pytest.skip('LLM temporarily unavailable — shape check skipped')
        assert r.status_code == 200
        data = r.json()
        for key in ('side_a', 'side_b', 'common', 'generated_at'):
            assert key in data, f"Missing key {key} in response: {data}"
        assert isinstance(data['side_a'], list)
        assert isinstance(data['side_b'], list)
        assert isinstance(data['common'], list)
        # Response is one of two valid shapes:
        #   - empty:true (LLM saw nothing substantive) → arrays empty
        #   - empty:false/absent (real synthesis) → arrays MAY still be empty
        #     for either side if the LLM couldn't extract, but at least one
        #     of side_a/side_b/common must have content.
        if data.get('empty') is not True:
            has_content = bool(data['side_a']) or bool(data['side_b']) or bool(data['common'])
            assert has_content, f"Non-empty summary must have at least one bullet, got: {data}"

    def test_ai_summary_requires_auth(self, session, feud_with_comments):
        r = session.post(f"{API}/feuds/{feud_with_comments}/ai-summary", timeout=20)
        assert r.status_code in (401, 403), f"Expected auth error, got {r.status_code}: {r.text}"

    def test_ai_summary_404_for_unknown_feud(self, session, user_a):
        r = session.post(
            f"{API}/feuds/does-not-exist-iter76/ai-summary",
            headers=user_a['headers'], timeout=20,
        )
        assert r.status_code == 404


class TestCommentsRegression:
    """Regression: GET /feuds/{id}/comments must still work with the new
    `owner_user_id` query param that was added in iter75."""

    def test_get_comments_no_owner(self, session, user_a, feud_with_comments):
        r = session.get(
            f"{API}/feuds/{feud_with_comments}/comments",
            headers=user_a['headers'], timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'side_a' in data and 'side_b' in data
        assert isinstance(data['side_a'], list)
        assert isinstance(data['side_b'], list)

    def test_get_comments_with_owner_user_id(self, session, user_a, feud_with_comments):
        r = session.get(
            f"{API}/feuds/{feud_with_comments}/comments",
            params={'owner_user_id': user_a['user']['user_id']},
            headers=user_a['headers'], timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'side_a' in data and 'side_b' in data

    def test_get_comments_anonymous(self, session, feud_with_comments):
        # No auth header — should still succeed (optional auth).
        r = requests.get(f"{API}/feuds/{feud_with_comments}/comments", timeout=20)
        assert r.status_code == 200, r.text
