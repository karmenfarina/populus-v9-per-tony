"""
Iteration 134 — Bot admin flow (BotPanel UX fix) + router regression.

Scope
──────────────────────────────────────────────────────────────────────
1) Bot admin endpoints (/api/admin/bots/*):
     - GET  /state              → snapshot contains all expected keys
     - POST /toggle             → enable / disable bots
     - POST /count              → set active_count
     - POST /burst              → burst updates last_burst_at
     - End-to-end sequence
2) Regression on newly-extracted routers:
     - /api/legal/{terms,nda}, /api/docs[/{slug}]
     - /api/sponsors[?category=…]
     - /api/favorites, /api/notifications, /api/users/me/blocks,
       /api/support/submit → 401 without auth (route wired correctly)

Auth
──────────────────────────────────────────────────────────────────────
Admin endpoints use header  X-Admin-Key: <ADMIN_TOKEN from backend/.env>.

Uses local backend URL (http://localhost:8001) as requested by the main
agent in the review payload.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import pytest
import requests

BASE_URL = "http://localhost:8001"
ADMIN_KEY = os.environ.get("ADMIN_TOKEN", "populus-admin-42b8f3")

ADMIN_HEADERS = {
    "X-Admin-Key": ADMIN_KEY,
    "Content-Type": "application/json",
}


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ═══════════════════════════════════════════════════════════════════
# 1) Bot admin endpoints
# ═══════════════════════════════════════════════════════════════════
class TestBotAdminEndpoints:
    """Chain of tests for /api/admin/bots/*"""

    def test_state_requires_admin_key(self, api):
        r = api.get(f"{BASE_URL}/api/admin/bots/state")
        assert r.status_code in (401, 403), (
            f"Expected auth error without X-Admin-Key, got {r.status_code}"
        )

    def test_state_returns_expected_shape(self, api):
        r = api.get(f"{BASE_URL}/api/admin/bots/state", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        data = r.json()
        for key in (
            "enabled",
            "active_count",
            "reported_active",
            "total_bots",
            "last_tick_at",
            "last_burst_at",
        ):
            assert key in data, f"missing key {key} in {data}"
        assert isinstance(data["enabled"], bool)
        assert isinstance(data["active_count"], int)
        assert isinstance(data["reported_active"], int)
        assert data["total_bots"] == 100, (
            f"Expected 100 seeded bots after Day-1 reset, got {data['total_bots']}"
        )

    def test_toggle_on(self, api):
        r = api.post(
            f"{BASE_URL}/api/admin/bots/toggle",
            headers=ADMIN_HEADERS,
            json={"enabled": True},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["enabled"] is True
        # active_count should be > 0 after enabling (engine defaults to 30 if 0)
        assert data["active_count"] > 0

    def test_set_count_25(self, api):
        r = api.post(
            f"{BASE_URL}/api/admin/bots/count",
            headers=ADMIN_HEADERS,
            json={"count": 25},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["active_count"] == 25
        # reported_active should update immediately (users.update_many is awaited)
        assert data["reported_active"] == 25

    def test_burst_bumps_last_burst_at(self, api):
        before_state = api.get(
            f"{BASE_URL}/api/admin/bots/state", headers=ADMIN_HEADERS
        ).json()
        before_ts = before_state.get("last_burst_at") or ""

        time.sleep(1.2)  # ensure timestamp diff (server uses UTC datetime)

        r = api.post(f"{BASE_URL}/api/admin/bots/burst", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        # Burst schedules run_initial_burst asynchronously, but sets
        # last_burst_at synchronously inside run_initial_burst before
        # ticking; give it a moment.
        time.sleep(2)

        after_state = api.get(
            f"{BASE_URL}/api/admin/bots/state", headers=ADMIN_HEADERS
        ).json()
        after_ts = after_state.get("last_burst_at") or ""
        assert after_ts, "last_burst_at should be populated after burst"
        assert after_ts != before_ts, (
            f"last_burst_at should have advanced (before={before_ts}, after={after_ts})"
        )

    def test_count_boundary_validation(self, api):
        # Pydantic Field(ge=0, le=100) — 150 must be rejected.
        r = api.post(
            f"{BASE_URL}/api/admin/bots/count",
            headers=ADMIN_HEADERS,
            json={"count": 150},
        )
        assert r.status_code == 422, r.text

    def test_toggle_off_restores_offline_state(self, api):
        r = api.post(
            f"{BASE_URL}/api/admin/bots/toggle",
            headers=ADMIN_HEADERS,
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["enabled"] is False
        # After OFF, engine sets all bot_active=False → reported_active == 0
        assert data["reported_active"] == 0
        # active_count config value is retained (persisted, not reset)
        assert data["active_count"] == 25


# ═══════════════════════════════════════════════════════════════════
# 2) Regression — extracted routers respond
# ═══════════════════════════════════════════════════════════════════
class TestExtractedRoutersRegression:
    """Verify extracted routers are still mounted and responding."""

    # ─── Legal & Docs ────────────────────────────────────────────
    def test_legal_terms(self, api):
        r = api.get(f"{BASE_URL}/api/legal/terms")
        assert r.status_code == 200
        j = r.json()
        assert "version" in j and "text" in j
        assert len(j["text"]) > 100  # non-empty markdown

    def test_legal_nda(self, api):
        r = api.get(f"{BASE_URL}/api/legal/nda")
        assert r.status_code == 200
        j = r.json()
        assert "version" in j and "text" in j
        assert len(j["text"]) > 50

    def test_docs_list(self, api):
        r = api.get(f"{BASE_URL}/api/docs")
        assert r.status_code == 200
        docs = r.json().get("docs", [])
        slugs = {d["slug"] for d in docs}
        assert {"regole", "algoritmo-ai", "architettura"}.issubset(slugs)

    def test_docs_by_slug(self, api):
        r = api.get(f"{BASE_URL}/api/docs/regole")
        assert r.status_code == 200
        j = r.json()
        assert j["slug"] == "regole"
        assert len(j.get("text", "")) > 50

    def test_docs_by_slug_404(self, api):
        r = api.get(f"{BASE_URL}/api/docs/nonexistent-doc")
        assert r.status_code == 404

    # ─── Sponsors ────────────────────────────────────────────────
    def test_sponsors_list(self, api):
        r = api.get(f"{BASE_URL}/api/sponsors")
        assert r.status_code == 200
        sponsors = r.json().get("sponsors", [])
        assert len(sponsors) >= 5, f"expected at least 5 seeded sponsors, got {len(sponsors)}"
        for s in sponsors:
            assert "sponsor_id" in s
            assert "category" in s
            assert "sponsor" in s
            assert "_id" not in s  # ObjectId must be excluded

    def test_sponsors_filter_by_category(self, api):
        r = api.get(f"{BASE_URL}/api/sponsors", params={"category": "politica"})
        assert r.status_code == 200
        sponsors = r.json().get("sponsors", [])
        assert len(sponsors) >= 1
        assert all(s["category"] == "politica" for s in sponsors)

    # ─── Favorites / Notifications / Blocks / Support (auth-gated) ───
    def test_favorites_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/favorites")
        assert r.status_code == 401

    def test_notifications_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/notifications")
        assert r.status_code == 401

    def test_notifications_unread_count_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/notifications/unread-count")
        assert r.status_code == 401

    def test_blocks_list_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/users/me/blocks")
        assert r.status_code == 401

    def test_block_user_requires_auth(self, api):
        r = api.post(f"{BASE_URL}/api/users/some-uid/block")
        assert r.status_code == 401

    def test_support_submit_requires_auth(self, api):
        r = api.post(f"{BASE_URL}/api/support/submit", json={"message": "x"})
        assert r.status_code == 401

    def test_add_favorite_requires_auth(self, api):
        r = api.post(f"{BASE_URL}/api/feuds/nonexistent/favorite")
        assert r.status_code == 401
