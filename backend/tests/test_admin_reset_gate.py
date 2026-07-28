"""Regression tests for iter 80 - admin panel changes.

Coverage:
  - /api/auth/me works with the pre-baked owner JWT (email gate positive path)
  - Admin analytics overview requires X-Admin-Key
  - Admin analytics reset endpoint (POST) resets counters
  - Baseline_at is refreshed after a reset
"""
import os
import time

import pytest
import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")

OWNER_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyXzEzZjkzY2JkMWVhOSIsImlhdCI6MTc4NTI1MDkwOSwiZXhwIjoxNzg1ODU1NzA5fQ."
    "44FCxPrwqZtyf_OkCXZF-luFvVFXoWogZqLk_Ps55Yo"
)
ADMIN_KEY = "populus-admin-42b8f3"


@pytest.fixture
def api():
    s = requests.Session()
    return s


class TestOwnerAuth:
    def test_owner_jwt_returns_owner_email(self, api):
        r = api.get(f"{BASE}/api/auth/me", headers={"Authorization": f"Bearer {OWNER_JWT}"})
        assert r.status_code == 200, r.text
        data = r.json()
        # user is nested under "user"
        u = data.get("user") or data
        assert u["email"].lower() == "carlofarinapayme@gmail.com"
        assert u["user_id"] == "user_13f93cbd1ea9"


class TestAdminKeyGuard:
    def test_overview_without_key_rejected(self, api):
        r = api.get(f"{BASE}/api/admin/analytics/overview")
        assert r.status_code in (401, 403), r.status_code

    def test_overview_with_key_ok(self, api):
        r = api.get(
            f"{BASE}/api/admin/analytics/overview",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r.status_code == 200, r.text
        j = r.json()
        # Must contain the shape the frontend expects
        assert "users" in j
        assert "engagement" in j
        assert "active_users" in j


class TestAdminResetFlow:
    """Reset endpoint moves baseline forward and zeroes counters (relative)."""

    def test_reset_endpoint(self, api):
        # Snapshot baseline before
        r0 = api.get(
            f"{BASE}/api/admin/analytics/overview",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r0.status_code == 200
        baseline_before = r0.json().get("baseline_at")

        # Give timestamps a tick so we can see baseline change
        time.sleep(1.1)

        # Reset
        r = api.post(
            f"{BASE}/api/admin/analytics/reset",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r.status_code == 200, r.text

        # Verify: baseline_at should have advanced
        r2 = api.get(
            f"{BASE}/api/admin/analytics/overview",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r2.status_code == 200
        baseline_after = r2.json().get("baseline_at")
        assert baseline_after and baseline_after != baseline_before, (
            f"baseline did not advance: before={baseline_before} after={baseline_after}"
        )

    def test_reset_requires_admin_key(self, api):
        r = api.post(f"{BASE}/api/admin/analytics/reset")
        assert r.status_code in (401, 403), r.status_code


class TestSnapshotEndpoints:
    """All endpoints used by fetchFullSnapshot() in admin.tsx."""

    @pytest.mark.parametrize("path", [
        "/api/admin/analytics/overview",
        "/api/admin/analytics/active-users?days=30",
        "/api/admin/analytics/retention",
        "/api/admin/analytics/deep-action-rate?days=7",
        "/api/admin/analytics/top-feuds-24h",
        "/api/admin/analytics/categories",
        "/api/admin/analytics/profiles",
        "/api/admin/analytics/funnel",
        "/api/admin/analytics/dev-accounts",
        "/api/admin/stats",
    ])
    def test_snapshot_endpoints_reachable(self, api, path):
        r = api.get(f"{BASE}{path}", headers={"X-Admin-Key": ADMIN_KEY})
        assert r.status_code == 200, f"{path} → HTTP {r.status_code}: {r.text[:200]}"
