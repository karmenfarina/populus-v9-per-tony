"""Iteration 116 backend regression suite for Populus.

Covers three tightened behaviours:

1. HYPE stricter threshold (`GET /api/feuds/hype`):
   - A feud must have BOTH `total_votes >= 2` AND at least 2 VISIBLE
     comments/replies to appear.  Previously `>=1` on each side.

2. @mention scrubbing on comments (`GET /api/feuds/{fid}/comments`):
   - When viewer A has blocked user B, any surviving comment (from A
     themselves or any 3rd party) whose text mentions `@chatUserB`
     must be returned with:
       * text replaced → `@chatUserB` → `[utente bloccato]`
       * `mentions` array with B's entry removed.
   - Unblock → payload restored to original text + mentions.

3. Same scrubbing on replies (`GET /api/comments/{cid}/replies`).

Also runs a subset of iter115 regressions to make sure the block
filter still hides comments authored BY the blocked user entirely.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/")
API = f"{BASE_URL}/api"

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

A_EMAIL = "chat_a@test.it"
B_EMAIL = "chat_b@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"
B_ID = "user_16f709708760"
A_NICK = "chat_a"
B_NICK = "chatuserb"  # lowercased at write time

BLOCK_FEUD = "feud_7c6d16e4baee"


# ─────────────────────── helpers / fixtures ───────────────────────


def _login(sess, email, password):
    r = sess.post(f"{API}/auth/login",
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


@pytest.fixture(scope="module")
def mdb():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


def _unblock_all(sess, tok):
    for src, target in (("A", B_ID), ("B", A_ID)):
        sess.delete(f"{API}/users/{target}/block",
                    headers=_h(tok[src]), timeout=10)


def _hype_body(sess, token=None):
    headers = _h(token) if token else {}
    r = sess.get(f"{API}/feuds/hype?limit=200", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def _hype_ids(sess, token=None):
    return [f["feud_id"] for f in _hype_body(sess, token).get("feuds") or []]


def _seed_feud(mdb, tag):
    fid = f"feud_iter116_{uuid.uuid4().hex[:10]}"
    mdb.feuds.insert_one({
        "feud_id": fid,
        "title": f"ITER116-{tag} {fid}",
        "side_a_label": "A",
        "side_b_label": "B",
        "category": "curiosita",
        "context_text": "iter116 seed",
        "created_at": datetime.now(timezone.utc),
        "votes_a": 0,
        "votes_b": 0,
        "expires_at": None,
        "media": None,
        "kind": "test",
        "public": True,
    })
    return fid


def _cleanup_feud(mdb, fid):
    mdb.comments.delete_many({"feud_id": fid})
    mdb.replies.delete_many({"feud_id": fid})
    mdb.votes.delete_many({"feud_id": fid})
    mdb.feuds.delete_many({"feud_id": fid})


def _vote(sess, tok, key, fid, side="A"):
    r = sess.post(f"{API}/feuds/{fid}/vote", json={"side": side},
                  headers=_h(tok[key]), timeout=15)
    assert r.status_code in (200, 201, 400), f"vote {r.status_code}: {r.text}"


def _find_comment(payload, cid):
    for k in ("side_a", "side_b", "comments"):
        for c in payload.get(k) or []:
            if c.get("comment_id") == cid:
                return c
    return None


def _all_authors(payload):
    ids = set()
    for k in ("side_a", "side_b", "comments"):
        for c in payload.get(k) or []:
            uid = c.get("user_id")
            if uid:
                ids.add(uid)
    return ids


@pytest.fixture(scope="module", autouse=True)
def _reset(sess, tok):
    _unblock_all(sess, tok)
    yield
    _unblock_all(sess, tok)


# ═══════════════════════ GROUP 1 — HYPE stricter threshold (>=2, >=2) ═══════════════════════


class TestHypeStricterThreshold:

    def test_global_invariant_no_low_engagement_feuds(self, sess, tok, mdb):
        """Every returned HYPE feud must have total_votes>=2 AND at
        least 2 VISIBLE comments+replies combined."""
        body = _hype_body(sess, token=tok["A"])
        feuds = body.get("feuds") or []
        offenders_votes = []
        offenders_engagement = []
        for f in feuds:
            fid = f["feud_id"]
            va = int(f.get("votes_a", 0) or 0)
            vb = int(f.get("votes_b", 0) or 0)
            if va + vb < 2:
                offenders_votes.append((fid, va + vb))

            visible = 0
            for c in mdb.comments.find({"feud_id": fid},
                                        {"_id": 0, "user_id": 1, "side": 1}):
                v = mdb.votes.find_one({"feud_id": fid, "user_id": c["user_id"]},
                                       {"_id": 0, "side": 1})
                if v is None or v.get("side") == c.get("side"):
                    visible += 1
            for rr in mdb.replies.find({"feud_id": fid},
                                        {"_id": 0, "user_id": 1, "side": 1}):
                v = mdb.votes.find_one({"feud_id": fid, "user_id": rr["user_id"]},
                                       {"_id": 0, "side": 1})
                if v is None or v.get("side") == rr.get("side"):
                    visible += 1
            if visible < 2:
                offenders_engagement.append((fid, visible))

        assert not offenders_votes, (
            f"HYPE leaked feuds with <2 total votes: {offenders_votes[:5]}"
        )
        assert not offenders_engagement, (
            f"HYPE leaked feuds with <2 visible comments+replies: "
            f"{offenders_engagement[:5]}"
        )

    def test_step_by_step_threshold(self, sess, tok, mdb):
        """1 vote + 1 comment → hidden.  Add 2nd vote → still hidden
        (only 1 comment).  Add 2nd comment → NOW visible."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "threshold")
        try:
            # Step 1: A votes side A + comments once
            _vote(sess, tok, "A", fid, "A")
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"iter116-cA {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200, r.text
            assert fid not in _hype_ids(sess, tok["A"]), (
                "HYPE should NOT show a feud with only 1 vote + 1 comment "
                "(threshold is >=2 of each)"
            )

            # Step 2: B also votes → 2 votes, still only 1 comment → hidden
            _vote(sess, tok, "B", fid, "B")
            assert fid not in _hype_ids(sess, tok["A"]), (
                "HYPE should NOT show a feud with 2 votes but only 1 comment"
            )

            # Step 3: B comments too → 2 votes + 2 comments → shown
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"iter116-cB {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200, r.text
            assert fid in _hype_ids(sess, tok["A"]), (
                "HYPE should NOW surface the feud: 2 votes + 2 visible comments"
            )
        finally:
            _cleanup_feud(mdb, fid)


