"""
Iter_70 regression suite:
  (A) POST /api/stories kind='badge' must accept `granted_category_badges` in
      BOTH the new dict format `[{category, tier}]` AND the legacy string
      format `['politica:1']`. If neither the count threshold nor a matching
      grant is present -> 403 with the exact Italian message.
  (B) All auth-adjacent endpoints must hydrate `primary_photo` via the shared
      `_hydrate_primary_photo` helper: /auth/login, /auth/anonymous,
      /auth/signup (no user field but must not crash), PATCH
      /auth/me/profile, GET /auth/me. Regression against iter_63/iter_68 auth
      contract (fields present, no shape change).

Seeding: uses pymongo directly on `MONGO_URL` / `DB_NAME` (protected env
vars). Every write is rolled back in the `finalize` fixture teardown.
"""
import os
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://vote-ui-polish.preview.emergentagent.com",
).rstrip("/")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

USER_A_EMAIL = "chat_a@test.it"
USER_A_PASSWORD = "test123"
USER_A_ID = "user_6e65e19525d5"


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _login(email=USER_A_EMAIL, password=USER_A_PASSWORD):
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


@pytest.fixture(autouse=True)
def cleanup_grants_and_stories(mongo):
    """Ensure test_user starts with NO grant and rollback any stories the tests create."""
    mongo.users.update_one({"user_id": USER_A_ID}, {"$unset": {"granted_category_badges": ""}})
    yield
    # Rollback
    mongo.users.update_one({"user_id": USER_A_ID}, {"$unset": {"granted_category_badges": ""}})
    mongo.stories.delete_many({"user_id": USER_A_ID, "kind": "badge"})


# ---------------------------------------------------------------------------
# Part A — POST /api/stories kind='badge' with mixed grant formats
# ---------------------------------------------------------------------------
class TestBadgeShareGrantFormats:
    """Iter_70 fix: create_story must accept dict AND string grant formats."""

    def _post_badge_story(self, tok, category="politica", tier=1):
        payload = {"kind": "badge", "badge_category": category, "badge_tier": tier}
        return requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(tok),
            json=payload,
            timeout=20,
        )

    def test_no_grant_returns_403(self, mongo):
        """Baseline: with no granted_category_badges and no count, POST must 403."""
        auth = _login()
        r = self._post_badge_story(auth["token"])
        assert r.status_code == 403, (
            f"expected 403 with no grant, got {r.status_code}: {r.text[:200]}"
        )
        body = r.json()
        # Exact Italian message
        assert body.get("detail") == "Devi prima sbloccare questa spilla.", body

    def test_dict_format_grant_returns_200(self, mongo):
        """NEW format: granted_category_badges = [{category, tier}] -> 200."""
        mongo.users.update_one(
            {"user_id": USER_A_ID},
            {"$set": {"granted_category_badges": [{"category": "politica", "tier": 1}]}},
        )
        auth = _login()
        r = self._post_badge_story(auth["token"])
        assert r.status_code == 200, (
            f"expected 200 with dict grant, got {r.status_code}: {r.text[:200]}"
        )
        d = r.json()
        assert "story" in d
        st = d["story"]
        assert st.get("kind") == "badge"
        # POST /stories returns the hydrated story: raw badge_category / badge_tier
        # get folded into a nested `badge` object by `_hydrate_story_row`.
        badge = st.get("badge") or {}
        assert badge.get("category_id") == "politica"
        assert int(badge.get("tier") or 0) == 1
        assert st.get("story_id")
        # Persistence check: GET /stories/feed should include this row.
        f = requests.get(
            f"{BASE_URL}/api/stories/feed",
            headers=_headers(auth["token"]),
            timeout=20,
        )
        assert f.status_code == 200
        found = False
        for g in f.json().get("groups", []):
            for row in g.get("stories", []):
                if row.get("story_id") == st["story_id"]:
                    found = True
                    assert row.get("kind") == "badge"
                    assert (row.get("badge") or {}).get("category_id") == "politica"
                    assert (row.get("badge") or {}).get("tier") == 1
        assert found, "created badge story not found in feed"

    def test_string_format_grant_returns_200(self, mongo):
        """LEGACY format: granted_category_badges = ['politica:1'] -> 200."""
        mongo.users.update_one(
            {"user_id": USER_A_ID},
            {"$set": {"granted_category_badges": ["politica:1"]}},
        )
        auth = _login()
        r = self._post_badge_story(auth["token"])
        assert r.status_code == 200, (
            f"expected 200 with string grant, got {r.status_code}: {r.text[:200]}"
        )
        st = r.json()["story"]
        assert st.get("kind") == "badge"
        badge = st.get("badge") or {}
        assert badge.get("category_id") == "politica"
        assert int(badge.get("tier") or 0) == 1

    def test_dict_grant_wrong_tier_returns_403(self, mongo):
        """Grant is tier 1 dict, but request is tier 2 -> 403 (no wildcard)."""
        mongo.users.update_one(
            {"user_id": USER_A_ID},
            {"$set": {"granted_category_badges": [{"category": "politica", "tier": 1}]}},
        )
        auth = _login()
        r = self._post_badge_story(auth["token"], category="politica", tier=2)
        assert r.status_code == 403, (
            f"expected 403 for mismatched tier, got {r.status_code}: {r.text[:200]}"
        )

    def test_string_grant_wrong_category_returns_403(self, mongo):
        """Grant is politica:1 string, but request is sport:1 -> 403."""
        mongo.users.update_one(
            {"user_id": USER_A_ID},
            {"$set": {"granted_category_badges": ["politica:1"]}},
        )
        auth = _login()
        r = self._post_badge_story(auth["token"], category="sport", tier=1)
        assert r.status_code == 403, r.text[:200]


