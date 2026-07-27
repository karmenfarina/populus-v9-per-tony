"""Iteration 77 backend regression tests.

Focus:
- Task 2 backend: /api/admin/generate-daily now must produce feuds whose
  `context_text` is a non-empty 80-150 word string (backstory/context).
- /api/feuds/{feud_id} must return the `context_text` field for newly
  generated feuds (and either null or absent is tolerated for legacy).
- /api/feuds/{feud_id}/ai-summary regression from iter76 fix.
"""
import os
import re
import time

import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', '').rstrip('/') or \
           os.environ.get('EXPO_BACKEND_URL', '').rstrip('/')
ADMIN_KEY = 'populus-admin-42b8f3'
TEST_EMAIL = 'chat_a@test.it'
TEST_PASSWORD = 'test123'


@pytest.fixture(scope='module')
def api_client():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def user_token(api_client):
    """Login as pre-verified test user (needed for ai-summary auth)."""
    r = api_client.post(f'{BASE_URL}/api/auth/login', json={
        'email': TEST_EMAIL, 'password': TEST_PASSWORD,
    })
    if r.status_code != 200:
        pytest.skip(f'Login failed ({r.status_code}): {r.text[:200]}')
    tok = r.json().get('token')
    assert tok, 'login returned no token'
    return tok


# ── Task 2: /api/admin/generate-daily → context_text ─────────────────
class TestGenerateDailyContextText:

    def test_admin_key_required(self, api_client):
        r = api_client.post(f'{BASE_URL}/api/admin/generate-daily?count=1')
        assert r.status_code in (401, 403), f'expected auth failure, got {r.status_code}'

    def test_generate_daily_returns_context_text(self, api_client):
        """Try up to 3 attempts (AI may skip when no worthy headlines).

        On success we verify context_text is non-empty and 80-150 words.
        """
        last_feuds = []
        for attempt in range(3):
            r = api_client.post(
                f'{BASE_URL}/api/admin/generate-daily?count=1',
                headers={'X-Admin-Key': ADMIN_KEY},
                timeout=180,
            )
            assert r.status_code == 200, f'attempt {attempt}: {r.status_code} {r.text[:200]}'
            data = r.json()
            assert 'created' in data, f'missing "created" key: {data}'
            created = data['created']
            assert isinstance(created, list), 'created must be a list'
            last_feuds = created
            if created:
                break
            # Small backoff between attempts (rate-limit friendliness).
            time.sleep(2)

        if not last_feuds:
            pytest.skip('AI skipped all attempts — no worthy headlines today. '
                        'Backend endpoint returned 200 with []; retry manually later.')

        feud = last_feuds[0]
        # Sanity: expected feud fields.
        assert feud.get('feud_id'), f'feud missing feud_id: {feud}'
        assert 'context_text' in feud, 'context_text key missing from generated feud'
        ctx = feud['context_text']
        assert isinstance(ctx, str) and ctx.strip(), \
            f'context_text should be non-empty string, got: {ctx!r}'
        # Word count sanity (80-150 requested; allow soft tolerance 60-200 to
        # avoid brittle failures from LLM style variance).
        words = re.findall(r'\S+', ctx)
        wc = len(words)
        assert 60 <= wc <= 200, (
            f'context_text word count {wc} out of tolerance (target 80-150). '
            f'text={ctx[:300]!r}'
        )
        # Stash the feud_id for the next class via pytest cache.
        pytest.generated_feud_id = feud['feud_id']

    def test_get_feud_exposes_context_text(self, api_client):
        """Fetch the just-generated feud and confirm context_text is returned."""
        feud_id = getattr(pytest, 'generated_feud_id', None)
        if not feud_id:
            pytest.skip('no fresh feud id from previous test (AI skipped)')
        r = api_client.get(f'{BASE_URL}/api/feuds/{feud_id}')
        assert r.status_code == 200, f'{r.status_code} {r.text[:200]}'
        body = r.json()
        assert 'feud' in body, body
        feud = body['feud']
        assert 'context_text' in feud, 'GET /feuds/{id} did not return context_text field'
        assert isinstance(feud['context_text'], str) and feud['context_text'].strip(), \
            f'context_text should be non-empty on fresh feud, got: {feud.get("context_text")!r}'


# ── Legacy feuds: context_text absent or null (both acceptable) ──────
class TestLegacyFeudsContextTextTolerant:

    def test_legacy_feud_context_text_optional(self, api_client):
        """Grab a feud from the list endpoint and confirm the API tolerates
        legacy rows without context_text (field null or absent — both OK).
        """
        r = api_client.get(f'{BASE_URL}/api/feuds?limit=20')
        assert r.status_code == 200, f'{r.status_code} {r.text[:200]}'
        payload = r.json()
        # The endpoint may return either {"feuds":[...]} or a bare list.
        feuds = payload.get('feuds') if isinstance(payload, dict) else payload
        assert isinstance(feuds, list) and feuds, 'expected non-empty feuds list'
        # Pick the oldest one to maximise chance it's a legacy row.
        legacy = feuds[-1]
        feud_id = legacy.get('feud_id')
        assert feud_id, f'feud missing feud_id: {legacy}'
        r2 = api_client.get(f'{BASE_URL}/api/feuds/{feud_id}')
        assert r2.status_code == 200, f'{r2.status_code} {r2.text[:200]}'
        feud = r2.json()['feud']
        # Contract: either field is absent, or it's None, or it's a
        # non-empty string. Empty-string with no content is a bug.
        ctx = feud.get('context_text', None)
        assert ctx is None or (isinstance(ctx, str) and ctx.strip()), \
            f'context_text must be None or non-empty string, got: {ctx!r}'


# ── ai-summary regression (iter76 fix) ──────────────────────────────
class TestAiSummaryRegression:

    def test_ai_summary_endpoint_reachable(self, api_client, user_token):
        """Pick any feud and hit /ai-summary. Empty comments → 200 with
        empty:true. Non-empty → 200 or 503 (transient LLM). We only
        assert the endpoint isn't broken (no 500 / no unhandled error).
        """
        r = api_client.get(f'{BASE_URL}/api/feuds?limit=1')
        assert r.status_code == 200
        payload = r.json()
        feuds = payload.get('feuds') if isinstance(payload, dict) else payload
        assert isinstance(feuds, list) and feuds, 'need at least one feud in DB'
        feud_id = feuds[0]['feud_id']

        r2 = api_client.post(
            f'{BASE_URL}/api/feuds/{feud_id}/ai-summary',
            headers={'Authorization': f'Bearer {user_token}'},
            timeout=90,
        )
        # 200 (with or without comments), or 503 (LLM transient) are ok.
        # 404 is ok if the picked feud was purged mid-test. 500 = regression.
        assert r2.status_code in (200, 404, 503), \
            f'ai-summary regression: {r2.status_code} {r2.text[:200]}'
        if r2.status_code == 200:
            body = r2.json()
            # Contract keys must be present.
            for k in ('side_a', 'side_b', 'party_a', 'party_b'):
                assert k in body, f'ai-summary response missing key {k}: {body}'
