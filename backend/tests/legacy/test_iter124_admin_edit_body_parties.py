"""Iteration 124 — Admin PATCH /api/feuds/{id} accepts summary/party_a/party_b.

Delta on iter123. All previous title/question/category behaviour is unchanged.
Focus: the three new fields on `AdminEditFeudBody`.

  • admin can update `summary` (article body) — persists, GET returns it
  • admin can update `party_a` — persists
  • admin can update `party_b` — persists
  • empty / whitespace-only for any of them → 400 with italian error
  • non-admin sending any of them → 403 (RBAC unchanged)
  • combined multi-field PATCH (title + summary + party_a + party_b) works
"""
import os
import time
import uuid
import jwt
import pytest
import requests
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

BASE_URL = (
    os.environ.get('EXPO_PUBLIC_BACKEND_URL')
    or os.environ.get('EXPO_BACKEND_URL')
    or 'https://feud-governance.preview.emergentagent.com'
).rstrip('/')
JWT_SECRET = os.environ['JWT_SECRET']
API = f'{BASE_URL}/api'

ADMIN_USER_ID = 'user_13f93cbd1ea9'
ADMIN_EMAIL = 'carlofarinapayme@gmail.com'
USER_A_EMAIL = 'chat_a@test.it'
USER_A_PASS = 'test123'


def _mint_jwt(user_id: str) -> str:
    return jwt.encode(
        {'sub': user_id, 'iat': int(time.time()), 'exp': int(time.time()) + 7 * 86400},
        JWT_SECRET, algorithm='HS256',
    )


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def admin_headers():
    return {'Authorization': f'Bearer {_mint_jwt(ADMIN_USER_ID)}', 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def user_a_headers():
    r = requests.post(f'{API}/auth/login', json={'email': USER_A_EMAIL, 'password': USER_A_PASS}, timeout=15)
    assert r.status_code == 200, f'Login user A failed: {r.status_code} {r.text}'
    return {'Authorization': f"Bearer {r.json()['token']}", 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def anon_headers():
    r = requests.post(
        f'{API}/auth/anonymous',
        json={'nickname': f'anon{uuid.uuid4().hex[:6]}', 'device_id': f'dev_{uuid.uuid4().hex[:10]}'},
        timeout=15,
    )
    assert r.status_code == 200
    return {'Authorization': f"Bearer {r.json()['token']}", 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def sample_feud(admin_headers):
    """Grab an existing live feud and snapshot its original fields so we can
    restore them after the whole test module runs (avoid polluting prod-like data)."""
    r = requests.get(f'{API}/feuds?limit=10', timeout=15)
    assert r.status_code == 200
    feuds = r.json().get('feuds', [])
    assert feuds, 'No live feuds available for testing'
    fid = feuds[0]['feud_id']
    detail = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
    assert detail.status_code == 200, detail.text
    original = detail.json()['feud']
    yield {'feud_id': fid, 'original': original}
    # Restore original values (best-effort).
    restore = {
        'title': original.get('title'),
        'question': original.get('question') or original.get('title'),
        'category': original.get('category'),
    }
    if original.get('summary') is not None:
        restore['summary'] = original.get('summary')
    if original.get('party_a') is not None:
        restore['party_a'] = original.get('party_a')
    if original.get('party_b') is not None:
        restore['party_b'] = original.get('party_b')
    # Only send non-None fields
    restore = {k: v for k, v in restore.items() if v is not None and str(v).strip() != ''}
    if restore:
        try:
            requests.patch(f'{API}/feuds/{fid}', headers=admin_headers, json=restore, timeout=15)
        except Exception:
            pass


# ─── Auth sanity ────────────────────────────────────────────────────────────

class TestAuthSanity:
    def test_admin_token_resolves_to_founder_email(self, admin_headers):
        r = requests.get(f'{API}/auth/me', headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()['user']['email'].lower() == ADMIN_EMAIL


# ─── Positive: single field updates persist ─────────────────────────────────

class TestAdminEditNewFields:
    def test_edit_summary_persists(self, admin_headers, sample_feud):
        fid = sample_feud['feud_id']
        new_summary = f'TEST_SUMMARY body text {uuid.uuid4().hex[:8]}\n\nSecond paragraph con testo più lungo.'
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json={'summary': new_summary}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()['feud']
        assert body.get('summary') == new_summary
        assert body.get('edited_by') == ADMIN_USER_ID
        assert body.get('edited_at')

        # Verify persisted via GET
        r2 = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        assert r2.json()['feud'].get('summary') == new_summary

    def test_edit_party_a_persists(self, admin_headers, sample_feud):
        fid = sample_feud['feud_id']
        new_a = f'TEST_PARTY_A_{uuid.uuid4().hex[:6]}'
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json={'party_a': new_a}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()['feud'].get('party_a') == new_a

        r2 = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        assert r2.json()['feud'].get('party_a') == new_a

    def test_edit_party_b_persists(self, admin_headers, sample_feud):
        fid = sample_feud['feud_id']
        new_b = f'TEST_PARTY_B_{uuid.uuid4().hex[:6]}'
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json={'party_b': new_b}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()['feud'].get('party_b') == new_b

        r2 = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        assert r2.json()['feud'].get('party_b') == new_b

    def test_edit_summary_trimmed_and_capped(self, admin_headers, sample_feud):
        """Whitespace around the value must be trimmed, and >6000 chars capped."""
        fid = sample_feud['feud_id']
        raw = '  padded summary body ' + ('x' * 100) + '   '
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json={'summary': raw}, timeout=15)
        assert r.status_code == 200, r.text
        got = r.json()['feud'].get('summary')
        assert got == raw.strip()

        # Test capping at 6000
        big = 'A' * 6500
        r2 = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                            json={'summary': big}, timeout=15)
        assert r2.status_code == 200
        assert len(r2.json()['feud'].get('summary') or '') == 6000

    def test_edit_party_trimmed_and_capped(self, admin_headers, sample_feud):
        fid = sample_feud['feud_id']
        # trim
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json={'party_a': '  Squadra Alfa  '}, timeout=15)
        assert r.status_code == 200
        assert r.json()['feud'].get('party_a') == 'Squadra Alfa'
        # cap at 80
        long_name = 'Z' * 120
        r2 = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                            json={'party_b': long_name}, timeout=15)
        assert r2.status_code == 200
        assert len(r2.json()['feud'].get('party_b') or '') == 80


