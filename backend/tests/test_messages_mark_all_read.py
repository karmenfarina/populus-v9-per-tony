"""Tests for POST /api/messages/mark-all-read orphan-sweep endpoint.

Covers:
 - Orphan unread message injected directly in Mongo -> unread-count >=1
 - mark-all-read clears it (updated >=1) and unread-count becomes 0
 - Anonymous user gets {updated: 0}
"""
import os
import time
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://populus-bots.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

CHAT_A_EMAIL = "chat_a@test.it"
CHAT_A_PASSWORD = "test123"
ORPHAN_MSG_ID = "TEST_orphan_mark_all_read_1"


@pytest.fixture(scope="module")
def mongo_db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    # module-level cleanup safety net
    try:
        client[DB_NAME].messages.delete_one({"message_id": ORPHAN_MSG_ID})
    except Exception:
        pass
    client.close()


@pytest.fixture(scope="module")
def chat_a_auth():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": CHAT_A_EMAIL, "password": CHAT_A_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    user = data.get("user") or {}
    assert token, f"no token in login response: {data}"
    assert user.get("user_id"), f"no user_id in login response: {data}"
    return {"token": token, "user_id": user["user_id"]}


@pytest.fixture()
def inject_orphan(mongo_db, chat_a_auth):
    # ensure clean slate
    mongo_db.messages.delete_one({"message_id": ORPHAN_MSG_ID})
    mongo_db.messages.insert_one({
        "message_id": ORPHAN_MSG_ID,
        "sender_id": "ghost_user",
        "recipient_id": chat_a_auth["user_id"],
        "conversation_id": "test-orphan-conv",
        "text": "ping",
        "read_at": None,
        "deleted": False,
        "created_at": datetime.now(timezone.utc),
    })
    yield
    mongo_db.messages.delete_one({"message_id": ORPHAN_MSG_ID})


class TestMarkAllReadOrphanSweep:
    def test_orphan_bumps_unread_count(self, chat_a_auth, inject_orphan):
        h = {"Authorization": f"Bearer {chat_a_auth['token']}"}
        r = requests.get(f"{BASE_URL}/api/messages/unread-count", headers=h, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "count" in body
        assert body["count"] >= 1, f"expected orphan to bump count, got {body}"

    def test_mark_all_read_clears_orphan(self, chat_a_auth, inject_orphan):
        h = {"Authorization": f"Bearer {chat_a_auth['token']}"}
        # confirm >=1 unread
        pre = requests.get(f"{BASE_URL}/api/messages/unread-count", headers=h, timeout=10).json()
        assert pre["count"] >= 1

        r = requests.post(f"{BASE_URL}/api/messages/mark-all-read", headers=h, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "updated" in body
        assert body["updated"] >= 1, f"expected updated>=1, got {body}"

        # verify unread-count now 0
        time.sleep(0.2)
        post = requests.get(f"{BASE_URL}/api/messages/unread-count", headers=h, timeout=10).json()
        assert post["count"] == 0, f"expected 0 after sweep, got {post}"


class TestMarkAllReadAnonymous:
    def test_anonymous_returns_zero(self):
        nickname = f"anonMAR_{int(time.time())}"
        r = requests.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": nickname},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        anon = r.json()
        token = anon.get("token") or anon.get("access_token")
        assert token, anon
        h = {"Authorization": f"Bearer {token}"}
        r2 = requests.post(f"{BASE_URL}/api/messages/mark-all-read", headers=h, timeout=10)
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body == {"updated": 0}, f"anon should get updated:0, got {body}"


class TestMarkAllReadAuthGuards:
    def test_no_token_returns_401(self):
        r = requests.post(f"{BASE_URL}/api/messages/mark-all-read", timeout=10)
        assert r.status_code in (401, 403), r.text

    def test_idempotent_when_no_unread(self, chat_a_auth):
        h = {"Authorization": f"Bearer {chat_a_auth['token']}"}
        # first sweep to ensure nothing pending
        requests.post(f"{BASE_URL}/api/messages/mark-all-read", headers=h, timeout=10)
        r = requests.post(f"{BASE_URL}/api/messages/mark-all-read", headers=h, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("updated") == 0, f"expected 0 on second sweep, got {body}"
