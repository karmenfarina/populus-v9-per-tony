"""
Iteration 29 targeted verification:
- Anonymous auth response now includes is_anonymous: true
- /api/auth/me for anon returns is_anonymous: true
- /api/messages/unread-count returns {count:0} for anon (200)
- Regression: chat_a send, conversations list, report user
"""
import os
import time
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://skeleton-cache-build.preview.emergentagent.com").rstrip("/")

CHAT_A = {"email": "chat_a@test.it", "password": "test123"}
CHAT_B_ID = "user_16f709708760"


def _login(payload):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Anonymous lockout backend verification ----------

def test_anonymous_signup_returns_is_anonymous_true():
    nick = f"tAnon_{int(time.time()) % 100000}"
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "user" in body and "token" in body
    u = body["user"]
    assert u.get("is_anonymous") is True, f"is_anonymous missing/false in /api/auth/anonymous: {u}"
    assert u.get("auth_provider") == "anonymous"


def test_anonymous_auth_me_returns_is_anonymous_true():
    nick = f"tAnMe_{int(time.time()) % 100000}"
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200
    token = r.json()["token"]
    me = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    assert me.status_code == 200, me.text
    payload = me.json()
    # /api/auth/me may return user directly or wrapped
    user = payload.get("user", payload)
    assert user.get("is_anonymous") is True, f"is_anonymous missing on /api/auth/me: {payload}"


def test_anonymous_unread_count_returns_zero():
    nick = f"tAnUn_{int(time.time()) % 100000}"
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200
    token = r.json()["token"]
    uc = requests.get(
        f"{BASE_URL}/api/messages/unread-count",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert uc.status_code == 200, uc.text
    assert uc.json().get("count") == 0


# ---------- Regression: verify chat_a auth also carries is_anonymous:false ----------

def test_chat_a_login_has_is_anonymous_false():
    body = _login(CHAT_A)
    u = body["user"]
    # Must be present and False
    assert "is_anonymous" in u, f"is_anonymous key missing: {u}"
    assert u["is_anonymous"] is False


# ---------- Regression: messaging flows unchanged ----------

def test_regression_send_and_conversations_and_report():
    body = _login(CHAT_A)
    token = body["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Send message A -> B
    send = requests.post(
        f"{BASE_URL}/api/messages/send",
        json={"recipient_id": CHAT_B_ID, "text": f"TEST_regression_{int(time.time())}"},
        headers=headers,
        timeout=15,
    )
    assert send.status_code == 200, send.text
    msg = send.json().get("message", send.json())
    assert msg.get("message_id"), f"missing message_id in send response: {send.json()}"

    # Conversations list
    convs = requests.get(f"{BASE_URL}/api/messages/conversations", headers=headers, timeout=15)
    assert convs.status_code == 200, convs.text
    data = convs.json()
    items = data.get("conversations", data if isinstance(data, list) else [])
    assert any(
        CHAT_B_ID in (c.get("other_user_id"), c.get("peer_id"), (c.get("other_user") or {}).get("user_id"))
        for c in items
    ), f"chat_b conv not found: {items}"

    # Report chat_b (idempotent-ish)
    rep = requests.post(
        f"{BASE_URL}/api/users/{CHAT_B_ID}/report",
        json={"reason": "spam", "details": "TEST_regression report"},
        headers=headers,
        timeout=15,
    )
    assert rep.status_code == 200, rep.text
