"""
Iteration 114 — BUG B: /api/feuds/hype must exclude comments/replies
authored by users the viewer has blocked (or been blocked by) when
computing the hype rail eligibility.

Rules covered:
  1. Feud whose ONLY commenter is B → after A blocks B, feud disappears
     from A's hype (cc+rc drops below 1).
  2. Anonymous viewers see unfiltered rail (no block filter applied).
  3. Feud with mixed authors (A + B) stays visible after A blocks B
     because A's own comment keeps cc >= 1.
  4. Unblock restores the feud to A's rail.

Because there is NO user-facing endpoint to create a feud, we seed a
throw-away feud directly in Mongo, then use the public API for
comment/vote/block interactions.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient


BASE_URL = os.environ["EXPO_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

A_EMAIL = "chat_a@test.it"
B_EMAIL = "chat_b@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"
B_ID = "user_16f709708760"


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


@pytest.fixture(scope="module")
def mdb():
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


def _unblock_all(sess, tok):
    for src, target in (("A", B_ID), ("B", A_ID)):
        sess.delete(f"{API}/users/{target}/block", headers=_h(tok[src]), timeout=10)


def _vote(sess, tok, key, feud_id, side="A"):
    r = sess.post(f"{API}/feuds/{feud_id}/vote", json={"side": side},
                  headers=_h(tok[key]), timeout=15)
    assert r.status_code in (200, 201, 400), f"vote {r.status_code}: {r.text}"


def _hype_ids(sess, token: str | None = None) -> list[str]:
    headers = _h(token) if token else {}
    r = sess.get(f"{API}/feuds/hype", headers=headers, timeout=25)
    assert r.status_code == 200, r.text
    return [f["feud_id"] for f in (r.json().get("feuds") or [])]


def _seed_feud(mdb, tag: str) -> str:
    """Insert a fresh feud directly (no user-facing endpoint exists)."""
    fid = f"feud_iter114_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    mdb.feuds.insert_one({
        "feud_id": fid,
        "title": f"HYPE-TEST-{tag} {fid}",
        "side_a_label": "A",
        "side_b_label": "B",
        "category": "curiosita",
        "context_text": "iter114 hype block filter seed",
        "created_at": now,
        "votes_a": 0,
        "votes_b": 0,
        "expires_at": None,
        "media": None,
        "kind": "test",
        "public": True,
    })
    return fid


def _cleanup_feud(mdb, fid: str):
    mdb.comments.delete_many({"feud_id": fid})
    # replies live under comment_id; comments already gone, orphan replies
    # ignore — they won't affect hype since their $lookup finds no comment.
    mdb.votes.delete_many({"feud_id": fid})
    mdb.feuds.delete_many({"feud_id": fid})


@pytest.fixture(scope="module", autouse=True)
def _reset(sess, tok):
    _unblock_all(sess, tok)
    yield
    _unblock_all(sess, tok)


class TestHypeBlockFilter:

    def test_hype_excludes_feuds_when_only_commenter_is_blocked(self, sess, tok, mdb):
        """A feud whose only commenter is B → after A blocks B, feud must
        disappear from A's hype rail; anon still sees it; unblock restores."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "only-B")
        try:
            # 1) Both vote so votes>=1 (eligibility)
            _vote(sess, tok, "A", fid, "A")
            _vote(sess, tok, "B", fid, "B")
            # 2) Only B comments
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"only-B {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200, r.text

            # 3) Baseline: A (no block) sees feud in hype
            ids_before = _hype_ids(sess, tok["A"])
            assert fid in ids_before, (
                f"Baseline: seeded feud must appear in A's hype before block. "
                f"got {len(ids_before)} feuds, {fid} missing."
            )

            # 4) Anonymous also sees it
            assert fid in _hype_ids(sess, token=None), \
                "seeded feud missing from anon hype baseline"

            # 5) A blocks B → feud disappears
            rb = sess.post(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200, rb.text

            ids_after = _hype_ids(sess, tok["A"])
            assert fid not in ids_after, (
                f"BUG B: after A blocks B, feud {fid} (only B commented) "
                f"is STILL in A's hype rail."
            )

            # 6) Anon still unaffected
            assert fid in _hype_ids(sess, token=None), (
                "Anonymous rail must be unaffected by A→B block."
            )

            # 7) Unblock → feud comes back
            sess.delete(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=10)
            assert fid in _hype_ids(sess, tok["A"]), \
                "After unblock, feud should reappear in A's hype."
        finally:
            _cleanup_feud(mdb, fid)
            _unblock_all(sess, tok)

    def test_hype_reply_by_blocked_user_alone_hides_feud(self, sess, tok, mdb):
        """Variant: B is the only interaction author (a reply on B's own
        comment). Block filter must drop cc+rc to 0 → feud hidden.
        Set up: only B comments and only B replies."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "only-B-reply")
        try:
            _vote(sess, tok, "A", fid, "A")
            _vote(sess, tok, "B", fid, "B")
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"B-parent {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200, r.text
            cid = r.json()["comment"]["comment_id"]
            sess.post(f"{API}/comments/{cid}/replies",
                      json={"text": f"B-reply {uuid.uuid4().hex[:6]}"},
                      headers=_h(tok["B"]), timeout=15)

            assert fid in _hype_ids(sess, tok["A"]), "baseline before block"
            # A blocks B
            sess.post(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=15)
            assert fid not in _hype_ids(sess, tok["A"]), (
                "BUG B: comment+reply both by blocked user → feud must vanish"
            )
        finally:
            sess.delete(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=10)
            _cleanup_feud(mdb, fid)

    def test_hype_mixed_feud_stays_visible_after_block(self, sess, tok, mdb):
        """When both A and B comment, A's block on B must NOT remove the
        feud from A's rail — A's own comment keeps cc >= 1."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "mixed")
        try:
            _vote(sess, tok, "A", fid, "A")
            _vote(sess, tok, "B", fid, "B")
            sess.post(f"{API}/feuds/{fid}/comments",
                      json={"text": f"A-mixed {uuid.uuid4().hex[:6]}"},
                      headers=_h(tok["A"]), timeout=15)
            sess.post(f"{API}/feuds/{fid}/comments",
                      json={"text": f"B-mixed {uuid.uuid4().hex[:6]}"},
                      headers=_h(tok["B"]), timeout=15)

            assert fid in _hype_ids(sess, tok["A"]), "baseline"
            sess.post(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=15)
            assert fid in _hype_ids(sess, tok["A"]), (
                "Mixed feud should stay in hype after block (A's own comment counts)."
            )
        finally:
            sess.delete(f"{API}/users/{B_ID}/block", headers=_h(tok["A"]), timeout=10)
            _cleanup_feud(mdb, fid)

    def test_hype_reverse_block_direction(self, sess, tok, mdb):
        """Symmetric case: B blocks A. A feud whose only commenter is A
        must disappear from B's hype but stay visible in anon rail."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "only-A-vs-B-blocks")
        try:
            _vote(sess, tok, "A", fid, "A")
            _vote(sess, tok, "B", fid, "B")
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"A-only {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200

            assert fid in _hype_ids(sess, tok["B"]), "baseline B sees feud"
            sess.post(f"{API}/users/{A_ID}/block", headers=_h(tok["B"]), timeout=15)
            assert fid not in _hype_ids(sess, tok["B"]), (
                "BUG B (symmetry): after B blocks A, feud with only-A comments "
                "must disappear from B's hype."
            )
            # Sanity: from A's own POV, A's own comment still counts even
            # though B blocked A (viewer_blocked for A = {B} bidirectionally,
            # but A's user_id is not in that set → cc>=1 preserved).
            assert fid in _hype_ids(sess, tok["A"]), (
                "A must still see own-comment feud in hype even when B blocked A."
            )
        finally:
            sess.delete(f"{API}/users/{A_ID}/block", headers=_h(tok["B"]), timeout=10)
            _cleanup_feud(mdb, fid)

    def test_hype_response_schema_intact(self, sess, tok):
        """Regression: /feuds/hype schema unchanged and no _id leaks."""
        for token in (None, tok["A"]):
            headers = _h(token) if token else {}
            r = sess.get(f"{API}/feuds/hype", headers=headers, timeout=20)
            assert r.status_code == 200
            body = r.json()
            assert isinstance(body.get("feuds"), list)
            assert body.get("source") == "hype"
            assert body.get("personalized") is False
            for f in body["feuds"]:
                assert "feud_id" in f
                assert "_id" not in f, "MongoDB _id leaked into hype response"
