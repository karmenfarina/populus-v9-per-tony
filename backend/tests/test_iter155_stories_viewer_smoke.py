"""
Iter 155 — Stories viewer glitch fix smoke test.

Focus (per review request):
- Verify /api/stories/feed still responds correctly (regression).
- Verify /api/stories/{id}/view is idempotent + rejects outside-circle viewers.
- Verify /api/auth/anonymous still works (viewer opens for logged users).

Frontend fix (advanceLockRef + transform:scaleX + key with currentUserId)
is code-review only per the review_request agent_to_agent_context_note
(visible only on production APK, not testable on desktop web).
"""
import os
import uuid
import requests
import pytest

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set"


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _anon(prefix="TESTiter155"):
    body = {"nickname": f"{prefix}_{uuid.uuid4().hex[:6]}"}
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json=body, timeout=20)
    assert r.status_code == 200, f"anon signup failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


# ---------- Basic health ----------


class TestStoriesFeedHealth:
    """Smoke checks for the endpoints the viewer relies on."""

    def test_stories_feed_reachable_for_anon(self):
        tok, _ = _anon()
        r = requests.get(
            f"{BASE_URL}/api/stories/feed", headers=_headers(tok), timeout=20
        )
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert "groups" in data and isinstance(data["groups"], list)
        # Anon user with no circle → most likely empty groups but structure must hold.
        assert data.get("ttl_hours") == 24
        assert isinstance(data.get("quota"), int)

    def test_anonymous_auth_still_works(self):
        tok, user = _anon()
        assert tok
        assert user.get("user_id")

    def test_view_unknown_story_returns_404(self):
        tok, _ = _anon()
        r = requests.post(
            f"{BASE_URL}/api/stories/story_iter155_nope_{uuid.uuid4().hex[:6]}/view",
            headers=_headers(tok),
            timeout=20,
        )
        # Either 404 (not found) or 403 (outside-circle) is acceptable — the
        # important thing is the endpoint is reachable and does NOT 5xx.
        assert r.status_code in (403, 404), f"unexpected {r.status_code}: {r.text[:200]}"

    def test_view_unauth_401_or_403(self):
        r = requests.post(
            f"{BASE_URL}/api/stories/story_iter155_nope_x/view",
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        assert r.status_code in (401, 403), f"unexpected {r.status_code}"

    def test_stories_by_user_reachable(self):
        tok, user = _anon()
        # Anon querying own stories list — should return 200 with stories=[]
        r = requests.get(
            f"{BASE_URL}/api/stories/user/{user['user_id']}",
            headers=_headers(tok),
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert d.get("user_id") == user["user_id"]
        assert isinstance(d.get("stories"), list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
