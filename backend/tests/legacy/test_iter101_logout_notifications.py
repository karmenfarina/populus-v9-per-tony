"""Iteration 101 regression tests.

Coverage:
- /api/auth/logout — 200 for valid token, idempotent for invalid / missing token
- /api/notifications — returns list with `read` boolean per notification
- /api/notifications/mark-read — bulk marks all notifications as read
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")


def _login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def token_a() -> str:
    return _login("chat_a@test.it", "test123")


@pytest.fixture(scope="module")
def token_b() -> str:
    return _login("chat_b@test.it", "test123")


class TestAuthLogout:
    """/api/auth/logout must respond 200 for any request (idempotent)."""

    def test_logout_valid_token(self):
        tok = _login("chat_a@test.it", "test123")  # ephemeral, we destroy it here
        r = requests.post(
            f"{BASE_URL}/api/auth/logout",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    def test_logout_invalid_token(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/logout",
            headers={"Authorization": "Bearer not_a_real_token_xxxxxx"},
            timeout=10,
        )
        # Must not error — idempotent behaviour.
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    def test_logout_no_token(self):
        r = requests.post(f"{BASE_URL}/api/auth/logout", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True


class TestNotifications:
    """Regression on notifications list + mark-read endpoints."""

    def test_notifications_list_returns_read_field(self, token_a):
        r = requests.get(
            f"{BASE_URL}/api/notifications",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "notifications" in body
        assert isinstance(body["notifications"], list)
        # If the account has any notifications, every entry must have a
        # boolean `read` field (drives the red-border rendering client-side).
        for n in body["notifications"]:
            assert "notif_id" in n
            assert "read" in n
            assert isinstance(n["read"], bool)
            assert "type" in n
            assert "created_at" in n

    def test_mark_read_bulk(self, token_a):
        # Attempt to mark-read; must return 200 even with 0 unread.
        r = requests.post(
            f"{BASE_URL}/api/notifications/mark-read",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        # After mark-read, all notifications must be `read: true`.
        r2 = requests.get(
            f"{BASE_URL}/api/notifications",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=15,
        )
        assert r2.status_code == 200
        for n in r2.json().get("notifications", []):
            assert n["read"] is True, f"notif {n.get('notif_id')} still unread after mark-read"

    def test_unread_count_endpoint(self, token_a):
        r = requests.get(
            f"{BASE_URL}/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        # After the previous mark-read, unread should be 0.
        assert isinstance(body.get("count"), int)
        assert body["count"] == 0

    def test_unread_appears_after_reply(self, token_a, token_b):
        """End-to-end: user B replies to a comment authored by A —> A gets
        a notification with `read: false` on the next fetch."""
        # 1) Fetch a feud that already has some content for A.
        rf = requests.get(f"{BASE_URL}/api/feuds?category=all", timeout=15)
        assert rf.status_code == 200
        feuds = rf.json().get("feuds") or []
        if not feuds:
            pytest.skip("No feuds available to seed comment")
        feud_id = feuds[0]["feud_id"]

        # 2) A posts a comment on side A.
        c1 = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/comments",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"side": "A", "text": "TEST_iter101 seed comment for reply test"},
            timeout=15,
        )
        if c1.status_code != 200:
            pytest.skip(f"could not post seed comment: {c1.status_code} {c1.text}")
        comment_id = c1.json().get("comment", {}).get("comment_id") or c1.json().get("comment_id")
        if not comment_id:
            pytest.skip("comment id missing in response")

        # 3) B replies to A's comment.
        c2 = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/comments",
            headers={"Authorization": f"Bearer {token_b}"},
            json={
                "side": "A",
                "text": "TEST_iter101 reply from B to trigger notification",
                "reply_to": comment_id,
            },
            timeout=15,
        )
        if c2.status_code != 200:
            pytest.skip(f"could not post reply: {c2.status_code} {c2.text}")

        # Small delay so any async notification insertion settles.
        time.sleep(1.2)

        # 4) A should have at least one unread notification with `read: false`.
        r = requests.get(
            f"{BASE_URL}/api/notifications",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=15,
        )
        assert r.status_code == 200
        notifs = r.json().get("notifications", [])
        unread = [n for n in notifs if not n.get("read")]
        assert len(unread) >= 1, "expected at least one unread notification after reply"
        # Structural expectation: the fresh unread notif references the
        # feud we posted into so the UI can deep-link (used by the red
        # border + tap-to-open flow).
        matching = [n for n in unread if n.get("feud_id") == feud_id]
        assert len(matching) >= 1, "no unread notification pointing to seeded feud"
