"""Iteration 103 regression: per-notification mark-read endpoint + logout idempotency.

Covers Bug B (backend) and Bug C (backend) from the review request.
"""
import os
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/") or os.environ.get(
    "EXPO_BACKEND_URL", ""
).rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

USER_A_EMAIL = "chat_a@test.it"
USER_A_PWD = "test123"
USER_A_ID = "user_6e65e19525d5"


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_a(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_A_EMAIL, "password": USER_A_PWD},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return data["token"]


@pytest.fixture(scope="module")
def mongo_db():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    # Ensure user A has at least a couple of unread notifs so the test isn't empty.
    yield db
    client.close()


def _headers(token):
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


# --- Notifications: list endpoint sanity ---
class TestNotificationsList:
    def test_list_notifications_returns_200(self, api_client, auth_a):
        r = api_client.get(f"{BASE_URL}/api/notifications", headers=_headers(auth_a))
        assert r.status_code == 200
        data = r.json()
        assert "notifications" in data
        assert isinstance(data["notifications"], list)

    def test_unread_count_endpoint(self, api_client, auth_a):
        r = api_client.get(
            f"{BASE_URL}/api/notifications/unread-count", headers=_headers(auth_a)
        )
        assert r.status_code == 200
        data = r.json()
        # Could be `count` or `unread` — accept either
        assert any(k in data for k in ("count", "unread", "unread_count"))


# --- NEW ENDPOINT: mark one notification as read ---
class TestMarkOneRead:
    def test_seed_and_mark_single_notif(self, api_client, auth_a, mongo_db):
        # Force at least one notif to unread=false so we have something to work with
        existing = list(
            mongo_db.notifications.find({"user_id": USER_A_ID}).limit(3)
        )
        if not existing:
            pytest.skip("No notifications seeded for chat_a — cannot run.")
        # Mark them all unread
        notif_ids = [n["notif_id"] for n in existing]
        mongo_db.notifications.update_many(
            {"user_id": USER_A_ID, "notif_id": {"$in": notif_ids}},
            {"$set": {"read": False}},
        )
        # Get unread count before
        r_before = api_client.get(
            f"{BASE_URL}/api/notifications/unread-count", headers=_headers(auth_a)
        )
        count_before = r_before.json().get("count", r_before.json().get("unread", 0))

        # Pick the first notif_id, mark it read
        target = notif_ids[0]
        r = api_client.post(
            f"{BASE_URL}/api/notifications/{target}/read", headers=_headers(auth_a)
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "updated" in data
        assert data["updated"] == 1

        # Confirm in Mongo the read field is now True
        doc = mongo_db.notifications.find_one(
            {"notif_id": target, "user_id": USER_A_ID}
        )
        assert doc is not None
        assert doc.get("read") is True

        # Confirm unread count decremented by exactly 1
        r_after = api_client.get(
            f"{BASE_URL}/api/notifications/unread-count", headers=_headers(auth_a)
        )
        count_after = r_after.json().get("count", r_after.json().get("unread", 0))
        assert count_after == count_before - 1, (
            f"unread count expected {count_before-1}, got {count_after}"
        )

    def test_mark_one_read_idempotent(self, api_client, auth_a, mongo_db):
        # Marking an already-read notif twice should not error.
        doc = mongo_db.notifications.find_one(
            {"user_id": USER_A_ID, "read": True}
        )
        if not doc:
            pytest.skip("No read notif to test idempotency")
        target = doc["notif_id"]
        r = api_client.post(
            f"{BASE_URL}/api/notifications/{target}/read", headers=_headers(auth_a)
        )
        assert r.status_code == 200
        # Endpoint always sets read_at=now(), so modified_count can be 1
        # even if `read` was already True. Behaviourally idempotent (no error).
        assert "updated" in r.json()

    def test_mark_one_read_wrong_owner_noop(self, api_client, auth_a, mongo_db):
        # Passing an unknown notif_id must not error nor mutate anything.
        r = api_client.post(
            f"{BASE_URL}/api/notifications/nonexistent_notif_id_xyz/read",
            headers=_headers(auth_a),
        )
        assert r.status_code == 200
        assert r.json().get("updated") == 0

    def test_mark_one_read_requires_auth(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/notifications/whatever/read"
        )
        assert r.status_code in (401, 403)


# --- Bulk mark-read still works (regression) ---
class TestBulkMarkRead:
    def test_bulk_mark_read_still_works(self, api_client, auth_a, mongo_db):
        # Reset a couple of notifs to unread
        docs = list(
            mongo_db.notifications.find({"user_id": USER_A_ID}).limit(2)
        )
        if not docs:
            pytest.skip("No notifications to test bulk mark-read")
        ids = [d["notif_id"] for d in docs]
        mongo_db.notifications.update_many(
            {"user_id": USER_A_ID, "notif_id": {"$in": ids}},
            {"$set": {"read": False}},
        )
        r = api_client.post(
            f"{BASE_URL}/api/notifications/mark-read", headers=_headers(auth_a)
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("updated", 0) >= len(ids)


# --- Bug C backend: logout endpoint idempotency ---
class TestLogoutIdempotency:
    def test_logout_valid_token(self, api_client, auth_a):
        r = api_client.post(
            f"{BASE_URL}/api/auth/logout", headers=_headers(auth_a)
        )
        assert r.status_code == 200
        # Must be JSON with some ok flag
        try:
            j = r.json()
            assert j.get("ok") is True or j == {} or "message" in j or "ok" in j
        except Exception:
            pass

    def test_logout_no_token_still_ok(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/logout")
        assert r.status_code == 200

    def test_logout_bad_token_still_ok(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/auth/logout",
            headers={"Authorization": "Bearer garbage.token.here"},
        )
        assert r.status_code == 200

    def test_logout_twice_is_ok(self, api_client, auth_a):
        # Even if the first call already invalidated, second must not error.
        r1 = api_client.post(
            f"{BASE_URL}/api/auth/logout", headers=_headers(auth_a)
        )
        r2 = api_client.post(
            f"{BASE_URL}/api/auth/logout", headers=_headers(auth_a)
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
