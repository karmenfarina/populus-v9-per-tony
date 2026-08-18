"""Admin stats and admin-protected endpoints tests."""
import os
import uuid
import pytest
import requests

BASE_URL = "https://feud-admin-panel.preview.emergentagent.com"
ADMIN_KEY = "populus-admin-42b8f3"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _signup_anon(api, nickname):
    r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nickname})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]


class TestAdminAuth:
    """Admin endpoint auth checks"""

    def test_stats_no_key_returns_401(self, api):
        r = api.get(f"{BASE_URL}/api/admin/stats")
        assert r.status_code == 401
        assert "Chiave admin non valida" in r.json().get("detail", "")

    def test_stats_wrong_key_returns_401(self, api):
        r = api.get(f"{BASE_URL}/api/admin/stats", headers={"X-Admin-Key": "wrong-key"})
        assert r.status_code == 401
        assert "Chiave admin non valida" in r.json().get("detail", "")

    def test_generate_daily_no_key_returns_401(self, api):
        r = api.post(f"{BASE_URL}/api/admin/generate-daily")
        assert r.status_code == 401

    def test_generate_daily_wrong_key_returns_401(self, api):
        r = api.post(f"{BASE_URL}/api/admin/generate-daily",
                     headers={"X-Admin-Key": "wrong-key"})
        assert r.status_code == 401


class TestAdminStatsShape:
    """Validate the shape of /admin/stats"""

    def test_stats_valid_key_returns_200_with_expected_shape(self, api):
        r = api.get(f"{BASE_URL}/api/admin/stats",
                    headers={"X-Admin-Key": ADMIN_KEY})
        assert r.status_code == 200, r.text
        data = r.json()
        # Required top-level fields
        for k in ["total_users", "onboarded_users", "total_votes",
                  "by_region", "by_sex", "by_age", "top_feuds"]:
            assert k in data, f"missing {k}"
        # Numeric non-negative
        assert isinstance(data["total_users"], int) and data["total_users"] >= 0
        assert isinstance(data["onboarded_users"], int) and data["onboarded_users"] >= 0
        assert isinstance(data["total_votes"], int) and data["total_votes"] >= 0
        # by_region list, sorted desc
        assert isinstance(data["by_region"], list)
        counts = [x["count"] for x in data["by_region"]]
        assert counts == sorted(counts, reverse=True)
        # by_sex dict with expected keys
        assert isinstance(data["by_sex"], dict)
        for k in ["F", "M", "other", "na", "unknown"]:
            assert k in data["by_sex"]
        # by_age dict with expected buckets
        assert isinstance(data["by_age"], dict)
        for k in ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+", "unknown"]:
            assert k in data["by_age"]
        # top_feuds list <= 5
        assert isinstance(data["top_feuds"], list)
        assert len(data["top_feuds"]) <= 5
        if data["top_feuds"]:
            tf = data["top_feuds"][0]
            for k in ["feud_id", "title", "party_a", "party_b",
                     "pct_a", "pct_b", "category_label"]:
                assert k in tf, f"missing top_feuds field {k}"


class TestAdminStatsIntegration:
    """End-to-end: onboarded user votes, stats reflect it."""

    def test_demographics_reflected_after_vote(self, api):
        # Baseline
        r0 = api.get(f"{BASE_URL}/api/admin/stats",
                     headers={"X-Admin-Key": ADMIN_KEY}).json()
        base_users = r0["total_users"]
        base_lomb = next((x["count"] for x in r0["by_region"]
                          if x["region"] == "Lombardia"), 0)
        base_f = r0["by_sex"].get("F", 0)
        base_2534 = r0["by_age"].get("25-34", 0)

        # Sign up 2 anonymous users
        t1, u1 = _signup_anon(api, f"TEST_{uuid.uuid4().hex[:6]}")
        t2, u2 = _signup_anon(api, f"TEST_{uuid.uuid4().hex[:6]}")

        # Onboard user 1 (age 28, F, Lombardia, politica)
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers={"Authorization": f"Bearer {t1}",
                     "Content-Type": "application/json"},
            json={"age": 28, "sex": "F", "region": "Lombardia",
                  "favorite_categories": ["politica"]},
        )
        assert r.status_code == 200, r.text

        # Get a feud to vote on
        feeds = requests.get(f"{BASE_URL}/api/feuds").json()["feuds"]
        assert feeds, "no feuds available to vote on"
        feud_id = feeds[0]["feud_id"]

        # user 1 votes A
        rv = requests.post(
            f"{BASE_URL}/api/feuds/{feud_id}/vote",
            headers={"Authorization": f"Bearer {t1}",
                     "Content-Type": "application/json"},
            json={"side": "A"},
        )
        assert rv.status_code == 200, rv.text

        # Fetch stats and assert
        r2 = api.get(f"{BASE_URL}/api/admin/stats",
                     headers={"X-Admin-Key": ADMIN_KEY}).json()
        assert r2["total_users"] >= base_users + 2
        lomb = next((x["count"] for x in r2["by_region"]
                     if x["region"] == "Lombardia"), 0)
        assert lomb >= base_lomb + 1, f"Lombardia count did not increase: {lomb} vs base {base_lomb}"
        assert r2["by_sex"].get("F", 0) >= base_f + 1
        assert r2["by_age"].get("25-34", 0) >= base_2534 + 1


class TestGenerateDailyAuth:
    """Ensure generate-daily is now admin-protected and returns 200 with key."""

    def test_generate_daily_with_valid_key_returns_200(self, api):
        # count=1 to keep it fast; may still take a while due to LLM
        r = api.post(f"{BASE_URL}/api/admin/generate-daily?count=1",
                     headers={"X-Admin-Key": ADMIN_KEY}, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "created" in data
        assert isinstance(data["created"], list)
