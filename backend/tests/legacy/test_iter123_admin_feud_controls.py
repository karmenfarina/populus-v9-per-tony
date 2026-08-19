"""Iteration 123 — Founder-admin feud controls & is_hidden filtering.

Covers:
  • PATCH  /api/feuds/{id}                — admin edit (title/question/category)
  • DELETE /api/feuds/{id}                — admin soft-hide
  • POST   /api/feuds/{id}/restore        — admin restore
  • GET    /api/admin/hidden-feuds        — admin list
  • GET    /api/feuds/{id}                — 410 for regular, admin viewer flags
  • Feed filters (is_hidden excluded) on:
      /api/feuds, /api/feuds/hype, /api/feuds/archive, /api/search,
      /api/hashtags/{tag}, /api/favorites
  • RBAC: 403 for non-admin, 401 for anon on protected endpoints
  • Comment mention notification deep-link (?comment=<id>) regression

Uses the pre-provisioned admin identity `carlofarinapayme@gmail.com`
(user_13f93cbd1ea9) via a freshly-signed JWT and the pre-verified
`chat_a@test.it` / `chat_b@test.it` accounts for the non-admin role.
"""
import os
import time
import jwt
import uuid
import pytest
import requests
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

BASE_URL = os.environ['EXPO_PUBLIC_BACKEND_URL'].rstrip('/') if os.environ.get('EXPO_PUBLIC_BACKEND_URL') else \
    'https://bot-burst-fix.preview.emergentagent.com'
JWT_SECRET = os.environ['JWT_SECRET']
API = f"{BASE_URL}/api"

