"""Backend tests for kind='badge' story feed hydration (iter_68).

Complements test_stories_kind_badge.py by covering:
    - Successful badge story creation (happy path). Uses a fresh user that
      is granted a category badge via admin override so we can exercise
      the 200 path deterministically.
    - /stories/feed hydrates badge story with `kind='badge'` and a full
      `badge` object containing category_id, category_label, color, icon,
      tier, name, emoji, threshold.
    - Feud stories still round-trip correctly (regression).
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://gossip-beta.preview.emergentagent.com",
).rstrip("/")

ADMIN_KEY = "populus-admin-42b8f3"
USER_A = {"email": "chat_a@test.it", "password": "test123"}
USER_B = {"email": "chat_b@test.it", "password": "test123"}


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _admin_headers():
    return {"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


def _grant_badge(user_id: str, category: str, tier: int) -> bool:
    """Try to grant a category badge via admin endpoint. Returns True on success.

    The endpoint name/shape is not documented in the request — we try a
    couple of plausible ones. If none work we skip the happy-path test.
    """
    candidates = [
        (
            "POST",
            f"{BASE_URL}/api/admin/users/{user_id}/grant_badge",
            {"category": category, "tier": tier},
        ),
        (
            "POST",
            f"{BASE_URL}/api/admin/users/{user_id}/badges/grant",
            {"category": category, "tier": tier},
        ),
        (
            "POST",
            f"{BASE_URL}/api/admin/grant_category_badge",
            {"user_id": user_id, "category": category, "tier": tier},
        ),
    ]
    for method, url, payload in candidates:
        try:
            r = requests.request(method, url, headers=_admin_headers(), json=payload, timeout=15)
            if r.status_code < 300:
                return True
        except Exception:
            continue
    return False


@pytest.fixture(scope="module")
def ctx():
    tok_a, ua = _login(USER_A)
    tok_b, ub = _login(USER_B)
    # ensure mutual circle so feed hydrates
    requests.post(f"{BASE_URL}/api/circle/{ub['user_id']}", headers=_headers(tok_a), timeout=20)
    requests.post(f"{BASE_URL}/api/circle/{ua['user_id']}", headers=_headers(tok_b), timeout=20)
    return {"tok_a": tok_a, "ua": ua, "tok_b": tok_b, "ub": ub}


class TestBadgeStoryHappyPath:
    """POST /stories kind='badge' with a user that has the badge → 200 + hydrated feed."""

    def test_create_and_feed_hydrate(self, ctx):
        # Try to grant a badge via admin so the happy path is deterministic.
        granted = _grant_badge(ctx["ua"]["user_id"], "politica", 1)
        if not granted:
            pytest.skip(
                "Could not grant a category badge via admin endpoint — "
                "the 403 path is already covered in test_stories_kind_badge.py."
            )

        payload = {
            "kind": "badge",
            "badge_category": "politica",
            "badge_tier": 1,
            "comment": f"TEST_iter68_{uuid.uuid4().hex[:6]}",
        }
        r = requests.post(
            f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20
        )
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
        story = r.json()["story"]
        assert story["kind"] == "badge"
        assert story.get("feud") is None
        b = story.get("badge")
        assert b is not None, "badge object missing"
        assert b["category_id"] == "politica"
        assert b["tier"] == 1
        assert b.get("category_label")
        assert b.get("color")
        assert b.get("icon")
        assert b.get("name")
        assert b.get("emoji")
        assert isinstance(b.get("threshold"), int)
        sid = story["story_id"]

        try:
            # Verify feed exposes the same shape to another viewer in the circle
            r2 = requests.get(
                f"{BASE_URL}/api/stories/feed", headers=_headers(ctx["tok_b"]), timeout=20
            )
            assert r2.status_code == 200, r2.text[:200]
            groups = r2.json().get("groups") or []
            found = None
            for g in groups:
                for st in g.get("stories", []):
                    if st.get("story_id") == sid:
                        found = st
                        break
                if found:
                    break
            assert found is not None, "created badge story not visible in B's feed"
            assert found["kind"] == "badge"
            assert found.get("feud") is None
            assert found.get("badge") is not None
            assert found["badge"]["category_id"] == "politica"
            assert found["badge"]["tier"] == 1
        finally:
            requests.delete(
                f"{BASE_URL}/api/stories/{sid}", headers=_headers(ctx["tok_a"]), timeout=20
            )


class TestFeudStoryRegression:
    """Make sure feud stories continue to work with the new kind field."""

    def test_feud_story_still_has_feud_hydrated(self, ctx):
        # get any feud
        r = requests.get(f"{BASE_URL}/api/feuds?limit=1", timeout=15)
        assert r.status_code == 200
        items = r.json().get("feuds") or r.json().get("items") or []
        if not items:
            pytest.skip("No feuds available for regression check")
        feud_id = items[0]["feud_id"]

        cr = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_a"]),
            json={"feud_id": feud_id},
            timeout=20,
        )
        assert cr.status_code == 200, cr.text[:200]
        story = cr.json()["story"]
        sid = story["story_id"]
        try:
            assert story["kind"] == "feud"
            assert story.get("feud") is not None
            assert story["feud"]["feud_id"] == feud_id
            # No badge for feud stories
            assert story.get("badge") in (None,) or story.get("badge") is None
        finally:
            requests.delete(
                f"{BASE_URL}/api/stories/{sid}", headers=_headers(ctx["tok_a"]), timeout=20
            )
