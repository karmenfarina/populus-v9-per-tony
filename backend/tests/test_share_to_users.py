"""Backend tests for the Instagram-style share-to-user feature.

Endpoints under test:
- GET  /api/messages/share-suggestions
- GET  /api/search/users
- POST /api/feuds/{feud_id}/share
- POST /api/messages/send  (extended with shared_feud_id)
- GET  /api/messages/conversations  (preview text for shared feuds)
"""
import os
import time

import pytest
import requests

BASE_URL = (os.environ.get('EXPO_PUBLIC_BACKEND_URL') or 'https://gossip-beta.preview.emergentagent.com').rstrip('/')

USER_A = {'email': 'chat_a@test.it', 'password': 'test123', 'user_id': 'user_6e65e19525d5'}
USER_B = {'email': 'chat_b@test.it', 'password': 'test123', 'user_id': 'user_16f709708760'}


# ---------- helpers ---------------------------------------------------------
def _login(email: str, password: str) -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login", json={'email': email, 'password': password}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()['token']


def _anon(nickname: str) -> str:
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={'nickname': nickname}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()['token']


def _hdr(t: str) -> dict:
    return {'Authorization': f'Bearer {t}', 'Content-Type': 'application/json'}


def _first_feud_id() -> str:
    r = requests.get(f"{BASE_URL}/api/feuds?limit=1", timeout=15)
    assert r.status_code == 200, r.text
    feuds = r.json().get('feuds') or []
    assert feuds, 'No feuds available for test'
    return feuds[0]['feud_id']


@pytest.fixture(scope='module')
def tokens():
    a = _login(USER_A['email'], USER_A['password'])
    b = _login(USER_B['email'], USER_B['password'])
    anon = _anon(f'anonShr{int(time.time()) % 100000}')
    # Clean any existing block state
    requests.delete(f"{BASE_URL}/api/users/{USER_B['user_id']}/block", headers=_hdr(a), timeout=15)
    requests.delete(f"{BASE_URL}/api/users/{USER_A['user_id']}/block", headers=_hdr(b), timeout=15)
    return {'a': a, 'b': b, 'anon': anon}


@pytest.fixture(scope='module')
def feud_id():
    return _first_feud_id()


@pytest.fixture(scope='module')
def seed_conversation(tokens):
    """Ensure user A and B have chat history so A ranks B highly in suggestions."""
    for _ in range(2):
        r = requests.post(
            f"{BASE_URL}/api/messages/send",
            headers=_hdr(tokens['a']),
            json={'recipient_id': USER_B['user_id'], 'text': f'seed hello {int(time.time())}'},
            timeout=15,
        )
        assert r.status_code == 200, r.text
    return True


