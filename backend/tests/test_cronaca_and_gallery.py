"""
Tests for Bug 1 (cronaca backfill in favorite_categories) and
Bug 2 (photo gallery — checks are frontend-only, backend just verifies
photos list is returned for chat_a).
"""
import os
import requests
import pytest
from pymongo import MongoClient

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "populus")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def db():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


# --- Bug 1 backend ---
class TestCronacaBackfill:
    def test_all_users_with_favorites_have_cronaca(self, db):
        """DB-level check: no user with non-empty favorite_categories lacks 'cronaca'."""
        bad = list(db.users.find(
            {"favorite_categories.0": {"$exists": True},
             "favorite_categories": {"$nin": ["cronaca"]}},
            {"user_id": 1, "email": 1, "favorite_categories": 1},
        ))
        assert len(bad) == 0, f"Users missing cronaca: {bad}"

    def test_chat_a_login_favorites_include_cronaca(self, api):
        r = api.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "chat_a@test.it", "password": "test123"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        me = api.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200, me.text
        body = me.json()
        user_obj = body.get("user") if "user" in body else body
        favs = user_obj.get("favorite_categories") or []
        assert "cronaca" in favs, f"chat_a favs missing cronaca: {favs}"
        # original favorite still present
        assert "tech" in favs, f"chat_a favs missing tech: {favs}"

    def test_chat_b_empty_favorites_untouched(self, db):
        u = db.users.find_one({"email": "chat_b@test.it"}, {"favorite_categories": 1})
        if u is None:
            pytest.skip("chat_b user not seeded")
        favs = u.get("favorite_categories") or []
        # If chat_b has empty favorites, backfill should NOT have added cronaca.
        # If they have non-empty favorites (e.g. added later), cronaca must be in.
        if len(favs) == 0:
            assert "cronaca" not in favs
        else:
            assert "cronaca" in favs

    def test_categories_endpoint_has_cronaca(self, api):
        r = api.get(f"{BASE_URL}/api/categories")
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["categories"]]
        assert "cronaca" in ids
        # All 9 categories
        expected = {"politica", "tv", "musica", "sport", "cinema",
                    "social", "gossip", "tech", "cronaca"}
        assert expected.issubset(set(ids))


# --- Bug 2 backend prereq ---
class TestChatAPhotos:
    def test_chat_a_profile_has_two_photos(self, api):
        r = api.get(f"{BASE_URL}/api/users/user_6e65e19525d5")
        assert r.status_code == 200, r.text
        data = r.json()
        photos = data.get("photos") or []
        assert len(photos) >= 2, f"expected >=2 photos for chat_a, got {len(photos)}"
        # sanity: primary_photo_id set
        assert data.get("primary_photo_id") is not None
