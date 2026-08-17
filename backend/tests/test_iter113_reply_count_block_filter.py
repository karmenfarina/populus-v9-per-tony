"""
Iteration 113 — BUG A: get_comments' reply_count must exclude replies
whose author is in a bi-directional block relationship with the viewer.

Setup contract (see review request):
  - Unblock both directions before each test
  - Both users must vote on the SAME side of the feud so their
    comment/reply is even visible in that side's thread
  - Target feud: feud_198672d881cc (side B per user_report)
"""
from __future__ import annotations

import os
import uuid
import pytest
import requests


BASE_URL = os.environ["EXPO_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

A_EMAIL = "chat_a@test.it"
B_EMAIL = "chat_b@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"
B_ID = "user_16f709708760"
FEUD_ID = "feud_198672d881cc"
SIDE = "B"


def _login(sess, email, password):
    r = sess.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login {r.status_code}: {r.text}"
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
    for src, target in (("A", B_ID), ("B", A_ID)):
        sess.delete(f"{API}/users/{target}/block", headers=_h(tok[src]), timeout=10)


def _vote(sess, tok, key, side=SIDE):
    r = sess.post(f"{API}/feuds/{FEUD_ID}/vote", json={"side": side},
                  headers=_h(tok[key]), timeout=15)
    assert r.status_code in (200, 201, 400), f"vote {r.status_code}: {r.text}"


def _find_comment(payload: dict, cid: str) -> dict | None:
    for k in ("side_a", "side_b", "comments"):
        for c in payload.get(k) or []:
            if c.get("comment_id") == cid:
                return c
    return None


@pytest.fixture(scope="module", autouse=True)
def _reset(sess, tok):
    _unblock_all(sess, tok)
    _vote(sess, tok, "A")
    _vote(sess, tok, "B")
    yield
    _unblock_all(sess, tok)


class TestReplyCountBlockFilter:
    """BUG A: reply_count on parent comment must respect viewer's blocks."""

    def test_a_blocks_b_reply_count_zero_from_a_perspective(self, sess, tok):
        """A posts comment, B replies, A blocks B →
        A's reply_count on their own comment goes from 1 → 0."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A"); _vote(sess, tok, "B")
        stamp = uuid.uuid4().hex[:6]

        # 1) A posts a comment
        r = sess.post(f"{API}/feuds/{FEUD_ID}/comments",
                      json={"text": f"iter113-A {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        # 2) B replies
        r = sess.post(f"{API}/comments/{cid}/replies",
                      json={"text": f"iter113-B-reply {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200, r.text
        rid = r.json()["reply"]["reply_id"]

        # 3) Baseline: A viewing feud → reply_count = 1
        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        c = _find_comment(r.json(), cid)
        assert c is not None, f"A cannot see own comment {cid}"
        assert c.get("reply_count") == 1, \
            f"baseline reply_count expected 1, got {c.get('reply_count')}"

        # 4) A blocks B
        rb = sess.post(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=15)
        assert rb.status_code == 200 and rb.json().get("blocked") is True, rb.text

        # 5) A re-loads comments → reply_count must drop to 0
        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        c2 = _find_comment(r.json(), cid)
        assert c2 is not None, "A lost visibility on own comment after blocking B"
        assert c2.get("reply_count") == 0, \
            f"BUG A: reply_count expected 0 after block, got {c2.get('reply_count')}"

        # 6) A GETs /replies → empty list
        r = sess.get(f"{API}/comments/{cid}/replies",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        replies = r.json().get("replies") or []
        assert not any(rp.get("reply_id") == rid for rp in replies), \
            f"blocked user's reply still returned by /replies: {replies}"

        # cleanup
        _unblock_all(sess, tok)

    def test_b_blocks_a_symmetry(self, sess, tok):
        """Reverse direction: B posts comment, A replies, B blocks A →
        B's reply_count on their own comment drops 1 → 0."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A"); _vote(sess, tok, "B")
        stamp = uuid.uuid4().hex[:6]

        # B posts, A replies
        r = sess.post(f"{API}/feuds/{FEUD_ID}/comments",
                      json={"text": f"iter113-B {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        r = sess.post(f"{API}/comments/{cid}/replies",
                      json={"text": f"iter113-A-reply {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        rid = r.json()["reply"]["reply_id"]

        # Baseline
        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments",
                     headers=_h(tok["B"]), timeout=15)
        c = _find_comment(r.json(), cid)
        assert c is not None
        assert c.get("reply_count") == 1, \
            f"baseline reply_count expected 1, got {c.get('reply_count')}"

        # B blocks A
        rb = sess.post(f"{API}/users/{A_ID}/block", headers=_h(tok["B"]), timeout=15)
        assert rb.status_code == 200 and rb.json().get("blocked") is True

        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments",
                     headers=_h(tok["B"]), timeout=15)
        c2 = _find_comment(r.json(), cid)
        assert c2 is not None
        assert c2.get("reply_count") == 0, \
            f"BUG A (symmetry): reply_count expected 0 after block, got {c2.get('reply_count')}"

        # /replies empty
        r = sess.get(f"{API}/comments/{cid}/replies",
                     headers=_h(tok["B"]), timeout=15)
        replies = r.json().get("replies") or []
        assert not any(rp.get("reply_id") == rid for rp in replies)

        _unblock_all(sess, tok)

    def test_reply_count_intact_when_no_block(self, sess, tok):
        """Sanity: without blocks, reply_count stays at 1."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A"); _vote(sess, tok, "B")
        stamp = uuid.uuid4().hex[:6]
        r = sess.post(f"{API}/feuds/{FEUD_ID}/comments",
                      json={"text": f"iter113-nb-A {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        cid = r.json()["comment"]["comment_id"]
        sess.post(f"{API}/comments/{cid}/replies",
                  json={"text": f"iter113-nb-B {stamp}"},
                  headers=_h(tok["B"]), timeout=15)
        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments",
                     headers=_h(tok["A"]), timeout=15)
        c = _find_comment(r.json(), cid)
        assert c.get("reply_count") == 1, \
            f"no-block reply_count expected 1, got {c.get('reply_count')}"

    def test_anonymous_viewer_sees_full_reply_count(self, sess, tok):
        """Block filter only applies when a viewer is authenticated —
        anonymous /comments should still count all replies."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A"); _vote(sess, tok, "B")
        stamp = uuid.uuid4().hex[:6]
        r = sess.post(f"{API}/feuds/{FEUD_ID}/comments",
                      json={"text": f"iter113-anon-A {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        cid = r.json()["comment"]["comment_id"]
        sess.post(f"{API}/comments/{cid}/replies",
                  json={"text": f"iter113-anon-B {stamp}"},
                  headers=_h(tok["B"]), timeout=15)
        # Block one direction; anon shouldn't care.
        sess.post(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=15)
        r = sess.get(f"{API}/feuds/{FEUD_ID}/comments", timeout=15)
        c = _find_comment(r.json(), cid)
        assert c is not None
        assert c.get("reply_count") == 1, \
            f"anon reply_count should be 1, got {c.get('reply_count')}"
        _unblock_all(sess, tok)