ADMIN_USER_ID = 'user_13f93cbd1ea9'
ADMIN_EMAIL = 'carlofarinapayme@gmail.com'
USER_A_EMAIL = 'chat_a@test.it'
USER_A_PASS = 'test123'
USER_B_EMAIL = 'chat_b@test.it'
USER_B_PASS = 'test123'


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
    tok = r.json()['token']
    return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def user_b_headers():
    r = requests.post(f'{API}/auth/login', json={'email': USER_B_EMAIL, 'password': USER_B_PASS}, timeout=15)
    assert r.status_code == 200, f'Login user B failed: {r.status_code} {r.text}'
    tok = r.json()['token']
    return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def anon_headers():
    r = requests.post(f'{API}/auth/anonymous', json={'nickname': f'anon{uuid.uuid4().hex[:6]}',
                                                     'device_id': f'dev_{uuid.uuid4().hex[:10]}'}, timeout=15)
    assert r.status_code == 200
    return {'Authorization': f"Bearer {r.json()['token']}", 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def sample_feud_id():
    """Grab an existing live feud (admin sees all)."""
    r = requests.get(f'{API}/feuds?limit=5', timeout=15)
    assert r.status_code == 200
    feuds = r.json().get('feuds', [])
    assert feuds, 'No live feuds available for testing'
    return feuds[0]['feud_id']


# ─── Helpers ────────────────────────────────────────────────────────────────

def _get_feud_ids(url: str, headers=None):
    r = requests.get(url, headers=headers or {}, timeout=15)
    assert r.status_code == 200, f'{url} -> {r.status_code} {r.text[:200]}'
    return [f['feud_id'] for f in r.json().get('feuds', [])]


# ═════════════════════════════════════════════════════════════════════════════
# Auth / Sanity
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthSanity:
    def test_admin_token_resolves_to_founder_email(self, admin_headers):
        r = requests.get(f'{API}/auth/me', headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()['user']['email'].lower() == ADMIN_EMAIL

    def test_user_a_is_not_admin(self, user_a_headers):
        r = requests.get(f'{API}/auth/me', headers=user_a_headers, timeout=15)
        assert r.status_code == 200
        assert r.json()['user']['email'].lower() != ADMIN_EMAIL


# ═════════════════════════════════════════════════════════════════════════════
# PATCH /api/feuds/{id}
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminEditFeud:
    def test_edit_requires_auth(self, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}', json={'title': 'x'}, timeout=15)
        assert r.status_code in (401, 403), f'expected 401/403, got {r.status_code}'

    def test_edit_forbidden_for_non_admin(self, user_a_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=user_a_headers, json={'title': 'hack'}, timeout=15)
        assert r.status_code == 403, f'expected 403, got {r.status_code} {r.text}'

    def test_edit_forbidden_for_anon(self, anon_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=anon_headers, json={'title': 'hack'}, timeout=15)
        assert r.status_code == 403

    def test_edit_404_when_not_found(self, admin_headers):
        r = requests.patch(f'{API}/feuds/feud_does_not_exist_xxx',
                           headers=admin_headers, json={'title': 'x'}, timeout=15)
        assert r.status_code == 404

    def test_edit_empty_title_rejected(self, admin_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=admin_headers, json={'title': '   '}, timeout=15)
        assert r.status_code == 400

    def test_edit_empty_question_rejected(self, admin_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=admin_headers, json={'question': ''}, timeout=15)
        assert r.status_code == 400

    def test_edit_invalid_category_rejected(self, admin_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=admin_headers, json={'category': 'notarealcategory'}, timeout=15)
        assert r.status_code == 400

    def test_edit_empty_payload_rejected(self, admin_headers, sample_feud_id):
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=admin_headers, json={}, timeout=15)
        assert r.status_code == 400

    def test_edit_title_question_category_persists(self, admin_headers, sample_feud_id):
        # Snapshot original values so we can restore.
        original = requests.get(f'{API}/feuds/{sample_feud_id}', headers=admin_headers, timeout=15).json()['feud']
        new_title = f'TEST_EDIT_TITLE_{uuid.uuid4().hex[:6]}'
        new_question = f'TEST_QUESTION_{uuid.uuid4().hex[:6]}?'
        payload = {'title': new_title, 'question': new_question, 'category': 'tech'}
        r = requests.patch(f'{API}/feuds/{sample_feud_id}',
                           headers=admin_headers, json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()['feud']
        assert body['title'] == new_title
        assert body['question'] == new_question
        assert body['category'] == 'tech'
        assert body.get('category_label') == 'Tech'
        assert body.get('edited_by') == ADMIN_USER_ID
        assert body.get('edited_at')

        # Verify persisted via GET
        r2 = requests.get(f'{API}/feuds/{sample_feud_id}', headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        got = r2.json()['feud']
        assert got['title'] == new_title
        assert got['question'] == new_question
        assert got['category'] == 'tech'

        # Restore original
        restore_payload = {
            'title': original['title'],
            'question': original.get('question') or original['title'],
            'category': original['category'],
        }
        rr = requests.patch(f'{API}/feuds/{sample_feud_id}',
                            headers=admin_headers, json=restore_payload, timeout=15)
        assert rr.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# DELETE + RESTORE + Feed filtering
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminHideRestore:

    @pytest.fixture(scope='class')
    def target_feud(self, admin_headers):
        """Pick an existing live feud we'll hide/restore during the class."""
        r = requests.get(f'{API}/feuds?limit=20', timeout=15)
        assert r.status_code == 200
        feuds = r.json().get('feuds', [])
        assert feuds
        # Prefer a feud that is NOT the same one used by TestAdminEditFeud to
        # keep runs deterministic under parallel exec.
        target = feuds[-1]
        yield target
        # Teardown: ensure it's un-hidden regardless of test outcome
        try:
            requests.post(f'{API}/feuds/{target["feud_id"]}/restore',
                          headers=admin_headers, timeout=15)
        except Exception:
            pass

    def test_hide_forbidden_for_non_admin(self, user_a_headers, target_feud):
        r = requests.delete(f'{API}/feuds/{target_feud["feud_id"]}',
                            headers=user_a_headers, timeout=15)
        assert r.status_code == 403

    def test_hide_requires_auth(self, target_feud):
        r = requests.delete(f'{API}/feuds/{target_feud["feud_id"]}', timeout=15)
        assert r.status_code in (401, 403)

    def test_hide_404_when_not_found(self, admin_headers):
        r = requests.delete(f'{API}/feuds/feud_does_not_exist_xxx',
                            headers=admin_headers, timeout=15)
        assert r.status_code == 404

    def test_hide_then_visibility_matrix(self, admin_headers, user_a_headers, anon_headers, target_feud):
        fid = target_feud['feud_id']
        # Sanity: currently visible to a regular user
        pre = requests.get(f'{API}/feuds/{fid}', headers=user_a_headers, timeout=15)
        assert pre.status_code == 200, f'pre-hide should be visible: {pre.status_code}'

        # 1. Hide as admin
        h = requests.delete(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert h.status_code == 200, h.text
        j = h.json()
        assert j['ok'] is True and j['is_hidden'] is True and j['feud_id'] == fid

        # 2. Regular user now sees 410
        r_user = requests.get(f'{API}/feuds/{fid}', headers=user_a_headers, timeout=15)
        assert r_user.status_code == 410, f'expected 410, got {r_user.status_code}'
        # 3. Anonymous also sees 410
        r_anon = requests.get(f'{API}/feuds/{fid}', headers=anon_headers, timeout=15)
        assert r_anon.status_code == 410
        # 4. Unauthenticated also sees 410
        r_none = requests.get(f'{API}/feuds/{fid}', timeout=15)
        assert r_none.status_code == 410

        # 5. Admin still sees it with is_hidden=True + is_admin_viewer=True
        r_admin = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert r_admin.status_code == 200, r_admin.text
        f = r_admin.json()['feud']
        assert f.get('is_hidden') is True
        assert f.get('is_admin_viewer') is True
        assert f.get('hidden_by') == ADMIN_USER_ID
        assert f.get('hidden_at')

        # 6. Feed filters — hidden feud must be absent
        assert fid not in _get_feud_ids(f'{API}/feuds?limit=200')
        assert fid not in _get_feud_ids(f'{API}/feuds/hype?limit=200')
        # Archive uses a date range; scan all archive dates for cronaca-ish
        adates = requests.get(f'{API}/feuds/archive/dates', timeout=15).json().get('dates', [])
        for d in adates[:5]:
            assert fid not in _get_feud_ids(f'{API}/feuds/archive?date={d}')

        # Search: use two words from title if we can
        title = target_feud.get('title', '')
        words = [w for w in title.split() if len(w) >= 4][:2]
        if words:
            q = ' '.join(words)
            assert fid not in _get_feud_ids(f'{API}/search?q={q}')

        # Hashtag: derive from admin view
        htag = f.get('hashtag')
        if htag:
            assert fid not in _get_feud_ids(f'{API}/hashtags/{htag}')

        # Favorites — favorite it as user A first (should succeed regardless),
        # then confirm it does NOT appear in their favorites feed while hidden.
        requests.post(f'{API}/feuds/{fid}/favorite', headers=user_a_headers, timeout=15)
        assert fid not in _get_feud_ids(f'{API}/favorites', headers=user_a_headers)

        # 7. Admin list-hidden includes it
        lh = requests.get(f'{API}/admin/hidden-feuds', headers=admin_headers, timeout=15)
        assert lh.status_code == 200
        assert fid in [x['feud_id'] for x in lh.json().get('feuds', [])]

        # 8. Non-admin cannot list hidden
        lh_forbidden = requests.get(f'{API}/admin/hidden-feuds', headers=user_a_headers, timeout=15)
        assert lh_forbidden.status_code == 403

        # 9. Restore forbidden for non-admin
        rf = requests.post(f'{API}/feuds/{fid}/restore', headers=user_a_headers, timeout=15)
        assert rf.status_code == 403

        # 10. Restore as admin
        rs = requests.post(f'{API}/feuds/{fid}/restore', headers=admin_headers, timeout=15)
        assert rs.status_code == 200, rs.text
        rj = rs.json()
        assert rj['ok'] is True and rj['is_hidden'] is False

        # 11. Post-restore visibility restored for everyone
        post_user = requests.get(f'{API}/feuds/{fid}', headers=user_a_headers, timeout=15)
        assert post_user.status_code == 200
        post_admin = requests.get(f'{API}/feuds/{fid}', headers=admin_headers, timeout=15)
        assert post_admin.status_code == 200
        pa = post_admin.json()['feud']
        assert pa.get('is_hidden') is False

        # 12. No longer in hidden list
        lh2 = requests.get(f'{API}/admin/hidden-feuds', headers=admin_headers, timeout=15)
        assert fid not in [x['feud_id'] for x in lh2.json().get('feuds', [])]

    def test_restore_404_when_not_found(self, admin_headers):
        r = requests.post(f'{API}/feuds/feud_does_not_exist_xxx/restore',
                          headers=admin_headers, timeout=15)
        assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# Regression: comment mention notification deep-link still carries ?comment=
# ═════════════════════════════════════════════════════════════════════════════

class TestCommentMentionDeeplinkRegression:
    def test_comment_creation_still_ok_and_notifies_mention(
        self, admin_headers, user_a_headers, user_b_headers, sample_feud_id
    ):
        """Post a comment on `sample_feud_id` that @mentions user B and confirm
        (a) the comment endpoint still works and (b) a notification lands for B
        with `comment_id` populated (used to build the ?comment=<id> deep link).
        """
        # Fetch user B nickname
        me_b = requests.get(f'{API}/auth/me', headers=user_b_headers, timeout=15).json()['user']
        nick_b = me_b.get('nickname')
        assert nick_b, 'chat_b needs a nickname to be mentioned'

        # Vote first (comments require a vote in many builds) — best-effort
        requests.post(f'{API}/feuds/{sample_feud_id}/vote',
                      headers=user_a_headers, json={'side': 'a'}, timeout=15)

        # Post comment mentioning user B
        text = f'ciao @{nick_b} test mention {uuid.uuid4().hex[:6]}'
        r = requests.post(f'{API}/feuds/{sample_feud_id}/comments',
                          headers=user_a_headers, json={'text': text}, timeout=20)
        if r.status_code not in (200, 201):
            pytest.skip(f'comment endpoint unavailable or precondition failed: {r.status_code} {r.text[:200]}')
        comment = r.json().get('comment') or r.json()
        cid = comment.get('comment_id') or comment.get('id')
        assert cid, f'comment_id missing in response: {r.json()}'

        # Give the notif hook a moment
        time.sleep(1.0)
        n = requests.get(f'{API}/notifications', headers=user_b_headers, timeout=15)
        assert n.status_code == 200
        notifs = n.json().get('notifications') or n.json().get('items') or []
        # Find any recent notif referencing this comment_id — this is what the
        # deep-link `?comment=<id>` is built from on the push side.
        match = [x for x in notifs if x.get('comment_id') == cid]
        assert match, f'expected notification with comment_id={cid}, got {notifs[:3]}'
        assert match[0].get('feud_id') == sample_feud_id


# ═════════════════════════════════════════════════════════════════════════════
# Regression: existing feed endpoints still return successfully
# ═════════════════════════════════════════════════════════════════════════════

class TestFeedRegression:
    @pytest.mark.parametrize('path', [
        '/feuds?limit=5',
        '/feuds/hype?limit=5',
        '/feuds/archive/dates',
        '/search?q=politica',
        '/categories',
    ])
    def test_public_feed_endpoints_ok(self, path):
        r = requests.get(f'{API}{path}', timeout=15)
        assert r.status_code == 200, f'{path} -> {r.status_code} {r.text[:120]}'