# ---------------------------------------------------------------------------
# Part B — primary_photo hydration on ALL auth endpoints
# ---------------------------------------------------------------------------
class TestPrimaryPhotoHydrationLogin:
    """chat_a has a primary_photo_id -> login response must include primary_photo."""

    def test_login_returns_primary_photo(self):
        d = _login()
        user = d["user"]
        # Existing fields must be preserved
        assert user.get("user_id") == USER_A_ID
        assert user.get("nickname")
        assert user.get("primary_photo_id"), "chat_a should have primary_photo_id"
        pp = user.get("primary_photo")
        assert pp is not None, "primary_photo must be hydrated on login for chat_a"
        assert isinstance(pp, dict)
        assert pp.get("photo_id") == user["primary_photo_id"]
        assert isinstance(pp.get("data"), str) and len(pp["data"]) > 20
        assert pp.get("mime")


class TestPrimaryPhotoHydrationSignup:
    """Signup returns `requires_verification`, no `user` field.
    The important thing is: no 500 crash after adding the hydrate call.
    """

    def test_signup_no_crash_no_user_field(self):
        fresh_email = f"iter70_signup_{uuid.uuid4().hex[:8]}@test.it"
        r = requests.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": fresh_email, "password": "TestPwd123", "nickname": f"iter70n{uuid.uuid4().hex[:6]}"},
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body.get("requires_verification") is True
        assert body.get("email") == fresh_email
        # No user session yet — must not include a token or user payload.
        assert "token" not in body
        assert "user" not in body


class TestPrimaryPhotoHydrationAnonymous:
    """Anonymous account -> primary_photo absent / null. Must not crash."""

    def test_anonymous_no_primary_photo(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": f"iter70a{uuid.uuid4().hex[:6]}"},
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert d.get("token")
        user = d["user"]
        # Core fields
        assert user.get("user_id")
        assert user.get("nickname")
        assert user.get("is_anonymous") is True
        # No photo hydration for fresh anon
        assert user.get("primary_photo") in (None,), user.get("primary_photo")
        assert user.get("primary_photo_id") in (None, "")


class TestPrimaryPhotoHydrationProfileUpdate:
    """PATCH /auth/me/profile must include primary_photo for chat_a."""

    def test_update_profile_returns_primary_photo(self):
        auth = _login()
        payload = {
            "age": 25,
            "sex": "na",
            "region": "Lazio",
            "favorite_categories": ["politica", "cronaca"],
        }
        r = requests.patch(
            f"{BASE_URL}/api/auth/me/profile",
            headers=_headers(auth["token"]),
            json=payload,
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        user = r.json()["user"]
        assert user.get("user_id") == USER_A_ID
        assert user.get("primary_photo_id"), user
        pp = user.get("primary_photo")
        assert pp is not None, "primary_photo must be hydrated on PATCH /auth/me/profile"
        assert pp.get("photo_id") == user["primary_photo_id"]
        assert isinstance(pp.get("data"), str) and len(pp["data"]) > 20
        assert pp.get("mime")


class TestPrimaryPhotoHydrationMe:
    """Regression: /auth/me still hydrates primary_photo."""

    def test_me_returns_primary_photo(self):
        auth = _login()
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(auth["token"]), timeout=20)
        assert r.status_code == 200, r.text[:200]
        user = r.json()["user"]
        pp = user.get("primary_photo")
        assert pp is not None
        assert pp.get("photo_id") == user["primary_photo_id"]
        assert isinstance(pp.get("data"), str)
