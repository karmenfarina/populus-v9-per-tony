"""Iteration 120 backend tests — TOTAL interaction erasure with blocked users.

Stricter than iter118 which SCRUBBED own comments tagging a blocked user
to "[utente bloccato]". Iter120 explicitly HIDES the entire comment/reply,
regardless of authorship, if it tags a blocked user.

Test cases:
1) Own comment tagging blocked user is HIDDEN to both parties, reappears
   intact after unblock.
2) Same, but for a reply.
3) Cannot reply to a blocked user's parent comment → 403
   "Non puoi rispondere a questo utente".
4) Cannot tag a blocked user in a new post → 400 "Non puoi taggare".
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")
INTERNAL = "http://localhost:8001/api"

A_EMAIL = "chat_a@test.it"
B_EMAIL = "chat_b@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"
B_ID = "user_16f709708760"
A_NICK = "chat_a"
B_NICK = "chatUserB"
FEUD = "feud_2e5b4481a8a4"


def _login(sess, email, password):
    r = sess.post(f"{INTERNAL}/auth/login",
                  json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login {email} {r.status_code}: {r.text[:200]}"
    return r.json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def sess():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def tok(sess):
    return {"A": _login(sess, A_EMAIL, PASS), "B": _login(sess, B_EMAIL, PASS)}


def _unblock_all(sess, tok):
    sess.delete(f"{INTERNAL}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=10)
    sess.delete(f"{INTERNAL}/users/{A_ID}/block", headers=_h(tok["B"]), timeout=10)


@pytest.fixture(scope="module", autouse=True)
def _reset(sess, tok):
    # Ensure both users have voted so they can comment on FEUD.
    for k in ("A", "B"):
        sess.post(f"{INTERNAL}/feuds/{FEUD}/vote", json={"side": "A"},
                  headers=_h(tok[k]), timeout=15)
    _unblock_all(sess, tok)
    yield
    _unblock_all(sess, tok)


def _block(sess, tok, actor_key, target_id):
    r = sess.post(f"{INTERNAL}/users/{target_id}/block",
                  headers=_h(tok[actor_key]), timeout=15)
    assert r.status_code == 200, f"block failed: {r.status_code} {r.text}"


def _unblock(sess, tok, actor_key, target_id):
    sess.delete(f"{INTERNAL}/users/{target_id}/block",
                headers=_h(tok[actor_key]), timeout=10)


def _get_comment(sess, tok_key, tok, cid):
    r = sess.get(f"{INTERNAL}/feuds/{FEUD}/comments",
                 headers=_h(tok[tok_key]), timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    for c in (body.get("side_a") or []) + (body.get("side_b") or []):
        if c.get("comment_id") == cid:
            return c
    return None


# ═══════════════ Case 1: Own comment tagging blocked user is HIDDEN ═══════════════


class TestOwnCommentTaggingBlockedIsHidden:

    def test_own_comment_hidden_when_block_and_reappears_on_unblock(self, sess, tok):
        _unblock_all(sess, tok)
        # A posts comment tagging B while NOT blocked.
        marker = uuid.uuid4().hex[:6]
        text = f"hello @{B_NICK} iter120-{marker}"
        r = sess.post(f"{INTERNAL}/feuds/{FEUD}/comments",
                      json={"text": text}, headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        # Baseline: visible to both A and B with mention intact.
        found_a = _get_comment(sess, "A", tok, cid)
        assert found_a is not None, "baseline comment must be visible to A"
        assert any(m.get("user_id") == B_ID for m in (found_a.get("mentions") or []))

        found_b = _get_comment(sess, "B", tok, cid)
        assert found_b is not None, "baseline comment must be visible to B"

        # A blocks B.
        _block(sess, tok, "A", B_ID)
        try:
            # A must NOT see the comment anymore (total erasure).
            found_a2 = _get_comment(sess, "A", tok, cid)
            assert found_a2 is None, (
                f"BUG iter120: A's own comment tagging blocked B must be HIDDEN "
                f"to A; got {found_a2}"
            )
            # B (viewer as blocked party) also must NOT see it.
            found_b2 = _get_comment(sess, "B", tok, cid)
            assert found_b2 is None, (
                f"BUG iter120: A's comment tagging B must be HIDDEN to B; got {found_b2}"
            )
        finally:
            _unblock(sess, tok, "A", B_ID)

        # After unblock, comment reappears with original text/mentions intact.
        found_a3 = _get_comment(sess, "A", tok, cid)
        assert found_a3 is not None, "after unblock, comment must reappear to A"
        assert (found_a3.get("text") or "").endswith(f"iter120-{marker}"), \
            f"text must be intact; got {found_a3.get('text')!r}"
        assert f"@{B_NICK}" in (found_a3.get("text") or ""), \
            "raw @nickname must be intact"
        assert any(m.get("user_id") == B_ID for m in (found_a3.get("mentions") or [])), \
            "mentions must include B after unblock"

        found_b3 = _get_comment(sess, "B", tok, cid)
        assert found_b3 is not None, "after unblock, comment must reappear to B"


# ═══════════════ Case 2: Own reply tagging blocked user is HIDDEN ═══════════════


class TestOwnReplyTaggingBlockedIsHidden:

    def test_own_reply_hidden_when_block_and_reappears_on_unblock(self, sess, tok):
        _unblock_all(sess, tok)
        # A posts a parent comment (no mention).
        marker = uuid.uuid4().hex[:6]
        r = sess.post(f"{INTERNAL}/feuds/{FEUD}/comments",
                      json={"text": f"iter120 parent {marker}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        parent_cid = r.json()["comment"]["comment_id"]

        # A posts a reply tagging @chatUserB.
        r = sess.post(f"{INTERNAL}/comments/{parent_cid}/replies",
                      json={"text": f"reply @{B_NICK} iter120-{marker}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        rid = r.json()["reply"]["reply_id"]

        # Baseline: reply visible to A.
        r = sess.get(f"{INTERNAL}/comments/{parent_cid}/replies",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        replies = r.json().get("replies") or []
        assert any(x.get("reply_id") == rid for x in replies), "baseline reply must be visible"

        # A blocks B.
        _block(sess, tok, "A", B_ID)
        try:
            # Reply must be hidden to both A and B.
            r = sess.get(f"{INTERNAL}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            replies = r.json().get("replies") or []
            assert not any(x.get("reply_id") == rid for x in replies), (
                f"BUG iter120: A's own reply tagging blocked B must be HIDDEN to A; "
                f"got replies={[x.get('reply_id') for x in replies]}"
            )

            r = sess.get(f"{INTERNAL}/comments/{parent_cid}/replies",
                         headers=_h(tok["B"]), timeout=15)
            replies = r.json().get("replies") or []
            assert not any(x.get("reply_id") == rid for x in replies), (
                f"BUG iter120: A's reply tagging B must be HIDDEN to B"
            )
        finally:
            _unblock(sess, tok, "A", B_ID)

        # After unblock, reply reappears with text/mentions intact.
        r = sess.get(f"{INTERNAL}/comments/{parent_cid}/replies",
                     headers=_h(tok["A"]), timeout=15)
        replies = r.json().get("replies") or []
        found = next((x for x in replies if x.get("reply_id") == rid), None)
        assert found is not None, "reply must reappear to A after unblock"
        assert f"@{B_NICK}" in (found.get("text") or "")
        assert any(m.get("user_id") == B_ID for m in (found.get("mentions") or []))


# ═══════════════ Case 3: Cannot reply to blocked user's parent comment ═══════════════


class TestCannotReplyToBlockedParent:

    def test_reply_to_blocked_parent_returns_403(self, sess, tok):
        _unblock_all(sess, tok)
        # B posts a parent comment.
        r = sess.post(f"{INTERNAL}/feuds/{FEUD}/comments",
                      json={"text": f"iter120 B parent {uuid.uuid4().hex[:6]}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200, r.text
        parent_cid = r.json()["comment"]["comment_id"]

        # A blocks B.
        _block(sess, tok, "A", B_ID)
        try:
            # A tries to reply → must be 403.
            r = sess.post(f"{INTERNAL}/comments/{parent_cid}/replies",
                          json={"text": "trying to reply"},
                          headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 403, (
                f"BUG: reply to blocked user's parent must be 403; got {r.status_code} "
                f"body={r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            assert detail == "Non puoi rispondere a questo utente", (
                f"BUG: detail mismatch; expected 'Non puoi rispondere a questo utente', got {detail!r}"
            )
        finally:
            _unblock_all(sess, tok)


# ═══════════════ Case 4: Cannot tag blocked user in new post ═══════════════


class TestCannotTagBlockedInNewPost:

    def test_post_tagging_blocked_returns_400(self, sess, tok):
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            r = sess.post(f"{INTERNAL}/feuds/{FEUD}/comments",
                          json={"text": f"hi @{B_NICK} iter120-{uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 400, (
                f"BUG: tagging blocked in new post must 400; got {r.status_code} {r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            assert "Non puoi taggare" in detail, (
                f"BUG: detail must contain 'Non puoi taggare'; got {detail!r}"
            )
            assert f"@{B_NICK}" in detail, (
                f"BUG: detail must contain '@{B_NICK}'; got {detail!r}"
            )
        finally:
            _unblock_all(sess, tok)
