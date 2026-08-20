"""Iter159 regression: backend endpoints touched by stories viewer + static verification
of the wall-clock guard fix in the stories viewer client file.

Backend contract (feed/user/view/anonymous auth) should remain intact.
Frontend fix is verified by grepping the viewer file for the wall-clock guard
invariants declared in the review request.
"""
import os
import re
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing"

VIEWER_PATH = "/app/frontend/app/(tabs)/stories/viewer/[userId].tsx"


@pytest.fixture(scope="module")
def anon_token():
    r = requests.post(f"{BASE_URL}/api/auth/anonymous",
                      json={"nickname": f"TEST_iter159_{os.getpid()}"}, timeout=15)
    assert r.status_code == 200, f"anonymous auth failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("token") or body.get("access_token") or body.get("session_token")
    assert tok, f"no token in response: {body}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(anon_token):
    return {"Authorization": f"Bearer {anon_token}", "Content-Type": "application/json"}


# ── Backend contract regression ──────────────────────────────────────
class TestStoriesEndpoints:
    def test_stories_feed_ok(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=auth_headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert isinstance(data, dict)
        assert "groups" in data

    def test_stories_by_user_current_user(self, auth_headers):
        me_r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=15)
        assert me_r.status_code == 200
        body = me_r.json()
        user = body.get("user", body)
        uid = user.get("user_id") or user.get("id")
        assert uid, f"no user_id: {body}"
        r = requests.get(f"{BASE_URL}/api/stories/user/{uid}", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "stories" in data
        assert isinstance(data["stories"], list)

    def test_stories_view_invalid_id_4xx(self, auth_headers):
        r = requests.post(f"{BASE_URL}/api/stories/nonexistent-iter159/view",
                          headers=auth_headers, timeout=15)
        assert r.status_code in (400, 404, 422), f"expected 4xx, got {r.status_code}"


class TestAuthSmoke:
    def test_anonymous_signup(self):
        r = requests.post(f"{BASE_URL}/api/auth/anonymous",
                          json={"nickname": f"TEST_iter159smoke_{os.getpid()}"}, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "token" in data or "access_token" in data or "session_token" in data


# ── Static verification of wall-clock guard fix in viewer ─────────────
class TestViewerWallClockFix:
    @pytest.fixture(scope="class")
    def src(self):
        with open(VIEWER_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_story_start_ts_ref_declared(self, src):
        """storyStartTsRef must be declared as a useRef initialised to Date.now()."""
        assert re.search(r"storyStartTsRef\s*=\s*useRef<number>\(Date\.now\(\)\)", src), \
            "storyStartTsRef useRef declaration missing"

    def test_wall_clock_guard_in_timer(self, src):
        """The timer callback must compare elapsed against STORY_DURATION_MS − TICK_MS
        and cap progress at 1 without advancing when the wall-clock hasn't elapsed."""
        # elapsed = Date.now() - storyStartTsRef.current
        assert re.search(
            r"const\s+elapsed\s*=\s*Date\.now\(\)\s*-\s*storyStartTsRef\.current",
            src,
        ), "elapsed computation missing"
        # elapsed < STORY_DURATION_MS − TICK_MS branch, returning 1 (visual cap only)
        assert re.search(
            r"if\s*\(\s*elapsed\s*<\s*STORY_DURATION_MS\s*-\s*TICK_MS\s*\)\s*\{[^}]*return\s+1\s*;",
            src,
            re.DOTALL,
        ), "wall-clock cap branch (return 1 without advance) missing"

    def test_reset_on_idx_change(self, src):
        """useEffect on [idx] must reset storyStartTsRef.current = Date.now()."""
        m = re.search(
            r"useEffect\(\(\)\s*=>\s*\{([^}]*storyStartTsRef\.current\s*=\s*Date\.now\(\)[^}]*)\}\s*,\s*\[idx\]\)",
            src,
            re.DOTALL,
        )
        assert m, "storyStartTsRef reset in useEffect [idx] missing"

    def test_reset_on_current_user_change(self, src):
        """A separate useEffect on [currentUserId] must also update storyStartTsRef.
        This covers the jumpToUser case where target idx equals current idx (React
        skips the [idx] effect)."""
        m = re.search(
            r"useEffect\(\(\)\s*=>\s*\{[^}]*storyStartTsRef\.current\s*=\s*Date\.now\(\)[^}]*\}\s*,\s*\[currentUserId\]\)",
            src,
            re.DOTALL,
        )
        assert m, "storyStartTsRef reset in useEffect [currentUserId] missing"

    def test_iter155_158_protections_intact(self, src):
        """Regression checks for previously-added protections."""
        # advanceLockRef
        assert "advanceLockRef" in src, "advanceLockRef missing"
        # transform: [{ scaleX: pct }] on progress fill
        assert re.search(r"transform:\s*\[\s*\{\s*scaleX:\s*pct\s*\}\s*\]", src), \
            "transform: scaleX(pct) missing"
        # key univoca `${currentUserId}-${i}` in progress strip
        assert re.search(r"key=\{`\$\{currentUserId\}-\$\{i\}`\}", src), \
            "unique key with currentUserId missing"
        # jumpToUserRef proxy
        assert "jumpToUserRef" in src, "jumpToUserRef proxy missing"
        # imageLoadedRef inline sync in onLoad / effect
        assert re.search(r"imageLoadedRef\.current\s*=\s*ready", src), \
            "imageLoadedRef inline sync in effect missing"
        # only ONE clearInterval call in the file (in the useFocusEffect cleanup)
        clear_interval_calls = [ln for ln in src.split("\n") if "clearInterval(" in ln]
        # Filter out lines that are pure comments (start with '//')
        real_calls = [ln for ln in clear_interval_calls if not ln.strip().startswith("//")]
        assert len(real_calls) == 1, \
            f"expected exactly 1 clearInterval() (cleanup only); got {len(real_calls)}: {real_calls}"
