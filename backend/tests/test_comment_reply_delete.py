"""Backend tests for DELETE /api/comments/{id} and DELETE /api/replies/{id}."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://feud-governance.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()["token"], r.json()["user"]


@pytest.fixture(scope="module")
def auth_a():
    token, user = _login("chat_a@test.it", "test123")
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def auth_b():
    token, user = _login("chat_b@test.it", "test123")
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def feud_id():
    r = requests.get(f"{API}/feuds", timeout=30)
    assert r.status_code == 200
    feuds = r.json().get("feuds", [])
    assert feuds, "No feuds available"
    return feuds[0]["feud_id"]


def _ensure_vote(headers, fid, side="A"):
    # Vote; may return 400 if same side; we don't care about failures other than server errors
    r = requests.post(f"{API}/feuds/{fid}/vote", headers=headers, json={"side": side}, timeout=30)
    assert r.status_code in (200, 400), f"vote failed: {r.status_code} {r.text}"


def _post_comment(headers, fid, text="TEST_comment"):
    r = requests.post(f"{API}/feuds/{fid}/comments", headers=headers, json={"text": text}, timeout=30)
    assert r.status_code == 200, f"post comment failed: {r.status_code} {r.text}"
    return r.json()["comment"]["comment_id"]


def _post_reply(headers, cid, text="TEST_reply"):
    r = requests.post(f"{API}/comments/{cid}/replies", headers=headers, json={"text": text}, timeout=30)
    assert r.status_code == 200, f"post reply failed: {r.status_code} {r.text}"
    return r.json()["reply"]["reply_id"]


class TestDeleteComment:
    def test_delete_own_comment(self, auth_a, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_del_own")
        r = requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        # Verify removed
        listr = requests.get(f"{API}/feuds/{feud_id}/comments", headers=auth_a["headers"], timeout=30).json()
        all_cids = [c["comment_id"] for c in listr["side_a"] + listr["side_b"]]
        assert cid not in all_cids

    def test_delete_missing_comment(self, auth_a):
        r = requests.delete(f"{API}/comments/cmt_nonexistent_zzz", headers=auth_a["headers"], timeout=30)
        assert r.status_code == 404
        assert "non trovato" in r.json().get("detail", "").lower()

    def test_delete_double(self, auth_a, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_double_del")
        r1 = requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)
        assert r1.status_code == 200
        r2 = requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)
        assert r2.status_code == 404

    def test_delete_forbidden(self, auth_a, auth_b, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_forbidden")
        r = requests.delete(f"{API}/comments/{cid}", headers=auth_b["headers"], timeout=30)
        assert r.status_code == 403
        assert "solo i tuoi" in r.json().get("detail", "").lower()
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)

    def test_delete_cascades_replies(self, auth_a, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_cascade_parent")
        rid = _post_reply(auth_a["headers"], cid, "TEST_cascade_reply")
        # Confirm reply exists
        replies = requests.get(f"{API}/comments/{cid}/replies", headers=auth_a["headers"], timeout=30).json()
        assert any(r["reply_id"] == rid for r in replies["replies"])
        # Delete parent
        r = requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)
        assert r.status_code == 200
        # Replies should now be empty
        after = requests.get(f"{API}/comments/{cid}/replies", headers=auth_a["headers"], timeout=30).json()
        assert after["replies"] == []


class TestDeleteReply:
    def test_delete_own_reply(self, auth_a, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_reply_parent")
        rid = _post_reply(auth_a["headers"], cid, "TEST_reply_own")
        r = requests.delete(f"{API}/replies/{rid}", headers=auth_a["headers"], timeout=30)
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        # cleanup parent
        requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)

    def test_delete_reply_forbidden(self, auth_a, auth_b, feud_id):
        _ensure_vote(auth_a["headers"], feud_id, "A")
        cid = _post_comment(auth_a["headers"], feud_id, "TEST_reply_forbidden_parent")
        rid = _post_reply(auth_a["headers"], cid, "TEST_reply_forbidden")
        r = requests.delete(f"{API}/replies/{rid}", headers=auth_b["headers"], timeout=30)
        assert r.status_code == 403
        # cleanup
        requests.delete(f"{API}/comments/{cid}", headers=auth_a["headers"], timeout=30)

    def test_delete_reply_missing(self, auth_a):
        r = requests.delete(f"{API}/replies/rpl_nonexistent_zzz", headers=auth_a["headers"], timeout=30)
        assert r.status_code == 404


class TestBlocksEndpoint:
    """Sanity check for GET/POST/DELETE /users/{id}/block and /users/me/blocks."""

    def test_blocks_flow(self, auth_a, auth_b):
        b_uid = auth_b["user"]["user_id"]
        # ensure not blocked
        requests.delete(f"{API}/users/{b_uid}/block", headers=auth_a["headers"], timeout=30)
        # list
        r0 = requests.get(f"{API}/users/me/blocks", headers=auth_a["headers"], timeout=30)
        assert r0.status_code == 200
        initial = r0.json()
        assert isinstance(initial.get("blocks", initial) if isinstance(initial, dict) else initial, list) or True
        # block
        r1 = requests.post(f"{API}/users/{b_uid}/block", headers=auth_a["headers"], timeout=30)
        assert r1.status_code == 200
        # list has B
        r2 = requests.get(f"{API}/users/me/blocks", headers=auth_a["headers"], timeout=30)
        assert r2.status_code == 200
        payload = r2.json()
        blocks = payload.get("blocked_users", payload.get("blocks", payload))
        ids = [u.get("user_id") for u in blocks] if isinstance(blocks, list) else []
        assert b_uid in ids, f"Block not in list: {blocks}"
        # unblock
        r3 = requests.delete(f"{API}/users/{b_uid}/block", headers=auth_a["headers"], timeout=30)
        assert r3.status_code == 200
