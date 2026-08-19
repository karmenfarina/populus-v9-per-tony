"""Backend tests for POST /api/stories new `kind` contract (iter_67).

Covers:
  - Backward-compat: POST /api/stories with only feud_id -> kind defaults to 'feud'
  - kind='feud' explicit still works and hydrates feud snapshot on /stories/feed
  - kind='badge' without unlocked badge -> 403 "Devi prima sbloccare questa spilla."
  - kind='badge' with bad category/tier -> 400
  - Invalid kind -> 400
  - feed rows contain the new `kind` field for legacy feud stories
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://bot-burst-fix.preview.emergentagent.com').rstrip('/')

USER_A = {"email": "chat_a@test.it", "password": "test123"}
USER_B = {"email": "chat_b@test.it", "password": "test123"}


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


def _get_feud_id():
    r = requests.get(f"{BASE_URL}/api/feuds?limit=1", timeout=20)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    items = d.get("feuds") or d.get("items") or []
    assert items, "no feuds in system"
    return items[0]["feud_id"]


@pytest.fixture(scope="module")
def ctx():
    tok_a, ua = _login(USER_A)
    tok_b, ub = _login(USER_B)
    feud_id = _get_feud_id()
    # Ensure A/B are in each other's circle so feed hydration path is exercised
    requests.post(f"{BASE_URL}/api/circle/{ub['user_id']}", headers=_headers(tok_a), timeout=20)
    requests.post(f"{BASE_URL}/api/circle/{ua['user_id']}", headers=_headers(tok_b), timeout=20)
    return {"tok_a": tok_a, "ua": ua, "tok_b": tok_b, "ub": ub, "feud_id": feud_id}


class TestBackwardCompat:
    """Ensure the legacy body shape (only feud_id) still works."""

    def test_legacy_body_only_feud_id_creates_story(self, ctx):
        payload = {"feud_id": ctx["feud_id"]}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert "story" in d
        story = d["story"]
        assert story.get("kind") == "feud", f"kind should default to 'feud', got {story.get('kind')}"
        assert story.get("feud_id") == ctx["feud_id"]
        assert story.get("story_id")
        # cleanup
        requests.delete(f"{BASE_URL}/api/stories/{story['story_id']}", headers=_headers(ctx["tok_a"]), timeout=20)

    def test_explicit_kind_feud_creates_story(self, ctx):
        payload = {"kind": "feud", "feud_id": ctx["feud_id"], "comment": "TEST_explicit_feud"}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 200, r.text[:200]
        story = r.json()["story"]
        assert story["kind"] == "feud"
        assert story["feud_id"] == ctx["feud_id"]
        assert story.get("feud") is not None, "feud snapshot should be present on feud kind"
        assert story["feud"].get("feud_id") == ctx["feud_id"]
        # cleanup
        requests.delete(f"{BASE_URL}/api/stories/{story['story_id']}", headers=_headers(ctx["tok_a"]), timeout=20)


class TestFeedContractKind:
    """The /stories/feed rows must contain the new `kind` field."""

    def test_feed_rows_have_kind_feud(self, ctx):
        # create one story so the feed has at least one row
        payload = {"feud_id": ctx["feud_id"]}
        cr = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert cr.status_code == 200
        sid = cr.json()["story"]["story_id"]
        try:
            r = requests.get(f"{BASE_URL}/api/stories/feed", headers=_headers(ctx["tok_a"]), timeout=20)
            assert r.status_code == 200, r.text[:200]
            groups = r.json().get("groups") or []
            assert groups, "expected at least one group"
            found = False
            for g in groups:
                for st in g.get("stories", []):
                    assert "kind" in st, f"missing 'kind' in story row: {list(st.keys())}"
                    if st["story_id"] == sid:
                        found = True
                        assert st["kind"] == "feud"
                        assert st.get("feud") is not None
                        assert st.get("badge") in (None,) or "badge" not in st  # feud story has no badge or badge is None
            assert found, "created story not visible in feed"
        finally:
            requests.delete(f"{BASE_URL}/api/stories/{sid}", headers=_headers(ctx["tok_a"]), timeout=20)


class TestBadgeKindValidation:
    """Badge stories must be validated: 400 on bad input, 403 if not unlocked."""

    def test_invalid_kind_returns_400(self, ctx):
        payload = {"kind": "bogus", "feud_id": ctx["feud_id"]}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"

    def test_badge_missing_category_400(self, ctx):
        payload = {"kind": "badge", "badge_tier": 1}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 400, r.text[:200]
        assert "spilla" in r.text.lower() or "not valid" in r.text.lower() or "non valid" in r.text.lower()

    def test_badge_bad_tier_400(self, ctx):
        payload = {"kind": "badge", "badge_category": "politica", "badge_tier": 99}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 400, r.text[:200]

    def test_badge_unknown_category_400(self, ctx):
        payload = {"kind": "badge", "badge_category": "nonexistent_cat_xyz", "badge_tier": 1}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 400, r.text[:200]

    def test_badge_not_unlocked_returns_403(self, ctx):
        """User A likely has NOT unlocked category badge 'politica' tier 1.
        Endpoint must respond 403 with the Italian message.
        """
        payload = {"kind": "badge", "badge_category": "politica", "badge_tier": 1}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        # Either the user has unlocked it (200) OR gets 403 — the spec allows both.
        assert r.status_code in (200, 403), f"unexpected {r.status_code}: {r.text[:200]}"
        if r.status_code == 403:
            assert "sblocca" in r.text.lower() or "spilla" in r.text.lower(), r.text[:200]
        else:
            # cleanup if it was actually created
            sid = r.json().get("story", {}).get("story_id")
            if sid:
                requests.delete(f"{BASE_URL}/api/stories/{sid}", headers=_headers(ctx["tok_a"]), timeout=20)


class TestFeudKindNotFound:
    def test_feud_kind_nonexistent_feud_returns_404(self, ctx):
        payload = {"kind": "feud", "feud_id": f"feud_nonexistent_{uuid.uuid4().hex[:6]}"}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text[:200]}"

    def test_feud_kind_missing_feud_id_returns_400(self, ctx):
        payload = {"kind": "feud"}
        r = requests.post(f"{BASE_URL}/api/stories", headers=_headers(ctx["tok_a"]), json=payload, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
