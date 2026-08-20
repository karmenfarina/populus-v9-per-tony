"""Iter160 regression — Instagram-style unseen-only auto-advance behaviour
in /app/frontend/app/(tabs)/stories/viewer/[userId].tsx.

Covers:
  1. Backend contract (feed / stories-by-user / mark-viewed / anonymous auth)
     still returns 2xx / expected 4xx — no route regression from viewer refactor.
  2. Static verification of the NEW auto-advance semantics:
     - jumpToUser('next') iterates candidates with a while-loop and skips users
       whose stories are all viewed (findIndex((s)=>!s.viewed) < 0).
     - jumpToUser('prev') NEVER skips (startFromLast = true, no direction-guard).
     - Loop exhaustion → closeViewer().
     - Timer callback searches for next UNSEEN inside the current user via
       explicit `for (let i = currentIdx + 1; i < list.length; i++)` and
       triggers jumpToUserRef.current("next") + autoCloseFiredRef = true
       when no unseen remains.
  3. Regression: manual goPrev/goNext still call setIdx directly at boundaries
     other than the first/last (so mid-chain manual navigation bypasses the
     unseen-only filter).
  4. Regression: iter155-159 protections intact (wall-clock guard, scaleX,
     jumpToUserRef proxy, advanceLockRef, imageLoadedRef inline sync,
     single clearInterval, storyStartTsRef reset on both [idx] and
     [currentUserId]).
"""
import os
import re
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing"

VIEWER_PATH = "/app/frontend/app/(tabs)/stories/viewer/[userId].tsx"


