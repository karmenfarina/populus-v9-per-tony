"""Backend tests for Stories feature (iteration 60).

Covers:
  - POST /api/stories (create, anonymous 403, quota check, moderation)
  - GET /api/stories/feed (grouping, ordering: mine → unseen → seen)
  - GET /api/stories/user/{author_id}
  - POST /api/stories/{story_id}/view (idempotent)
  - DELETE /api/stories/{story_id} (owner-only, 403 otherwise)
  - GET /api/stories/hidden_viewers
  - PUT /api/stories/hidden_viewers/{viewer_id}
  - POST /api/stories/{story_id}/reply (DM delivered)
  - Circle-based visibility (visitor not in circle → 403)
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', '').rstrip('/')
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set"

USER_A = {"email": "chat_a@test.it", "password": "test123"}
USER_B = {"email": "chat_b@test.it", "password": "test123"}


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


def _signup(nickname=None):
    """Create an outsider (anon) user for 'not in circle' tests.
    Regular signup requires email verification (no immediate token), so we
    use anonymous accounts to represent an outsider viewer. Anonymous users
    have an empty circle → they must not see A/B stories.
    """
    tok, user = _anon()
    return tok, user, {"nickname": user.get("nickname")}


def _anon():
    body = {"nickname": f"TEST_anon_{uuid.uuid4().hex[:6]}"}
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json=body, timeout=20)
    assert r.status_code == 200, f"anon signup failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


def _get_feud_id():
    r = requests.get(f"{BASE_URL}/api/feuds?limit=1", timeout=20)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    items = d.get("items") or d.get("feuds") or []
    assert items, "no feuds in system"
    return items[0]["feud_id"]


@pytest.fixture(scope="module")
def ctx():
    """Return shared context: tokens + a feud id + circle setup."""
    tok_a, ua = _login(USER_A)
    tok_b, ub = _login(USER_B)
    feud_id = _get_feud_id()
    # Make A and B mutual circle members (A follows B and B follows A).
    requests.post(f"{BASE_URL}/api/circle/{ub['user_id']}", headers=_headers(tok_a), timeout=20)
    requests.post(f"{BASE_URL}/api/circle/{ua['user_id']}", headers=_headers(tok_b), timeout=20)
    return {
        "tok_a": tok_a, "ua": ua,
        "tok_b": tok_b, "ub": ub,
        "feud_id": feud_id,
    }


# ---------- Story creation ----------

class TestStoryCreation:
    def test_create_story_success(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_a"]),
            json={"feud_id": ctx["feud_id"], "comment": "TEST_stories_comment_ok"},
            timeout=20,
        )
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text[:300]}"
        story = r.json()["story"]
        assert story["story_id"].startswith("story_")
        assert story["user_id"] == ctx["ua"]["user_id"]
        assert story["feud"] is not None
        assert story["feud"]["feud_id"] == ctx["feud_id"]
        assert story["comment"] == "TEST_stories_comment_ok"
        # Save for later
        ctx["story_a1"] = story["story_id"]

    def test_create_story_no_comment(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_b"]),
            json={"feud_id": ctx["feud_id"]},
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        ctx["story_b1"] = r.json()["story"]["story_id"]

    def test_anonymous_cannot_create_story(self, ctx):
        anon_tok, _ = _anon()
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(anon_tok),
            json={"feud_id": ctx["feud_id"]},
            timeout=20,
        )
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text[:200]}"

    def test_missing_feud_404(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_a"]),
            json={"feud_id": "feud_doesnotexist_x"},
            timeout=20,
        )
        assert r.status_code == 404, r.text[:200]

    def test_comment_too_long_422(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_a"]),
            json={"feud_id": ctx["feud_id"], "comment": "x" * 250},
            timeout=20,
        )
        # Pydantic max_length -> 422
        assert r.status_code in (400, 422), f"expected 4xx got {r.status_code} {r.text[:200]}"

    def test_unauthenticated_401(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers={"Content-Type": "application/json"},
            json={"feud_id": ctx["feud_id"]},
            timeout=20,
        )
        assert r.status_code in (401, 403), r.status_code


# ---------- Feed & visibility ----------

class TestStoryFeed:
    def test_feed_shape(self, ctx):
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=_headers(ctx["tok_a"]), timeout=20)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert "groups" in d and isinstance(d["groups"], list)
        assert d.get("ttl_hours") == 24
        assert d.get("quota") == 20
        # Mine first
        if d["groups"]:
            assert d["groups"][0]["is_mine"] is True, f"expected mine first: {d['groups'][0]}"

    def test_feed_shows_circle_member_stories(self, ctx):
        """User B's story should appear in User A's feed since B is in A's circle."""
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=_headers(ctx["tok_a"]), timeout=20)
        assert r.status_code == 200
        groups = r.json()["groups"]
        author_ids = {g["user_id"] for g in groups}
        assert ctx["ub"]["user_id"] in author_ids, (
            f"User B not visible in User A's feed. Groups: {[g['user_id'] for g in groups]}"
        )

    def test_feed_excludes_outside_circle(self, ctx):
        """A brand-new user C (not in A/B circles) should NOT see A/B stories."""
        tok_c, uc, _ = _signup()
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=_headers(tok_c), timeout=20)
        assert r.status_code == 200
        groups = r.json()["groups"]
        aids = {g["user_id"] for g in groups}
        assert ctx["ua"]["user_id"] not in aids
        assert ctx["ub"]["user_id"] not in aids

    def test_stories_by_user_owner(self, ctx):
        r = requests.get(
            f"{BASE_URL}/api/stories/user/{ctx['ua']['user_id']}",
            headers=_headers(ctx["tok_a"]),
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        assert r.json()["user_id"] == ctx["ua"]["user_id"]
        assert len(r.json()["stories"]) >= 1

    def test_stories_by_user_circle_member(self, ctx):
        """B is in A's circle → A can list B's stories."""
        r = requests.get(
            f"{BASE_URL}/api/stories/user/{ctx['ub']['user_id']}",
            headers=_headers(ctx["tok_a"]),
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        assert len(r.json()["stories"]) >= 1, "B's story should be visible to A"

    def test_stories_by_user_outside_circle_returns_empty(self, ctx):
        tok_c, _, _ = _signup()
        r = requests.get(
            f"{BASE_URL}/api/stories/user/{ctx['ua']['user_id']}",
            headers=_headers(tok_c),
            timeout=20,
        )
        assert r.status_code == 200
        assert r.json()["stories"] == []


# ---------- View, delete ----------

class TestViewAndDelete:
    def test_mark_viewed_idempotent(self, ctx):
        story_id = ctx["story_a1"]
        # B (in circle) marks A's story as viewed
        r1 = requests.post(f"{BASE_URL}/api/stories/{story_id}/view",
                           headers=_headers(ctx["tok_b"]), timeout=20)
        assert r1.status_code == 200, r1.text[:200]
        r2 = requests.post(f"{BASE_URL}/api/stories/{story_id}/view",
                           headers=_headers(ctx["tok_b"]), timeout=20)
        assert r2.status_code == 200
        # Verify viewed flag now true in feed for A (self-view != external)
        # For A, viewed=True since story is theirs — check via /stories/user
        r3 = requests.get(f"{BASE_URL}/api/stories/user/{ctx['ua']['user_id']}",
                          headers=_headers(ctx["tok_b"]), timeout=20)
        stories = r3.json()["stories"]
        target = next((s for s in stories if s["story_id"] == story_id), None)
        assert target is not None, "story disappeared"
        assert target["viewed"] is True

    def test_view_outside_circle_403(self, ctx):
        tok_c, _, _ = _signup()
        r = requests.post(
            f"{BASE_URL}/api/stories/{ctx['story_a1']}/view",
            headers=_headers(tok_c),
            timeout=20,
        )
        assert r.status_code == 403, f"expected 403 got {r.status_code}"

    def test_delete_non_owner_403(self, ctx):
        r = requests.delete(
            f"{BASE_URL}/api/stories/{ctx['story_a1']}",
            headers=_headers(ctx["tok_b"]),
            timeout=20,
        )
        assert r.status_code == 403, r.text[:200]

    def test_delete_owner_success(self, ctx):
        # Create a fresh story to delete
        r = requests.post(
            f"{BASE_URL}/api/stories",
            headers=_headers(ctx["tok_a"]),
            json={"feud_id": ctx["feud_id"], "comment": "TEST_to_delete"},
            timeout=20,
        )
        assert r.status_code == 200
        sid = r.json()["story"]["story_id"]
        # Delete
        r2 = requests.delete(f"{BASE_URL}/api/stories/{sid}", headers=_headers(ctx["tok_a"]), timeout=20)
        assert r2.status_code == 200
        # View after delete → 404
        r3 = requests.post(f"{BASE_URL}/api/stories/{sid}/view", headers=_headers(ctx["tok_a"]), timeout=20)
        assert r3.status_code == 404

    def test_delete_nonexistent_404(self, ctx):
        r = requests.delete(
            f"{BASE_URL}/api/stories/story_does_not_exist_x",
            headers=_headers(ctx["tok_a"]),
            timeout=20,
        )
        assert r.status_code == 404


# ---------- Hidden viewers ----------

class TestHiddenViewers:
    def test_get_hidden_viewers_lists_followers(self, ctx):
        r = requests.get(f"{BASE_URL}/api/stories/hidden_viewers",
                         headers=_headers(ctx["tok_a"]), timeout=20)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert "viewers" in d
        viewer_ids = {v["user_id"] for v in d["viewers"]}
        assert ctx["ub"]["user_id"] in viewer_ids, (
            f"B follows A → should appear as a viewer. Got: {viewer_ids}"
        )

    def test_toggle_hidden_viewer(self, ctx):
        r = requests.put(
            f"{BASE_URL}/api/stories/hidden_viewers/{ctx['ub']['user_id']}",
            headers=_headers(ctx["tok_a"]),
            json={"hidden": True},
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        assert r.json()["hidden"] is True
        # Verify B can no longer see A's stories
        r2 = requests.get(
            f"{BASE_URL}/api/stories/user/{ctx['ua']['user_id']}",
            headers=_headers(ctx["tok_b"]),
            timeout=20,
        )
        assert r2.status_code == 200
        assert r2.json()["stories"] == [], "B is hidden but still sees A's stories!"
        # Un-hide
        r3 = requests.put(
            f"{BASE_URL}/api/stories/hidden_viewers/{ctx['ub']['user_id']}",
            headers=_headers(ctx["tok_a"]),
            json={"hidden": False},
            timeout=20,
        )
        assert r3.status_code == 200
        assert r3.json()["hidden"] is False

    def test_anon_hidden_viewers_empty(self, ctx):
        anon_tok, _ = _anon()
        r = requests.get(f"{BASE_URL}/api/stories/hidden_viewers",
                         headers=_headers(anon_tok), timeout=20)
        assert r.status_code == 200
        assert r.json() == {"viewers": [], "hidden_count": 0}


# ---------- Reply ----------

class TestStoryReply:
    def test_reply_creates_dm(self, ctx):
        # B replies to A's story
        r = requests.post(
            f"{BASE_URL}/api/stories/{ctx['story_a1']}/reply",
            headers=_headers(ctx["tok_b"]),
            json={"text": "TEST_reply hello!"},
            timeout=20,
        )
        assert r.status_code == 200, f"reply failed: {r.status_code} {r.text[:300]}"
        d = r.json()
        assert d["ok"] is True
        msg = d.get("message") or {}
        assert msg.get("sender_id") == ctx["ub"]["user_id"]
        assert msg.get("recipient_id") == ctx["ua"]["user_id"]
        assert "Risposta alla storia" in (msg.get("text") or "")

    def test_reply_to_own_story_400(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories/{ctx['story_a1']}/reply",
            headers=_headers(ctx["tok_a"]),
            json={"text": "TEST self reply"},
            timeout=20,
        )
        assert r.status_code == 400, r.text[:200]

    def test_reply_outside_circle_403(self, ctx):
        tok_c, _, _ = _signup()
        r = requests.post(
            f"{BASE_URL}/api/stories/{ctx['story_a1']}/reply",
            headers=_headers(tok_c),
            json={"text": "TEST outsider"},
            timeout=20,
        )
        assert r.status_code == 403, r.text[:200]

    def test_reply_not_found_404(self, ctx):
        r = requests.post(
            f"{BASE_URL}/api/stories/story_nope_x/reply",
            headers=_headers(ctx["tok_b"]),
            json={"text": "TEST nope"},
            timeout=20,
        )
        assert r.status_code == 404
