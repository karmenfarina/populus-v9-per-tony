"""Tests for the vote-change feature (max 2 changes) and nickname_side on comments."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://team-pick.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


def _signup():
    email = f"TEST_vc_{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "testpass123", "nickname": f"vc_{uuid.uuid4().hex[:6]}"
    }, timeout=15)
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    return r.json()["token"], r.json()["user"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _live_feud_id():
    r = requests.get(f"{API}/feuds", timeout=15)
    assert r.status_code == 200
    feuds = r.json().get("feuds", [])
    assert feuds, "No live feuds available for testing"
    return feuds[0]["feud_id"]


@pytest.fixture(scope="module")
def feud_id():
    return _live_feud_id()


@pytest.fixture()
def user_token():
    token, _ = _signup()
    return token


class TestVoteChanges:
    """Vote-change flow: first vote, same-side reject, switch, second switch, third reject."""

    def test_full_vote_change_flow(self, feud_id, user_token):
        # Snapshot original counters — /share is always revealed (no auth needed)
        pre = requests.get(f"{API}/share/{feud_id}", timeout=10).json()["feud"]
        pre_a = pre.get("votes_a") or 0
        pre_b = pre.get("votes_b") or 0

        # 1) First vote A
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(user_token),
                          json={"side": "A"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] is False
        f = body["feud"]
        assert f["my_vote"] == "A"
        assert f["my_vote_changes"] == 0
        assert f["my_vote_changes_left"] == 2
        assert f["votes_a"] == pre_a + 1

        # 2) Same side again -> 400
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(user_token),
                          json={"side": "A"}, timeout=10)
        assert r.status_code == 400
        assert "Hai già votato per questa parte" in r.json().get("detail", "")

        # counters unchanged
        f2 = requests.get(f"{API}/feuds/{feud_id}", headers=_headers(user_token), timeout=10).json()["feud"]
        assert f2["votes_a"] == pre_a + 1
        assert f2["my_vote_changes"] == 0

        # 3) Switch to B -> 200 changed True
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(user_token),
                          json={"side": "B"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] is True
        f = body["feud"]
        assert f["my_vote"] == "B"
        assert f["my_vote_changes"] == 1
        assert f["my_vote_changes_left"] == 1
        assert f["votes_a"] == pre_a  # decremented
        assert f["votes_b"] == pre_b + 1  # incremented

        # 4) Switch back to A -> 200 changed True, changes == 2
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(user_token),
                          json={"side": "A"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] is True
        f = body["feud"]
        assert f["my_vote"] == "A"
        assert f["my_vote_changes"] == 2
        assert f["my_vote_changes_left"] == 0

        # 5) Third change attempt -> 403
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(user_token),
                          json={"side": "B"}, timeout=10)
        assert r.status_code == 403
        assert "Hai raggiunto il limite di 2 cambi voto" in r.json().get("detail", "")

        # 6) GET /feuds/{id} carries my_vote_changes==2, left==0
        f3 = requests.get(f"{API}/feuds/{feud_id}", headers=_headers(user_token), timeout=10).json()["feud"]
        assert f3["my_vote_changes"] == 2
        assert f3["my_vote_changes_left"] == 0


class TestNicknameSideOnComments:
    """Comment stores side frozen at write, but nickname_side reflects current vote."""

    def test_comment_nickname_side_updates_on_vote_change(self, feud_id):
        token, user = _signup()
        # Vote A
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(token),
                          json={"side": "A"}, timeout=10)
        assert r.status_code == 200

        # Post comment (side derived from current vote = A)
        marker = f"TEST_nick_side_{uuid.uuid4().hex[:8]}"
        r = requests.post(f"{API}/feuds/{feud_id}/comments", headers=_headers(token),
                          json={"text": marker}, timeout=10)
        assert r.status_code == 200, r.text
        cmt = r.json()["comment"]
        assert cmt["side"] == "A"
        cmt_id = cmt["comment_id"]

        # Comments GET now — nickname_side should be A
        r = requests.get(f"{API}/feuds/{feud_id}/comments", timeout=10)
        assert r.status_code == 200
        payload = r.json()
        mine = next((c for c in payload["side_a"] if c["comment_id"] == cmt_id), None)
        assert mine is not None, "comment should be in side_a"
        assert mine.get("nickname_side") == "A"

        # Now switch vote to B (1st change, still under limit)
        r = requests.post(f"{API}/feuds/{feud_id}/vote", headers=_headers(token),
                          json={"side": "B"}, timeout=10)
        assert r.status_code == 200, r.text

        # Re-fetch comments: frozen side == A, nickname_side == B
        r = requests.get(f"{API}/feuds/{feud_id}/comments", timeout=10)
        assert r.status_code == 200
        payload = r.json()
        mine = next((c for c in payload["side_a"] if c["comment_id"] == cmt_id), None)
        assert mine is not None, "comment should STILL be in side_a (frozen)"
        assert mine["side"] == "A"
        assert mine.get("nickname_side") == "B", f"expected nickname_side=B, got {mine.get('nickname_side')}"
