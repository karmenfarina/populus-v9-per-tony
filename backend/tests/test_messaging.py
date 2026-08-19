"""Backend tests for the Populus Messaging system (chat, blocks, reports, WS)."""
import asyncio
import json
import os
import time

import pytest
import requests
import websockets

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL') or 'https://feud-governance.preview.emergentagent.com'
BASE_URL = BASE_URL.rstrip('/')
WS_URL = BASE_URL.replace('https://', 'wss://').replace('http://', 'ws://') + '/api/ws/messages'

USER_A = {'email': 'chat_a@test.it', 'password': 'test123', 'user_id': 'user_6e65e19525d5'}
USER_B = {'email': 'chat_b@test.it', 'password': 'test123', 'user_id': 'user_16f709708760'}


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={'email': email, 'password': password}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()['token']


def _anon(nickname):
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={'nickname': nickname}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()['token']


def _hdr(t):
    return {'Authorization': f'Bearer {t}', 'Content-Type': 'application/json'}


@pytest.fixture(scope='module')
def tokens():
    a = _login(USER_A['email'], USER_A['password'])
    b = _login(USER_B['email'], USER_B['password'])
    anon = _anon(f'anonTester_{int(time.time())}')
    # Cleanup any prior block state between the two test users so tests start clean.
    requests.delete(f"{BASE_URL}/api/users/{USER_B['user_id']}/block", headers=_hdr(a), timeout=15)
    requests.delete(f"{BASE_URL}/api/users/{USER_A['user_id']}/block", headers=_hdr(b), timeout=15)
    return {'a': a, 'b': b, 'anon': anon}


# -------- unread-count -------------------------------------------------------
class TestUnreadCount:
    def test_unread_count_registered(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/unread-count", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert 'count' in j and isinstance(j['count'], int)

    def test_unread_count_anon_returns_zero(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/unread-count", headers=_hdr(tokens['anon']), timeout=15)
        assert r.status_code == 200
        assert r.json() == {'count': 0}


# -------- conversations ------------------------------------------------------
class TestConversations:
    def test_list_conversations_registered(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/conversations", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert 'conversations' in j and isinstance(j['conversations'], list)

    def test_list_conversations_anon_forbidden(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/conversations", headers=_hdr(tokens['anon']), timeout=15)
        assert r.status_code == 403


# -------- messages/with/{other} ----------------------------------------------
class TestMessagesWith:
    def test_messages_with_returns_structure(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/with/{USER_B['user_id']}", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert 'messages' in j and isinstance(j['messages'], list)
        assert 'i_blocked' in j and 'they_blocked' in j
        assert j['other_user']['user_id'] == USER_B['user_id']

    def test_messages_with_self_400(self, tokens):
        r = requests.get(f"{BASE_URL}/api/messages/with/{USER_A['user_id']}", headers=_hdr(tokens['a']), timeout=15)
        assert r.status_code == 400


# -------- send message -------------------------------------------------------
class TestSendMessage:
    def test_send_anon_sender_forbidden(self, tokens):
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['anon']),
                          json={'recipient_id': USER_B['user_id'], 'text': 'hi'}, timeout=15)
        assert r.status_code == 403

    def test_send_to_self_400(self, tokens):
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_A['user_id'], 'text': 'hi'}, timeout=15)
        assert r.status_code == 400

    def test_send_empty_payload_400(self, tokens):
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id']}, timeout=15)
        assert r.status_code == 400

    def test_send_text_ok_and_persisted(self, tokens):
        txt = f"TEST_hello_{int(time.time())}"
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'text': txt}, timeout=15)
        assert r.status_code == 200, r.text
        msg = r.json()['message']
        assert msg['text'] == txt
        assert msg['sender_id'] == USER_A['user_id']
        assert msg['recipient_id'] == USER_B['user_id']
        assert msg['read_at'] is None
        # Verify persistence via GET
        g = requests.get(f"{BASE_URL}/api/messages/with/{USER_A['user_id']}", headers=_hdr(tokens['b']), timeout=15)
        assert g.status_code == 200
        texts = [m.get('text') for m in g.json()['messages']]
        assert txt in texts
        # keep message_id on self for later tests
        pytest.msg_id_text = msg['message_id']

    def test_send_image_ok(self, tokens):
        b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII='
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'image_data': f'data:image/png;base64,{b64}'}, timeout=20)
        assert r.status_code == 200
        msg = r.json()['message']
        assert msg['kind'] == 'image'
        assert msg['image_data'] is not None


# -------- read receipts ------------------------------------------------------
class TestReadReceipts:
    def test_mark_read(self, tokens):
        # A sends new message
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'text': f'TEST_read_{int(time.time())}'}, timeout=15)
        assert r.status_code == 200
        mid = r.json()['message']['message_id']
        # B marks conversation read
        rr = requests.post(f"{BASE_URL}/api/messages/with/{USER_A['user_id']}/read",
                           headers=_hdr(tokens['b']), timeout=15)
        assert rr.status_code == 200
        assert rr.json()['updated'] >= 1
        # Verify read_at is now set
        g = requests.get(f"{BASE_URL}/api/messages/with/{USER_A['user_id']}", headers=_hdr(tokens['b']), timeout=15)
        found = [m for m in g.json()['messages'] if m['message_id'] == mid]
        assert found and found[0]['read_at'] is not None


