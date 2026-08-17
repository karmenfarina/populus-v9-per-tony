"""Iteration 115 — regression suite for three persistent Populus bugs:

1. HYPE endpoint (GET /api/feuds/hype) must ONLY surface feuds that
   have BOTH `total_votes >= 1` AND at least one VISIBLE comment/reply
   (author's current vote still matches the side the comment/reply was
   posted on). Vote-flip → comment must decrement out of the count.

2. Bi-directional block filter on comments/replies:
   - GET /api/feuds/{fid}/comments and GET /api/comments/{cid}/replies
     must hide items whose author is in a block pair with the viewer.

3. Bi-directional block filter on notifications:
   - GET /api/notifications and /api/notifications/unread-count must
     exclude entries whose `actor_id` is in a block pair with the viewer.

Base URL comes from EXPO_PUBLIC_BACKEND_URL (public preview).  MongoDB
comes from MONGO_URL + DB_NAME (backend .env).
"""
from __future__ import annotations

import os
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
    or os.environ["EXPO_PUBLIC_BACKEND_URL"]
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
B_NICK = "chatuserb"  # mentions are case-insensitive & lowercased

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


def _hype_ids(sess, token=None):
    headers = _h(token) if token else {}
    r = sess.get(f"{API}/feuds/hype", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    return [f["feud_id"] for f in (r.json().get("feuds") or [])]


def _hype_body(sess, token=None):
    headers = _h(token) if token else {}
    r = sess.get(f"{API}/feuds/hype", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_feud(mdb, tag):
    fid = f"feud_iter115_{uuid.uuid4().hex[:10]}"
    mdb.feuds.insert_one({
        "feud_id": fid,
        "title": f"ITER115-{tag} {fid}",
        "side_a_label": "A",
        "side_b_label": "B",
        "category": "curiosita",
        "context_text": "iter115 seed",
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
    # 200/201 accepted, 400 acceptable when re-voting same side
    assert r.status_code in (200, 201, 400), f"vote {r.status_code}: {r.text}"


def _find_comment(payload, cid):
    for k in ("side_a", "side_b", "comments"):
        for c in payload.get(k) or []:
            if c.get("comment_id") == cid:
                return c
    return None


def _all_authors(payload):
    """Return set of user_ids present in any bucket of a /comments response."""
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


# ═══════════════════════ TEST GROUP 1 — /feuds/hype filter ═══════════════════════


class TestHypeVisibleCountsFilter:
    """Bug #2 — HYPE must only surface hot feuds with BOTH votes>=1 AND
    at least one VISIBLE comment/reply.  Vote-flip → comment must
    disappear from the count."""

    def test_no_zero_vote_or_zero_visible_comment_in_hype(self, sess, tok, mdb):
        """Global invariant: every returned feud has votes>=1 AND at
        least one comment/reply whose author's current vote matches
        the side the comment was posted on."""
        body = _hype_body(sess, token=tok["A"])
        feuds = body.get("feuds") or []
        assert isinstance(feuds, list)

        offenders_zero_votes = []
        offenders_no_visible = []
        for f in feuds:
            fid = f["feud_id"]
            va = int(f.get("votes_a", 0) or 0)
            vb = int(f.get("votes_b", 0) or 0)
            if va + vb < 1:
                offenders_zero_votes.append(fid)

            # Count VISIBLE comments/replies exactly like the endpoint should
            visible_c = 0
            for c in mdb.comments.find({"feud_id": fid},
                                        {"_id": 0, "user_id": 1, "side": 1}):
                v = mdb.votes.find_one({"feud_id": fid, "user_id": c["user_id"]},
                                       {"_id": 0, "side": 1})
                if v is None or v.get("side") == c.get("side"):
                    visible_c += 1
            visible_r = 0
            for r in mdb.replies.find({"feud_id": fid},
                                       {"_id": 0, "user_id": 1, "side": 1}):
                v = mdb.votes.find_one({"feud_id": fid, "user_id": r["user_id"]},
                                       {"_id": 0, "side": 1})
                if v is None or v.get("side") == r.get("side"):
                    visible_r += 1
            if visible_c + visible_r < 1:
                offenders_no_visible.append((fid, visible_c, visible_r))

        assert not offenders_zero_votes, (
            f"HYPE leaked feuds with 0 total votes: {offenders_zero_votes[:5]}"
        )
        assert not offenders_no_visible, (
            f"HYPE leaked feuds with 0 visible comments+replies: "
            f"{offenders_no_visible[:5]}"
        )

    def test_fresh_feud_with_comment_but_no_vote_is_hidden(self, sess, tok, mdb):
        """Seed a fresh feud, add a comment but do NOT vote → must NOT
        appear in HYPE.  Then vote on the matching side → must appear."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "no-vote")
        try:
            # Add comment as A without voting first — but /comments requires
            # the author to have a vote (side is inferred from the vote).
            # So we insert the comment directly in Mongo to mimic a "no
            # vote" state.  This is legal because HYPE derives visibility
            # from the votes collection, not from the API endpoint.
            cid = f"cmt_iter115_{uuid.uuid4().hex[:10]}"
            mdb.comments.insert_one({
                "comment_id": cid,
                "feud_id": fid,
                "user_id": A_ID,
                "text": "iter115 no-vote",
                "side": "A",
                "created_at": datetime.now(timezone.utc),
            })
            # No votes on the feud at all → total_votes=0 → must be excluded
            assert fid not in _hype_ids(sess, tok["A"]), (
                "HYPE surfaced a feud with 0 votes (only had a comment)."
            )
            # Now A votes side A → total_votes=1 AND visible comment
            _vote(sess, tok, "A", fid, "A")
            # Also add B's vote to be sure votes_a increments in feud doc
            _vote(sess, tok, "B", fid, "B")
            assert fid in _hype_ids(sess, tok["A"]), (
                "HYPE excluded a feud that now has vote+visible comment."
            )
        finally:
            _cleanup_feud(mdb, fid)

    def test_vote_flip_hides_comment_from_hype(self, sess, tok, mdb):
        """Setup: A comments on side A after voting A. Then A flips to
        side B (vote change). Now the comment's side (A) differs from
        A's current vote (B) → invisible → feud drops from HYPE."""
        _unblock_all(sess, tok)
        fid = _seed_feud(mdb, "flip")
        try:
            # A votes A and comments → visible comment
            _vote(sess, tok, "A", fid, "A")
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"iter115-flip {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200, r.text
            # Also let B vote so total_votes >= 2 (satisfy votes>=1 requirement
            # even after A flips; we want to isolate the visibility rule).
            _vote(sess, tok, "B", fid, "B")

            # Baseline: appears in HYPE (has votes + visible comment)
            assert fid in _hype_ids(sess, tok["A"]), (
                "Baseline: seeded feud must appear in HYPE before flip."
            )

            # A flips to side B → their comment (side A) is no longer visible
            _vote(sess, tok, "A", fid, "B")
            time.sleep(0.5)

            # Assert: comment is no longer counted. Since B did not
            # comment, visible_comments becomes 0 → feud must drop out.
            assert fid not in _hype_ids(sess, tok["A"]), (
                "BUG: after A flips vote, their comment on the old side "
                "must not be counted → feud should drop from HYPE."
            )
        finally:
            _cleanup_feud(mdb, fid)


# ═══════════════════════ TEST GROUP 2 — block filter on comments/replies ═══════════════════════


class TestBiDirectionalBlockOnComments:
    """Bug #1 — GET /feuds/{fid}/comments and /comments/{cid}/replies
    must hide items whose author is in a block pair with the viewer,
    in BOTH directions."""

    def test_block_hides_other_users_comments_bidirectional(self, sess, tok, mdb):
        _unblock_all(sess, tok)
        stamp = uuid.uuid4().hex[:6]

        # Both users must have a vote so their comments become visible.
        # We don't care which side, but let's put both on side A so
        # they land in the same bucket.
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")

        # A posts a comment
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter115-A {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        cid_a = r.json()["comment"]["comment_id"]

        # B posts a comment
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter115-B {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200, r.text
        cid_b = r.json()["comment"]["comment_id"]

        try:
            # Baseline: A sees B's comment, B sees A's comment
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            authors_a_view = _all_authors(r.json())
            assert B_ID in authors_a_view, "baseline: A must see B's comment"

            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200
            authors_b_view = _all_authors(r.json())
            assert A_ID in authors_b_view, "baseline: B must see A's comment"

            # A blocks B
            rb = sess.post(f"{API}/users/{B_ID}/block",
                           headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200 and rb.json().get("blocked") is True, rb.text

            # From A's POV: B's comment gone
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid_b) is None, (
                "BUG #1: A→B block did not hide B's comment from A's view"
            )

            # From B's POV: A's comment ALSO gone (bi-directional)
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert _find_comment(r.json(), cid_a) is None, (
                "BUG #1: bi-directional block failed — B still sees A's "
                "comment even though A blocked B"
            )

            # Unblock → both sides see each other again
            sess.delete(f"{API}/users/{B_ID}/block",
                        headers=_h(tok["A"]), timeout=10)

            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid_b) is not None, (
                "after unblock: A should see B's comment again"
            )
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert _find_comment(r.json(), cid_a) is not None, (
                "after unblock: B should see A's comment again"
            )
        finally:
            mdb.comments.delete_many({"comment_id": {"$in": [cid_a, cid_b]}})
            _unblock_all(sess, tok)

    def test_block_hides_replies_bidirectional(self, sess, tok, mdb):
        _unblock_all(sess, tok)
        stamp = uuid.uuid4().hex[:6]
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")

        # A posts parent, B replies; then B posts parent, A replies
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter115-parA {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        parA = r.json()["comment"]["comment_id"]
        r = sess.post(f"{API}/comments/{parA}/replies",
                      json={"text": f"iter115-repB {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200
        repB = r.json()["reply"]["reply_id"]

        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter115-parB {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200
        parB = r.json()["comment"]["comment_id"]
        r = sess.post(f"{API}/comments/{parB}/replies",
                      json={"text": f"iter115-repA {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        repA = r.json()["reply"]["reply_id"]

        try:
            # A blocks B
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)

            # From A's side: querying replies on parA (their own comment)
            # must NOT show B's reply.
            r = sess.get(f"{API}/comments/{parA}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            assert not any(rp.get("reply_id") == repB for rp in reps), (
                "BUG #1: A's /replies on own comment still returned B's reply"
            )

            # From B's side (bidirectional): querying replies on parB
            # must NOT show A's reply.
            r = sess.get(f"{API}/comments/{parB}/replies",
                         headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            assert not any(rp.get("reply_id") == repA for rp in reps), (
                "BUG #1: bi-directional block failed for replies — B "
                "still sees A's reply"
            )

            # Unblock → both replies reappear
            sess.delete(f"{API}/users/{B_ID}/block",
                        headers=_h(tok["A"]), timeout=10)
            r = sess.get(f"{API}/comments/{parA}/replies",
                         headers=_h(tok["A"]), timeout=15)
            reps = r.json().get("replies") or []
            assert any(rp.get("reply_id") == repB for rp in reps), (
                "after unblock: A should see B's reply again"
            )
            r = sess.get(f"{API}/comments/{parB}/replies",
                         headers=_h(tok["B"]), timeout=15)
            reps = r.json().get("replies") or []
            assert any(rp.get("reply_id") == repA for rp in reps), (
                "after unblock: B should see A's reply again"
            )
        finally:
            mdb.replies.delete_many({"reply_id": {"$in": [repA, repB]}})
            mdb.comments.delete_many({"comment_id": {"$in": [parA, parB]}})
            _unblock_all(sess, tok)


# ═══════════════════════ TEST GROUP 3 — block filter on notifications ═══════════════════════


class TestNotificationsBlockFilter:
    """Bug #3 wrt /notifications & /notifications/unread-count — the
    bell icon and the notifications list must not include entries
    whose `actor_id` is in a block pair with the viewer."""

    def _list_ids(self, sess, tok_key, tok):
        r = sess.get(f"{API}/notifications", headers=_h(tok[tok_key]), timeout=15)
        assert r.status_code == 200, r.text
        return r.json().get("notifications") or []

    def _unread(self, sess, tok_key, tok):
        r = sess.get(f"{API}/notifications/unread-count",
                     headers=_h(tok[tok_key]), timeout=15)
        assert r.status_code == 200, r.text
        return int(r.json().get("count", 0))

    def test_mention_from_blocked_user_hidden(self, sess, tok, mdb):
        _unblock_all(sess, tok)
        # Mark all A's existing notifications as read to isolate the delta
        sess.post(f"{API}/notifications/mark-read",
                  headers=_h(tok["A"]), timeout=10)

        # Both need votes so mention comment is valid
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")

        # A blocks B FIRST
        rb = sess.post(f"{API}/users/{B_ID}/block",
                       headers=_h(tok["A"]), timeout=15)
        assert rb.status_code == 200

        # B posts a comment mentioning @chat_a
        stamp = uuid.uuid4().hex[:6]
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"@{A_NICK} iter115-mention {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        # Wait for the async _emit_notification task
        time.sleep(2.5)

        try:
            # Fetch A's notifications: no mention from B should appear
            notifs = self._list_ids(sess, "A", tok)
            offender = [n for n in notifs
                        if n.get("type") == "mention"
                        and n.get("actor_id") == B_ID
                        and n.get("comment_id") == cid]
            assert not offender, (
                f"BUG #3: mention from blocked B is present in A's "
                f"notifications: {offender[:2]}"
            )

            # Unread count for A should also exclude it
            unread = self._unread(sess, "A", tok)
            # There may be pre-existing unread notifications from other tests,
            # but at minimum the specific mention shouldn't be counted. We
            # verify by asserting that the notification with the specific
            # comment_id + unread state is not in the list (already done above)
            # AND that the unread count equals the number of unread notifs
            # actually returned by /notifications (they must be in sync).
            unread_in_list = sum(1 for n in notifs if not n.get("read"))
            assert unread == unread_in_list, (
                f"unread-count ({unread}) out of sync with /notifications "
                f"unread items ({unread_in_list})"
            )

            # Also verify direct DB state: notification either was NOT
            # persisted OR is filtered out at API level.
            db_persisted = mdb.notifications.count_documents({
                "user_id": A_ID, "actor_id": B_ID, "comment_id": cid,
            })
            # Either way is acceptable (skip persistence in _emit_notification,
            # OR filter at query time). We just care that API doesn't return it.
            # Log for debugging.
            print(f"[iter115] mention notif persisted in DB while blocked: {db_persisted}")

            # Now unblock and have B post ANOTHER mention → should now appear
            sess.delete(f"{API}/users/{B_ID}/block",
                        headers=_h(tok["A"]), timeout=10)
            stamp2 = uuid.uuid4().hex[:6]
            r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                          json={"text": f"@{A_NICK} iter115-mention2 {stamp2}"},
                          headers=_h(tok["B"]), timeout=15)
            assert r.status_code == 200, r.text
            cid2 = r.json()["comment"]["comment_id"]
            time.sleep(2.5)

            notifs2 = self._list_ids(sess, "A", tok)
            match = [n for n in notifs2
                     if n.get("type") == "mention"
                     and n.get("actor_id") == B_ID
                     and n.get("comment_id") == cid2]
            assert match, (
                f"After unblock, mention from B on new comment {cid2} did "
                f"NOT appear in A's notifications. Total notifs={len(notifs2)}"
            )

            # Cleanup created comments
            mdb.comments.delete_many({"comment_id": {"$in": [cid, cid2]}})
            mdb.notifications.delete_many({"comment_id": {"$in": [cid, cid2]}})
        finally:
            _unblock_all(sess, tok)