# ─── Negative: empty / whitespace / RBAC ────────────────────────────────────

class TestAdminEditNewFieldsValidation:
    @pytest.mark.parametrize('field,expected_error', [
        ('summary', 'Il testo non può essere vuoto'),
        ('party_a', 'La fazione A non può essere vuota'),
        ('party_b', 'La fazione B non può essere vuota'),
    ])
    def test_empty_string_rejected(self, admin_headers, sample_feud, field, expected_error):
        r = requests.patch(f"{API}/feuds/{sample_feud['feud_id']}",
                           headers=admin_headers, json={field: ''}, timeout=15)
        assert r.status_code == 400, f'{field} empty: expected 400, got {r.status_code} {r.text}'
        detail = (r.json() or {}).get('detail', '')
        assert expected_error in detail, f'expected italian msg for {field}: {detail!r}'

    @pytest.mark.parametrize('field,expected_error', [
        ('summary', 'Il testo non può essere vuoto'),
        ('party_a', 'La fazione A non può essere vuota'),
        ('party_b', 'La fazione B non può essere vuota'),
    ])
    def test_whitespace_only_rejected(self, admin_headers, sample_feud, field, expected_error):
        r = requests.patch(f"{API}/feuds/{sample_feud['feud_id']}",
                           headers=admin_headers, json={field: '   \n\t  '}, timeout=15)
        assert r.status_code == 400, f'{field} ws: expected 400, got {r.status_code} {r.text}'
        detail = (r.json() or {}).get('detail', '')
        assert expected_error in detail

    @pytest.mark.parametrize('field', ['summary', 'party_a', 'party_b'])
    def test_non_admin_forbidden_on_new_fields(self, user_a_headers, sample_feud, field):
        r = requests.patch(f"{API}/feuds/{sample_feud['feud_id']}",
                           headers=user_a_headers, json={field: 'hack'}, timeout=15)
        assert r.status_code == 403, f'{field}: expected 403 for non-admin, got {r.status_code}'

    @pytest.mark.parametrize('field', ['summary', 'party_a', 'party_b'])
    def test_anon_forbidden_on_new_fields(self, anon_headers, sample_feud, field):
        r = requests.patch(f"{API}/feuds/{sample_feud['feud_id']}",
                           headers=anon_headers, json={field: 'hack'}, timeout=15)
        assert r.status_code == 403

    @pytest.mark.parametrize('field', ['summary', 'party_a', 'party_b'])
    def test_unauth_rejected_on_new_fields(self, sample_feud, field):
        r = requests.patch(f"{API}/feuds/{sample_feud['feud_id']}",
                           json={field: 'hack'}, timeout=15)
        assert r.status_code in (401, 403)


# ─── Combined multi-field update ────────────────────────────────────────────

class TestCombinedMultiFieldPatch:
    def test_title_summary_party_a_party_b_together(self, admin_headers, sample_feud):
        fid = sample_feud['feud_id']
        # snapshot pre-values so we can compare
        pre = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15).json()['feud']

        stamp = uuid.uuid4().hex[:6]
        payload = {
            'title': f'TEST_COMBO_TITLE_{stamp}',
            'summary': f'TEST_COMBO_SUMMARY_{stamp}\n\nParagrafo due.',
            'party_a': f'TEST_A_{stamp}',
            'party_b': f'TEST_B_{stamp}',
        }
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()['feud']
        for k, v in payload.items():
            assert body.get(k) == v, f'{k} mismatch in PATCH response: {body.get(k)!r}'
        assert body.get('edited_by') == ADMIN_USER_ID
        assert body.get('edited_at')

        # GET verifies persistence
        r2 = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        got = r2.json()['feud']
        for k, v in payload.items():
            assert got.get(k) == v, f'{k} not persisted (GET): {got.get(k)!r}'

        # Untouched field (question) preserved
        assert got.get('question') == pre.get('question')

    def test_partial_bad_field_rolls_back(self, admin_headers, sample_feud):
        """If any single field validation fails, the whole PATCH must fail (400)
        and none of the values should have been written."""
        fid = sample_feud['feud_id']
        pre = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15).json()['feud']

        stamp = uuid.uuid4().hex[:6]
        payload = {
            'title': f'SHOULD_NOT_STICK_{stamp}',
            'summary': '   ',  # invalid -> should reject entire request
        }
        r = requests.patch(f'{API}/feuds/{fid}', headers=admin_headers,
                           json=payload, timeout=15)
        assert r.status_code == 400, f'expected 400, got {r.status_code} {r.text}'

        post = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15).json()['feud']
        assert post.get('title') == pre.get('title'), 'title should not have been updated on validation failure'
        assert post.get('summary') == pre.get('summary')