# ── shared fixtures ───────────────────────────────────────────────────
@pytest.fixture(scope="module")
def anon_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/anonymous",
        json={"nickname": f"TEST_iter160_{os.getpid()}"},
        timeout=15,
    )
    assert r.status_code == 200, f"anon auth failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("token") or body.get("access_token") or body.get("session_token")
    assert tok, f"no token in response: {body}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(anon_token):
    return {"Authorization": f"Bearer {anon_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def src():
    with open(VIEWER_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ── Backend contract regression ───────────────────────────────────────
class TestStoriesEndpoints:
    def test_stories_feed_ok(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/stories/feed", headers=auth_headers, timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert isinstance(data, dict)
        assert "groups" in data
        assert isinstance(data["groups"], list)

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

    def test_stories_view_invalid_id_returns_4xx(self, auth_headers):
        r = requests.post(
            f"{BASE_URL}/api/stories/nonexistent-iter160/view",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code in (400, 404, 422), f"expected 4xx, got {r.status_code}"

    def test_anonymous_auth_repeatable(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": f"TEST_iter160smoke_{os.getpid()}"},
            timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert "token" in data or "access_token" in data or "session_token" in data


# ── Instagram-style unseen-only auto-advance (NEW behaviour) ──────────
class TestJumpToUserUnseenOnly:
    def test_jump_to_user_uses_while_loop_over_cursor(self, src):
        """jumpToUser must iterate with a `while (cursor >= 0 && cursor < order.length)`
        loop rather than a one-shot indexOf+step."""
        assert re.search(
            r"let\s+cursor\s*=\s*currentIdx\s*\+\s*step\s*;",
            src,
        ), "cursor initialisation `let cursor = currentIdx + step` missing"
        assert re.search(
            r"while\s*\(\s*cursor\s*>=\s*0\s*&&\s*cursor\s*<\s*order\.length\s*\)",
            src,
        ), "while(cursor>=0 && cursor<order.length) loop missing in jumpToUser"

    def test_jump_next_skips_all_viewed_users(self, src):
        """When direction==='next' and candidateStories has NO unseen entry,
        jumpToUser must `cursor += step; continue;` rather than land on that user."""
        assert re.search(
            r"const\s+firstUnseen\s*=\s*candidateStories\.findIndex\(\(s\)\s*=>\s*!s\.viewed\)",
            src,
        ), "firstUnseen findIndex((s)=>!s.viewed) missing"
        # Direction guard: only 'next' skips.
        assert re.search(
            r"if\s*\(\s*direction\s*===\s*[\"']next[\"']\s*&&\s*firstUnseen\s*<\s*0\s*\)\s*\{\s*cursor\s*\+=\s*step\s*;\s*continue\s*;\s*\}",
            src,
        ), "direction==='next' + firstUnseen<0 skip branch missing"

    def test_target_idx_startFromLast_vs_firstUnseen(self, src):
        """When landing on a candidate:
          - prev  (startFromLast=true) → targetIdxInUser = candidateStories.length - 1
          - next  (startFromLast=false) → targetIdxInUser = firstUnseen if any else 0
        """
        assert re.search(
            r"const\s+targetIdxInUser\s*=\s*startFromLast"
            r"\s*\?\s*candidateStories\.length\s*-\s*1"
            r"\s*:\s*\(\s*firstUnseen\s*>=\s*0\s*\?\s*firstUnseen\s*:\s*0\s*\)",
            src,
        ), "targetIdxInUser ternary (startFromLast ? last : firstUnseen|0) missing"

    def test_prev_direction_does_not_skip(self, src):
        """`startFromLast` must be derived from direction==='prev' so prev never
        goes through the unseen-only skip branch."""
        assert re.search(
            r"const\s+startFromLast\s*=\s*direction\s*===\s*[\"']prev[\"']",
            src,
        ), "startFromLast = direction==='prev' missing"

    def test_loop_exhaustion_closes_viewer(self, src):
        """After the while-loop exits without a return, closeViewer() must fire."""
        # Find the jumpToUser body and assert closeViewer() appears AFTER the
        # while block's closing brace (i.e. the "no candidate found" fallback).
        m = re.search(
            r"const\s+jumpToUser\s*=\s*useCallback\(async\s*\(direction[\s\S]*?\},\s*\[currentUserId,\s*closeViewer\]\)",
            src,
        )
        assert m, "jumpToUser useCallback body not found"
        body = m.group(0)
        # There must be a while(...) { ... } block AND a trailing closeViewer()
        # after it (the exhausted-order branch).
        assert re.search(r"while\s*\([\s\S]*?\)\s*\{[\s\S]*?\}\s*[\s\S]*?closeViewer\(\)", body), \
            "trailing closeViewer() after while-loop exhaustion missing"

    def test_atomic_swap_on_candidate(self, src):
        """Atomic swap must set internalNavRef, currentUserId, stories, idx=targetIdxInUser,
        progress=0 and clear autoCloseFiredRef+advanceLockRef."""
        for needle in [
            "internalNavRef.current = true",
            "setCurrentUserId(candidateUid)",
            "setStories(candidateStories)",
            "setIdx(targetIdxInUser)",
            "setProgress(0)",
            "autoCloseFiredRef.current = false",
            "advanceLockRef.current = false",
        ]:
            assert needle in src, f"atomic-swap statement missing: {needle}"


# ── Timer callback: next-unseen search inside same user ────────────────
class TestTimerNextUnseenInSameUser:
    def test_for_loop_searches_next_unseen(self, src):
        """Timer must scan from currentIdx+1 upwards for the next story where
        `!list[i].viewed`, then setIdx(nextUnseenIdx) if found."""
        assert re.search(
            r"let\s+nextUnseenIdx\s*=\s*-1\s*;",
            src,
        ), "nextUnseenIdx = -1 initialisation missing"
        assert re.search(
            r"for\s*\(\s*let\s+i\s*=\s*currentIdx\s*\+\s*1\s*;\s*i\s*<\s*list\.length\s*;\s*i\+\+\s*\)\s*\{\s*"
            r"if\s*\(\s*!list\[i\]\.viewed\s*\)\s*\{\s*nextUnseenIdx\s*=\s*i\s*;\s*break\s*;\s*\}\s*\}",
            src,
        ), "for-loop searching next unseen in same user missing"

    def test_no_unseen_triggers_jump_to_next_user(self, src):
        """If nextUnseenIdx < 0 the timer must set autoCloseFiredRef=true and
        call jumpToUserRef.current?.('next') — NOT closeViewer() directly."""
        # The whole branch, order-insensitive on the two statements.
        m = re.search(
            r"if\s*\(\s*nextUnseenIdx\s*<\s*0\s*\)\s*\{([\s\S]*?)\}",
            src,
        )
        assert m, "if(nextUnseenIdx<0) branch missing"
        branch = m.group(1)
        assert "autoCloseFiredRef.current = true" in branch, \
            "autoCloseFiredRef=true not set in nextUnseenIdx<0 branch"
        assert re.search(r"jumpToUserRef\.current\?\.\(\s*[\"']next[\"']\s*\)", branch), \
            "jumpToUserRef.current?.('next') call missing in nextUnseenIdx<0 branch"

    def test_advance_lock_and_setIdx_on_next_unseen(self, src):
        """When nextUnseenIdx >= 0 the timer must lock the anti-double-advance
        guard AND call setIdx(nextUnseenIdx)."""
        # Look inside setProgress callback for the sequence.
        assert re.search(
            r"advanceLockRef\.current\s*=\s*true\s*;\s*setIdx\(\s*nextUnseenIdx\s*\)",
            src,
        ), "advanceLockRef=true; setIdx(nextUnseenIdx) sequence missing"


# ── Regression: manual goPrev / goNext bypass unseen filter mid-chain ─
class TestManualNavigationBypassesUnseenFilter:
    def test_go_next_mid_chain_uses_setIdx(self, src):
        """goNext must call setIdx(idx + 1) directly when not at boundary — it
        must NOT search for the next unseen (that logic belongs to auto-advance)."""
        m = re.search(
            r"const\s+goNext\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};",
            src,
        )
        assert m, "goNext arrow function not found"
        body = m.group(1)
        # Boundary → jumpToUser('next'); otherwise setIdx(idx+1)
        assert "jumpToUser(\"next\")" in body, "boundary → jumpToUser('next') missing"
        assert re.search(r"setIdx\(\s*idx\s*\+\s*1\s*\)", body), \
            "goNext must call setIdx(idx+1) for mid-chain navigation"
        # Must NOT do a viewed-check search inline (that would tie manual nav
        # to unseen-only behaviour).
        assert not re.search(r"!.*\.viewed", body), \
            f"goNext should not filter by viewed state; body was: {body!r}"

    def test_go_prev_mid_chain_uses_setIdx(self, src):
        m = re.search(
            r"const\s+goPrev\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};",
            src,
        )
        assert m, "goPrev arrow function not found"
        body = m.group(1)
        assert "jumpToUser(\"prev\")" in body, "boundary → jumpToUser('prev') missing"
        assert re.search(r"setIdx\(\s*idx\s*-\s*1\s*\)", body), \
            "goPrev must call setIdx(idx-1) for mid-chain navigation"
        assert not re.search(r"!.*\.viewed", body), \
            f"goPrev should not filter by viewed state; body was: {body!r}"


# ── Regression: iter155-159 protections intact ─────────────────────────
class TestIter155to159RegressionIntact:
    def test_wall_clock_guard_still_present(self, src):
        assert re.search(
            r"const\s+elapsed\s*=\s*Date\.now\(\)\s*-\s*storyStartTsRef\.current",
            src,
        ), "wall-clock elapsed comparison missing"
        assert re.search(
            r"if\s*\(\s*elapsed\s*<\s*STORY_DURATION_MS\s*-\s*TICK_MS\s*\)\s*\{[^}]*return\s+1\s*;",
            src,
            re.DOTALL,
        ), "wall-clock cap branch missing"

    def test_story_start_ts_reset_on_idx_and_currentUserId(self, src):
        assert re.search(
            r"useEffect\(\(\)\s*=>\s*\{[^}]*storyStartTsRef\.current\s*=\s*Date\.now\(\)[^}]*\}\s*,\s*\[idx\]\)",
            src,
            re.DOTALL,
        ), "storyStartTsRef reset in [idx] effect missing"
        assert re.search(
            r"useEffect\(\(\)\s*=>\s*\{[^}]*storyStartTsRef\.current\s*=\s*Date\.now\(\)[^}]*\}\s*,\s*\[currentUserId\]\)",
            src,
            re.DOTALL,
        ), "storyStartTsRef reset in [currentUserId] effect missing"

    def test_other_protections(self, src):
        assert "advanceLockRef" in src
        assert re.search(r"transform:\s*\[\s*\{\s*scaleX:\s*pct\s*\}\s*\]", src)
        assert re.search(r"key=\{`\$\{currentUserId\}-\$\{i\}`\}", src)
        assert "jumpToUserRef" in src
        assert re.search(r"imageLoadedRef\.current\s*=\s*ready", src)
        # exactly ONE real clearInterval call (in useFocusEffect cleanup)
        real = [ln for ln in src.split("\n")
                if "clearInterval(" in ln and not ln.strip().startswith("//")]
        assert len(real) == 1, f"expected 1 clearInterval, got {len(real)}: {real}"