# -------- reactions ----------------------------------------------------------
class TestReactions:
    def test_toggle_reaction(self, tokens):
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'text': f'TEST_react_{int(time.time())}'}, timeout=15)
        mid = r.json()['message']['message_id']
        # B reacts with heart
        rx = requests.post(f"{BASE_URL}/api/messages/{mid}/react", headers=_hdr(tokens['b']),
                           json={'emoji': '❤️'}, timeout=15)
        assert rx.status_code == 200
        reactions = rx.json()['message']['reactions']
        assert reactions.get(USER_B['user_id']) == '❤️'
        # Toggle off with same emoji
        rx2 = requests.post(f"{BASE_URL}/api/messages/{mid}/react", headers=_hdr(tokens['b']),
                            json={'emoji': '❤️'}, timeout=15)
        assert rx2.status_code == 200
        assert USER_B['user_id'] not in rx2.json()['message']['reactions']


# -------- delete -------------------------------------------------------------
class TestDelete:
    def test_only_sender_can_delete(self, tokens):
        r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'text': f'TEST_del_{int(time.time())}'}, timeout=15)
        mid = r.json()['message']['message_id']
        # B (recipient) should not delete
        bad = requests.delete(f"{BASE_URL}/api/messages/{mid}", headers=_hdr(tokens['b']), timeout=15)
        assert bad.status_code == 403
        # A (sender) can delete
        ok = requests.delete(f"{BASE_URL}/api/messages/{mid}", headers=_hdr(tokens['a']), timeout=15)
        assert ok.status_code == 200
        # Deleted messages are filtered out of the conversation view by design
        g = requests.get(f"{BASE_URL}/api/messages/with/{USER_B['user_id']}", headers=_hdr(tokens['a']), timeout=15)
        found = [m for m in g.json()['messages'] if m['message_id'] == mid]
        assert not found, "deleted message should be filtered from conversation"


# -------- block / report -----------------------------------------------------
class TestBlockReport:
    def test_block_and_send_forbidden_then_unblock(self, tokens):
        # A blocks B
        b = requests.post(f"{BASE_URL}/api/users/{USER_B['user_id']}/block", headers=_hdr(tokens['a']), timeout=15)
        assert b.status_code == 200
        # my blocks list contains B
        my = requests.get(f"{BASE_URL}/api/users/me/blocks", headers=_hdr(tokens['a']), timeout=15)
        assert my.status_code == 200
        ids = [u['user_id'] for u in my.json()['blocked_users']]
        assert USER_B['user_id'] in ids
        # A cannot send to B
        s = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                         json={'recipient_id': USER_B['user_id'], 'text': 'blocked?'}, timeout=15)
        assert s.status_code == 403
        # B also cannot send to A (bi-directional)
        s2 = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['b']),
                          json={'recipient_id': USER_A['user_id'], 'text': 'blocked?'}, timeout=15)
        assert s2.status_code == 403
        # messages/with reflects flags
        g = requests.get(f"{BASE_URL}/api/messages/with/{USER_B['user_id']}", headers=_hdr(tokens['a']), timeout=15)
        assert g.json()['i_blocked'] is True
        g2 = requests.get(f"{BASE_URL}/api/messages/with/{USER_A['user_id']}", headers=_hdr(tokens['b']), timeout=15)
        assert g2.json()['they_blocked'] is True
        # Unblock
        u = requests.delete(f"{BASE_URL}/api/users/{USER_B['user_id']}/block", headers=_hdr(tokens['a']), timeout=15)
        assert u.status_code == 200
        s3 = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                          json={'recipient_id': USER_B['user_id'], 'text': f'TEST_unblocked_{int(time.time())}'}, timeout=15)
        assert s3.status_code == 200

    def test_report_user(self, tokens):
        r = requests.post(f"{BASE_URL}/api/users/{USER_B['user_id']}/report", headers=_hdr(tokens['a']),
                         json={'reason': 'TEST_spam'}, timeout=15)
        assert r.status_code == 200
        # Too short reason (1 char) -> 422
        bad = requests.post(f"{BASE_URL}/api/users/{USER_B['user_id']}/report", headers=_hdr(tokens['a']),
                           json={'reason': 'x'}, timeout=15)
        assert bad.status_code == 422


# -------- WebSocket ----------------------------------------------------------
class TestWebSocket:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)

    def test_ws_invalid_token_rejected(self):
        async def go():
            try:
                async with websockets.connect(f"{WS_URL}?token=badtoken") as ws:
                    await ws.recv()
                    return None
            except websockets.exceptions.ConnectionClosed as e:
                return e.code
        code = self._run(go())
        assert code == 4401, f"expected 4401, got {code}"

    def test_ws_anonymous_rejected(self, tokens):
        async def go():
            try:
                async with websockets.connect(f"{WS_URL}?token={tokens['anon']}") as ws:
                    await ws.recv()
                    return None
            except websockets.exceptions.ConnectionClosed as e:
                return e.code
        code = self._run(go())
        assert code == 4403, f"expected 4403, got {code}"

    def test_ws_hello_and_message_delivery(self, tokens):
        async def go():
            async with websockets.connect(f"{WS_URL}?token={tokens['b']}") as ws:
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert hello['type'] == 'hello' and hello['user_id'] == USER_B['user_id']
                # A sends message to B while B is connected
                txt = f"TEST_ws_{int(time.time())}"
                r = requests.post(f"{BASE_URL}/api/messages/send", headers=_hdr(tokens['a']),
                                 json={'recipient_id': USER_B['user_id'], 'text': txt}, timeout=15)
                assert r.status_code == 200
                # Await push
                evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert evt['type'] == 'message.new'
                assert evt['message']['text'] == txt
        self._run(go())