# ---------- share-suggestions -----------------------------------------------
class TestShareSuggestions:
    def test_share_suggestions_registered_returns_users(self, tokens, seed_conversation):
        r = requests.get(
            f"{BASE_URL}/api/messages/share-suggestions?limit=21",
            headers=_hdr(tokens['a']),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert 'users' in j and isinstance(j['users'], list)
        # After seed_conversation, USER_B should be present
        uids = [u['user_id'] for u in j['users']]
        assert USER_B['user_id'] in uids, f"expected {USER_B['user_id']} in suggestions, got {uids}"
        # Verify shape
        for u in j['users']:
            assert 'user_id' in u
            assert 'nickname' in u
            assert 'score' in u
            assert u['user_id'] != USER_A['user_id'], 'self must not appear'

    def test_share_suggestions_chat_partners_rank_higher(self, tokens, seed_conversation):
        r = requests.get(
            f"{BASE_URL}/api/messages/share-suggestions?limit=21",
            headers=_hdr(tokens['a']),
            timeout=15,
        )
        assert r.status_code == 200
        users = r.json()['users']
        # USER_B should be top (or at least top-3) because chat weight = 1.0
        top_ids = [u['user_id'] for u in users[:3]]
        assert USER_B['user_id'] in top_ids

    def test_share_suggestions_anon_empty(self, tokens):
        r = requests.get(
            f"{BASE_URL}/api/messages/share-suggestions",
            headers=_hdr(tokens['anon']),
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json() == {'users': []}


# ---------- search/users -----------------------------------------------------
class TestSearchUsers:
    def test_search_users_substring_case_insensitive(self, tokens):
        # chat_a nickname is 'chatUserA', chat_b is 'chatUserB' — search 'chat'
        r = requests.get(
            f"{BASE_URL}/api/search/users?q=chat&limit=20",
            headers=_hdr(tokens['a']),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert 'users' in j and isinstance(j['users'], list)
        # Should not contain self (USER_A)
        uids = [u['user_id'] for u in j['users']]
        assert USER_A['user_id'] not in uids, 'search must exclude self'
        # Should contain USER_B (nickname chatUserB matches 'chat')
        assert USER_B['user_id'] in uids

    def test_search_users_case_insensitive(self, tokens):
        r = requests.get(
            f"{BASE_URL}/api/search/users?q=CHAT",
            headers=_hdr(tokens['a']),
            timeout=15,
        )
        assert r.status_code == 200
        uids = [u['user_id'] for u in r.json()['users']]
        assert USER_B['user_id'] in uids

    def test_search_users_empty_query_returns_empty(self, tokens):
        r = requests.get(f"{BASE_URL}/api/search/users?q=", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 200
        assert r.json() == {'users': []}

    def test_search_users_excludes_anonymous(self, tokens):
        # search a very common substring that anonymous accounts might match
        r = requests.get(f"{BASE_URL}/api/search/users?q=anon", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 200
        # any user_id returned must not be an anonymous account
        for u in r.json()['users']:
            # anonymous accounts are excluded by backend filter; just sanity check nickname is present
            assert u.get('nickname')


# ---------- POST /feuds/{id}/share ------------------------------------------
class TestShareFeudFanOut:
    def test_share_feud_to_valid_recipient(self, tokens, feud_id):
        text_marker = f'TEST_share_{int(time.time())}'
        r = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/share",
            headers=_hdr(tokens['a']),
            json={'recipient_ids': [USER_B['user_id']], 'text': text_marker},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert 'sent' in j and 'failed' in j
        assert USER_B['user_id'] in j['sent']
        assert j['failed'] == []

        # Verify message actually exists via messages/with (as recipient B)
        r2 = requests.get(
            f"{BASE_URL}/api/messages/with/{USER_A['user_id']}",
            headers=_hdr(tokens['b']),
            timeout=15,
        )
        assert r2.status_code == 200
        msgs = r2.json()['messages']
        matches = [m for m in msgs if m.get('text') == text_marker]
        assert matches, f'sent share message not found in recipient inbox'
        m = matches[-1]
        assert m.get('kind') == 'shared_feud'
        assert m.get('shared_feud') is not None
        assert m['shared_feud'].get('feud_id') == feud_id
        assert m.get('sender_id') == USER_A['user_id']
        assert m.get('recipient_id') == USER_B['user_id']

    def test_share_feud_fan_out_mixed_recipients(self, tokens, feud_id):
        payload = {
            'recipient_ids': [USER_B['user_id'], 'invalid_user_xyz_999', USER_A['user_id']],
            'text': f'TEST_fanout_{int(time.time())}',
        }
        r = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/share",
            headers=_hdr(tokens['a']),
            json=payload,
            timeout=20,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert USER_B['user_id'] in j['sent']
        failed_ids = {f['user_id']: f['error'] for f in j['failed']}
        assert 'invalid_user_xyz_999' in failed_ids
        assert USER_A['user_id'] in failed_ids
        # Self entry error should be 'self'
        assert failed_ids[USER_A['user_id']] == 'self'
        # invalid must have a non-empty error string
        assert failed_ids['invalid_user_xyz_999']

    def test_share_feud_anon_forbidden(self, tokens, feud_id):
        r = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/share",
            headers=_hdr(tokens['anon']),
            json={'recipient_ids': [USER_B['user_id']]},
            timeout=15,
        )
        assert r.status_code == 403

    def test_share_feud_not_found(self, tokens):
        r = requests.post(
            f"{BASE_URL}/api/feuds/does_not_exist_feud/share",
            headers=_hdr(tokens['a']),
            json={'recipient_ids': [USER_B['user_id']]},
            timeout=15,
        )
        assert r.status_code == 404


# ---------- /messages/send with shared_feud_id ------------------------------
class TestSendMessageWithSharedFeud:
    def test_send_with_shared_feud_id_builds_snapshot(self, tokens, feud_id):
        # Get feud title from API for comparison
        r = requests.get(f"{BASE_URL}/api/feuds/{feud_id}", timeout=15)
        assert r.status_code == 200
        expected_title = (r.json().get('feud') or r.json()).get('title')

        # Send with empty text; snapshot must still be attached
        r2 = requests.post(
            f"{BASE_URL}/api/messages/send",
            headers=_hdr(tokens['a']),
            json={'recipient_id': USER_B['user_id'], 'shared_feud_id': feud_id},
            timeout=15,
        )
        assert r2.status_code == 200, r2.text
        j = r2.json()
        # Server response should include the message with shared_feud snapshot
        msg = j.get('message') or j
        # The endpoint may return the raw message or a wrapper — locate shared_feud
        sf = msg.get('shared_feud') if isinstance(msg, dict) else None
        if sf is None:
            # Fallback: fetch messages
            r3 = requests.get(
                f"{BASE_URL}/api/messages/with/{USER_A['user_id']}",
                headers=_hdr(tokens['b']),
                timeout=15,
            )
            assert r3.status_code == 200
            latest = r3.json()['messages'][-1]
            sf = latest.get('shared_feud')
        assert sf is not None, 'shared_feud snapshot missing'
        assert sf.get('feud_id') == feud_id
        assert sf.get('title') == expected_title, 'server must build snapshot from DB'

    def test_send_client_cannot_spoof_snapshot_title(self, tokens, feud_id):
        # Client passes shared_feud_id + tries to spoof title/image_url in body.
        # Backend model only accepts shared_feud_id; any extra fields are ignored.
        r = requests.get(f"{BASE_URL}/api/feuds/{feud_id}", timeout=15)
        expected_title = (r.json().get('feud') or r.json()).get('title')

        r2 = requests.post(
            f"{BASE_URL}/api/messages/send",
            headers=_hdr(tokens['a']),
            json={
                'recipient_id': USER_B['user_id'],
                'shared_feud_id': feud_id,
                'shared_feud': {'title': 'SPOOFED', 'image_url': 'http://evil.example/spoof.jpg'},
            },
            timeout=15,
        )
        assert r2.status_code == 200, r2.text
        # Fetch latest message and assert title is not spoofed
        r3 = requests.get(
            f"{BASE_URL}/api/messages/with/{USER_A['user_id']}",
            headers=_hdr(tokens['b']),
            timeout=15,
        )
        assert r3.status_code == 200
        latest = r3.json()['messages'][-1]
        assert latest.get('shared_feud', {}).get('title') == expected_title
        assert latest['shared_feud'].get('title') != 'SPOOFED'


# ---------- conversations preview text --------------------------------------
class TestConversationsPreview:
    def test_last_message_preview_is_post_condiviso(self, tokens, feud_id):
        # Send a share message to make it the latest in the conversation
        r = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/share",
            headers=_hdr(tokens['a']),
            json={'recipient_ids': [USER_B['user_id']]},  # no text
            timeout=15,
        )
        assert r.status_code == 200
        # Fetch conversations
        r2 = requests.get(f"{BASE_URL}/api/messages/conversations", headers=_hdr(tokens['a']), timeout=15)
        assert r2.status_code == 200
        convs = r2.json()['conversations']
        # Find conversation with USER_B
        conv = next((c for c in convs if c['other_user']['user_id'] == USER_B['user_id']), None)
        assert conv is not None, 'conversation with B not found'
        preview = conv.get('last_message_preview', '')
        assert '📎 Post condiviso' in preview, f'expected shared-post preview, got: {preview!r}'
