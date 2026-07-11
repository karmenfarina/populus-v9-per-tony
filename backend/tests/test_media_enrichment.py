"""Backend tests for the media enrichment feature (iteration 19).

Covers:
- POST /api/admin/backfill_media auth and success behavior
- Media object shape on feuds after scheduler/backfill
- Real news-specific OG images (non-Unsplash)
- Regression: GET /api/feuds/{id} 410 for missing, POST vote snapshot
"""
import os
import requests
import pytest
from urllib.parse import urlparse

BASE_URL = "http://localhost:8001"
ADMIN_KEY = "populus-admin-42b8f3"


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---- Backfill media endpoint ----

class TestBackfillMedia:
    def test_backfill_requires_admin(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/backfill_media?force=true&limit=5")
        assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"

    def test_backfill_wrong_admin(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/admin/backfill_media?force=true&limit=5",
            headers={"X-Admin-Key": "wrong-token"},
        )
        assert r.status_code == 401

    def test_backfill_with_admin_success(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/admin/backfill_media?force=true&limit=5",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=120,
        )
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "scanned" in data
        assert "updated" in data
        assert isinstance(data["scanned"], int)
        assert isinstance(data["updated"], int)


# ---- Media object shape ----

def _all_feuds(api_client):
    """Collect feuds from live + archive endpoints."""
    feuds = []
    r = api_client.get(f"{BASE_URL}/api/feuds")
    if r.status_code == 200:
        feuds.extend(r.json().get("feuds", []))
    # try last 7 days of archive
    from datetime import datetime, timedelta, timezone
    for i in range(1, 8):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        r = api_client.get(f"{BASE_URL}/api/feuds/archive?date={d}")
        if r.status_code == 200:
            feuds.extend(r.json().get("feuds", []))
    return feuds


class TestMediaObjectShape:
    def test_at_least_one_youtube_media(self, api_client):
        feuds = _all_feuds(api_client)
        assert feuds, "no feuds in the DB, cannot verify media"
        yt_feuds = [f for f in feuds if isinstance(f.get("media"), dict) and f["media"].get("type") == "youtube"]
        assert yt_feuds, f"expected at least one feud with media.type=youtube; scanned={len(feuds)}"
        m = yt_feuds[0]["media"]
        vid = m.get("video_id")
        assert isinstance(vid, str) and len(vid) == 11, f"invalid video_id: {vid}"
        embed = m.get("embed_url") or ""
        assert embed.startswith("https://www.youtube-nocookie.com/embed/"), f"bad embed_url: {embed}"

    def test_at_least_one_real_og_image(self, api_client):
        feuds = _all_feuds(api_client)
        assert feuds, "no feuds in the DB"
        real = [f for f in feuds if f.get("image_url") and "images.unsplash.com" not in f["image_url"]]
        assert real, "no feud has a non-Unsplash image_url (OG extraction seems broken)"

    def test_get_feud_returns_media_field(self, api_client):
        feuds = _all_feuds(api_client)
        yt_feuds = [f for f in feuds if isinstance(f.get("media"), dict) and f["media"].get("type") == "youtube"]
        if not yt_feuds:
            pytest.skip("no youtube feud")
        fid = yt_feuds[0]["feud_id"]
        r = api_client.get(f"{BASE_URL}/api/feuds/{fid}")
        assert r.status_code == 200
        data = r.json()["feud"]
        assert "media" in data
        assert data["media"] is not None
        assert data["media"].get("type") == "youtube"
        assert data["media"].get("embed_url", "").startswith("https://www.youtube-nocookie.com/embed/")


# ---- Regressions ----

class TestRegressions:
    def test_get_nonexistent_feud_returns_410(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/feuds/feud_doesnotexist999")
        assert r.status_code == 410, f"expected 410, got {r.status_code}"

    def test_vote_still_populates_snapshot(self, api_client):
        # signup user
        import uuid
        email = f"TEST_media_{uuid.uuid4().hex[:8]}@ex.com"
        r = api_client.post(f"{BASE_URL}/api/auth/signup", json={
            "email": email, "password": "testpass123", "nickname": f"tm{uuid.uuid4().hex[:6]}"
        })
        assert r.status_code == 200, r.text
        token = r.json()["token"]

        # get one active feud
        r = api_client.get(f"{BASE_URL}/api/feuds")
        feuds = r.json().get("feuds", [])
        if not feuds:
            pytest.skip("no active feud available for voting")
        fid = feuds[0]["feud_id"]

        # vote
        r = api_client.post(
            f"{BASE_URL}/api/feuds/{fid}/vote",
            json={"side": "A"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        # verify snapshot in DB by querying share (which reads feud, but we need the vote)
        # check user history has the item (relies on feud_snapshot indirectly)
        r = api_client.get(
            f"{BASE_URL}/api/users/me/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        hist = r.json()["history"]
        assert any(h["feud_id"] == fid for h in hist), "vote not in history"