# ═══════════════════════ GROUP 2 — @mention scrubbing on comments ═══════════════════════


class TestMentionScrubbingOnComments:

    @pytest.mark.xfail(reason="Superseded by iter120: own comment tagging a blocked user is now HIDDEN entirely, not scrubbed.", strict=True)
    def test_scrub_mention_of_blocked_user_in_own_comment(self, sess, tok, mdb):
        """A posts a comment mentioning @chatUserB BEFORE blocking.
        After blocking B, fetching comments returns:
            - text → `[utente bloccato]` in place of `@chatUserB`
            - mentions array → without B's entry.
        After unblock → original text and mentions restored."""
        _unblock_all(sess, tok)
        # Ensure A has a vote so A's own comment is visible
        _vote(sess, tok, "A", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        original_text = f"hey @chatUserB test123 iter116-{stamp}"
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": original_text},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        try:
            # Baseline (no block): mentions array should contain B
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            baseline = _find_comment(r.json(), cid)
            assert baseline is not None, "baseline: A must see own comment"
            baseline_mentions = baseline.get("mentions") or []
            baseline_mention_uids = {m.get("user_id") for m in baseline_mentions
                                     if isinstance(m, dict)}
            assert B_ID in baseline_mention_uids, (
                f"baseline: mentions array must contain B ({B_ID}); "
                f"got {baseline_mentions}"
            )
            assert "@chatUserB" in baseline.get("text", "") or \
                   "@chatuserb" in baseline.get("text", "").lower(), (
                f"baseline: text should still contain @chatUserB; got "
                f"{baseline.get('text')}"
            )

            # A blocks B
            rb = sess.post(f"{API}/users/{B_ID}/block",
                           headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200 and rb.json().get("blocked") is True

            # Fetch again as A — mention must be scrubbed
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            after = _find_comment(r.json(), cid)
            assert after is not None, (
                "A's own comment must still be visible after A blocks B"
            )
            after_text = after.get("text", "")
            after_mentions = after.get("mentions") or []
            after_mention_uids = {m.get("user_id") for m in after_mentions
                                  if isinstance(m, dict)}

            assert B_ID not in after_mention_uids, (
                f"BUG: after A blocks B, B's user_id must be removed from "
                f"the mentions array; got {after_mentions}"
            )
            assert "[utente bloccato]" in after_text, (
                f"BUG: text must contain the placeholder [utente bloccato]; "
                f"got: {after_text!r}"
            )
            # And no raw @chatuserb (case-insensitive) anywhere
            assert not re.search(r"(?i)@chatuserb", after_text), (
                f"BUG: raw @chatUserB still appears in scrubbed text: "
                f"{after_text!r}"
            )

            # Unblock → mentions & text restored
            ru = sess.delete(f"{API}/users/{B_ID}/block",
                             headers=_h(tok["A"]), timeout=10)
            assert ru.status_code == 200
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            restored = _find_comment(r.json(), cid)
            assert restored is not None
            restored_text = restored.get("text", "")
            restored_mentions = restored.get("mentions") or []
            restored_mention_uids = {m.get("user_id") for m in restored_mentions
                                     if isinstance(m, dict)}
            assert B_ID in restored_mention_uids, (
                f"after unblock: B must be back in mentions; got "
                f"{restored_mentions}"
            )
            assert "[utente bloccato]" not in restored_text, (
                f"after unblock: placeholder must be gone; got {restored_text!r}"
            )
            assert re.search(r"(?i)@chatuserb", restored_text), (
                f"after unblock: original @chatUserB should be restored; "
                f"got {restored_text!r}"
            )
        finally:
            mdb.comments.delete_many({"comment_id": cid})
            _unblock_all(sess, tok)


# ═══════════════════════ GROUP 3 — @mention scrubbing on replies ═══════════════════════


class TestMentionScrubbingOnReplies:

    @pytest.mark.xfail(reason="Superseded by iter120: own reply tagging a blocked user is now HIDDEN entirely, not scrubbed.", strict=True)
    def test_scrub_mention_of_blocked_user_in_reply(self, sess, tok, mdb):
        """A posts a parent comment (no mention), then a reply
        mentioning @chatUserB.  After A blocks B, fetching the replies
        returns the reply with text scrubbed and mentions filtered.
        Unblock → restored."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        # Parent comment (plain, no mention)
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter116-parent {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        parent_cid = r.json()["comment"]["comment_id"]

        original_reply_text = f"ciao @chatUserB reply-iter116 {stamp}"
        r = sess.post(f"{API}/comments/{parent_cid}/replies",
                      json={"text": original_reply_text},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        rid = r.json()["reply"]["reply_id"]

        try:
            # Baseline: mentions contains B
            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            baseline = next((rp for rp in reps if rp.get("reply_id") == rid), None)
            assert baseline is not None, "baseline: reply must be visible to A"
            base_mention_uids = {m.get("user_id") for m in
                                 (baseline.get("mentions") or [])
                                 if isinstance(m, dict)}
            assert B_ID in base_mention_uids, (
                f"baseline: reply mentions must contain B; "
                f"got {baseline.get('mentions')}"
            )
            assert re.search(r"(?i)@chatuserb", baseline.get("text", "")), (
                f"baseline: reply text must contain @chatUserB; "
                f"got {baseline.get('text')!r}"
            )

            # A blocks B
            rb = sess.post(f"{API}/users/{B_ID}/block",
                           headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200

            # Fetch replies as A — mention must be scrubbed
            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            after = next((rp for rp in reps if rp.get("reply_id") == rid), None)
            assert after is not None, "A's own reply must survive block"
            after_text = after.get("text", "")
            after_mention_uids = {m.get("user_id") for m in
                                  (after.get("mentions") or [])
                                  if isinstance(m, dict)}
            assert B_ID not in after_mention_uids, (
                f"BUG: reply mentions still contain blocked B; "
                f"got {after.get('mentions')}"
            )
            assert "[utente bloccato]" in after_text, (
                f"BUG: reply text missing placeholder; got {after_text!r}"
            )
            assert not re.search(r"(?i)@chatuserb", after_text), (
                f"BUG: raw @chatUserB still in scrubbed reply text: "
                f"{after_text!r}"
            )

            # Unblock → restored
            sess.delete(f"{API}/users/{B_ID}/block",
                        headers=_h(tok["A"]), timeout=10)
            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            restored = next((rp for rp in reps if rp.get("reply_id") == rid), None)
            assert restored is not None
            restored_text = restored.get("text", "")
            restored_uids = {m.get("user_id") for m in
                             (restored.get("mentions") or [])
                             if isinstance(m, dict)}
            assert B_ID in restored_uids, (
                "after unblock: reply mentions must contain B again"
            )
            assert "[utente bloccato]" not in restored_text, (
                f"after unblock: placeholder must be gone: {restored_text!r}"
            )
            assert re.search(r"(?i)@chatuserb", restored_text), (
                f"after unblock: @chatUserB must be restored; "
                f"got {restored_text!r}"
            )
        finally:
            mdb.replies.delete_many({"reply_id": rid})
            mdb.comments.delete_many({"comment_id": parent_cid})
            _unblock_all(sess, tok)


# ═══════════════════════ GROUP 4 — Regression: bi-directional block hides authored comments ═══════════════════════


class TestRegressionBlockHidesAuthoredComments:

    def test_comment_from_blocked_user_hidden_entirely(self, sess, tok, mdb):
        """When A blocks B, B's own comments must be completely absent
        from A's /comments response (not merely their mentions).  And
        vice-versa (bi-directional)."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")
        stamp = uuid.uuid4().hex[:6]

        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter116-regA {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        cid_a = r.json()["comment"]["comment_id"]

        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter116-regB {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200
        cid_b = r.json()["comment"]["comment_id"]

        try:
            # baseline
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert B_ID in _all_authors(r.json())
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert A_ID in _all_authors(r.json())

            # A blocks B
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)

            # From A: B's comment gone
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid_b) is None, (
                "REGRESSION: B's comment must be hidden from A after A blocks B"
            )
            # From B: A's comment gone (bi-directional)
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert _find_comment(r.json(), cid_a) is None, (
                "REGRESSION: bi-directional block failed — B still sees A"
            )
        finally:
            mdb.comments.delete_many({"comment_id": {"$in": [cid_a, cid_b]}})
            _unblock_all(sess, tok)
