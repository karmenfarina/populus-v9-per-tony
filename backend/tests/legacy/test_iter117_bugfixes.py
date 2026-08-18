"""Iteration 117 backend regression suite for Populus.

Covers three new backend behaviours:

1. GET /api/mentions/suggest — proximity-ranked autocomplete for
   @mentions inside comment/reply boxes.

2. GET /api/feuds/{fid}/comments and /api/comments/{cid}/replies —
   when viewer A has blocked user B, any comment/reply that TAGS B
   (via mentions[] or raw @nick in text) is ENTIRELY hidden from A,
   no matter who authored it. Symmetric for the other direction. The
   parent comment's `reply_count` badge decrements accordingly.

3. GET /api/users/{user_id} — response now includes `history_counts`
   ({all, majority, minority}) so the "STORICO VOTI" badge matches the
   list length (raw `total_votes` can include purged legacy votes with
   no snapshot, which the actual history list silently drops).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

sys.path.insert(0, "/app/backend")
from helpers import hash_password  # noqa: E402

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
B_NICK = "chatuserb"

BLOCK_FEUD = "feud_7c6d16e4baee"

# 3rd party user (created fresh each module run) — used to author
# comments that @-tag a blocked user from a NEUTRAL account.
C_EMAIL = f"iter117_c_{uuid.uuid4().hex[:8]}@test.it"
C_PASS = "testC123"
C_NICK = f"iter117c{uuid.uuid4().hex[:6]}"


# ─────────────────────────── fixtures ───────────────────────────


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
def mdb():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


@pytest.fixture(scope="module")
def user_c(mdb):
    """Seed a fresh, email-verified 3rd party account we can log in
    as. Torn down at module exit."""
    uid = f"user_{uuid.uuid4().hex[:12]}"
    mdb.users.insert_one({
        "user_id": uid,
        "email": C_EMAIL,
        "nickname": C_NICK,
        "password_hash": hash_password(C_PASS),
        "auth_provider": "email",
        "email_verified": True,
        "terms_accepted": True,
        "onboarding_completed": True,
        "created_at": datetime.now(timezone.utc),
        "majority_votes": 0, "minority_votes": 0, "total_votes": 0,
    })
    yield {"user_id": uid, "email": C_EMAIL, "password": C_PASS, "nickname": C_NICK}
    # Cleanup
    mdb.users.delete_one({"user_id": uid})
    mdb.comments.delete_many({"user_id": uid})
    mdb.replies.delete_many({"user_id": uid})
    mdb.votes.delete_many({"user_id": uid})
    mdb.user_blocks.delete_many({"$or": [{"blocker_id": uid}, {"blocked_id": uid}]})


@pytest.fixture(scope="module")
def tok(sess, user_c):
    return {
        "A": _login(sess, A_EMAIL, PASS),
        "B": _login(sess, B_EMAIL, PASS),
        "C": _login(sess, C_EMAIL, C_PASS),
    }


def _unblock_all(sess, tok):
    for src, target in (("A", B_ID), ("B", A_ID)):
        sess.delete(f"{API}/users/{target}/block",
                    headers=_h(tok[src]), timeout=10)


@pytest.fixture(scope="module", autouse=True)
def _reset(sess, tok):
    _unblock_all(sess, tok)
    yield
    _unblock_all(sess, tok)


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


# ═══════════════ GROUP A — /api/mentions/suggest ═══════════════


class TestMentionsSuggest:

    def test_empty_q_returns_proximity_list(self, sess, tok):
        """Empty q must NOT error and should return a list (may be
        empty if the account has no proximity signals)."""
        r = sess.get(f"{API}/mentions/suggest?q=",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "users" in body and isinstance(body["users"], list)
        # Every entry must have the documented shape
        for u in body["users"]:
            for k in ("user_id", "nickname", "score"):
                assert k in u, f"missing {k} in {u}"
            assert u["user_id"] != A_ID, "self must never appear"

    def test_q_filters_by_substring_case_insensitive(self, sess, tok):
        """q='chat' must ONLY return users whose nickname or
        display_name contains 'chat' (case-insensitive)."""
        r = sess.get(f"{API}/mentions/suggest?q=chat",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        users = r.json()["users"]
        assert len(users) >= 1, "expected at least chatUserB to surface"
        for u in users:
            hay = f"{u.get('nickname','')} {u.get('display_name') or ''}".lower()
            assert "chat" in hay, (
                f"BUG: non-matching user returned for q='chat': {u}"
            )
            assert u["user_id"] != A_ID, "self must never appear"

    def test_q_matches_display_name_only(self, sess, tok, mdb, user_c):
        """A user whose nickname doesn't match but whose display_name
        does must still surface."""
        marker = f"zdisp{uuid.uuid4().hex[:6]}"
        mdb.users.update_one({"user_id": user_c["user_id"]},
                             {"$set": {"display_name": f"Mario {marker} Rossi"}})
        try:
            r = sess.get(f"{API}/mentions/suggest?q={marker}",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200, r.text
            uids = [u["user_id"] for u in r.json()["users"]]
            assert user_c["user_id"] in uids, (
                f"BUG: display_name-only substring match not returned; "
                f"got {uids}"
            )
        finally:
            mdb.users.update_one({"user_id": user_c["user_id"]},
                                 {"$unset": {"display_name": ""}})

    def test_blocked_user_never_appears(self, sess, tok):
        """After A blocks B, B must NEVER appear in A's suggestions
        even when q matches B's nickname directly."""
        try:
            rb = sess.post(f"{API}/users/{B_ID}/block",
                           headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200
            r = sess.get(f"{API}/mentions/suggest?q=chatuser",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            uids = [u["user_id"] for u in r.json()["users"]]
            assert B_ID not in uids, (
                f"BUG: blocked user surfaced in suggestions; got {uids}"
            )
            # Symmetric: B must not see A either.
            r = sess.get(f"{API}/mentions/suggest?q=chat_a",
                         headers=_h(tok["B"]), timeout=15)
            uids = [u["user_id"] for u in r.json()["users"]]
            assert A_ID not in uids, (
                f"BUG: reverse block did not scrub A; got {uids}"
            )
        finally:
            _unblock_all(sess, tok)

    def test_anonymous_viewer_returns_empty(self, sess):
        """Anonymous accounts must get an empty list (no error)."""
        # Create a throwaway anon session
        r = sess.post(f"{API}/auth/anonymous",
                      json={"nickname": f"anon{uuid.uuid4().hex[:6]}"},
                      timeout=15)
        assert r.status_code == 200, r.text
        anon_tok = r.json()["token"]
        r = sess.get(f"{API}/mentions/suggest?q=chat",
                     headers=_h(anon_tok), timeout=15)
        assert r.status_code == 200
        assert r.json() == {"users": []}, (
            f"BUG: anon viewer must get empty users list; got {r.json()}"
        )

    def test_no_match_returns_empty_no_error(self, sess, tok):
        """q with impossible substring returns empty array."""
        r = sess.get(f"{API}/mentions/suggest?q=zzzz_no_such_user_{uuid.uuid4().hex}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == {"users": []}

    def test_feud_context_boost(self, sess, tok, mdb, user_c):
        """A commenter on the passed feud must score HIGHER than the
        same user WITHOUT the feud_id — proves the +1.0 same-feud
        boost is applied."""
        # Seed a fresh feud where user_c comments.
        fid = f"feud_iter117_{uuid.uuid4().hex[:10]}"
        mdb.feuds.insert_one({
            "feud_id": fid, "title": f"iter117 ctx {fid}",
            "side_a_label": "A", "side_b_label": "B",
            "category": "curiosita", "context_text": "iter117 ctx seed",
            "created_at": datetime.now(timezone.utc),
            "votes_a": 0, "votes_b": 0, "public": True, "kind": "test",
        })
        try:
            _vote(sess, tok, "C", fid, "A")
            r = sess.post(f"{API}/feuds/{fid}/comments",
                          json={"text": f"iter117 ctx {uuid.uuid4().hex[:6]}"},
                          headers=_h(tok["C"]), timeout=15)
            assert r.status_code == 200, r.text

            # Query WITHOUT feud_id — get user_c's score (via substring)
            marker = C_NICK[:6]  # user_c's nickname prefix
            r_nof = sess.get(f"{API}/mentions/suggest?q={marker}",
                             headers=_h(tok["A"]), timeout=15)
            assert r_nof.status_code == 200
            base_users = {u["user_id"]: u["score"] for u in r_nof.json()["users"]}

            # Query WITH feud_id — score should be +1.0
            r_ctx = sess.get(
                f"{API}/mentions/suggest?q={marker}&feud_id={fid}",
                headers=_h(tok["A"]), timeout=15,
            )
            assert r_ctx.status_code == 200
            ctx_users = {u["user_id"]: u["score"] for u in r_ctx.json()["users"]}

            assert user_c["user_id"] in ctx_users, (
                f"BUG: same-feud commenter missing with feud_id; got {ctx_users}"
            )
            base_score = base_users.get(user_c["user_id"], 0.0)
            ctx_score = ctx_users[user_c["user_id"]]
            assert ctx_score >= base_score + 0.9, (
                f"BUG: feud_id boost not applied. base={base_score}, "
                f"ctx={ctx_score}, expected +1.0 delta"
            )
        finally:
            mdb.comments.delete_many({"feud_id": fid})
            mdb.votes.delete_many({"feud_id": fid})
            mdb.feuds.delete_many({"feud_id": fid})

    def test_response_shape(self, sess, tok):
        """Response entries carry all documented fields."""
        r = sess.get(f"{API}/mentions/suggest?q=chatuserb",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        users = r.json()["users"]
        assert users, "expected chatUserB to appear"
        expected_keys = {"user_id", "nickname", "display_name",
                         "primary_photo_id", "photo_data", "score"}
        for u in users:
            missing = expected_keys - set(u.keys())
            assert not missing, f"missing keys {missing} in {u}"


# ═══════════════ GROUP B — Block filter on TAGGED comments/replies ═══════════════


class TestBlockFilterOnTaggedComments:

    def test_third_party_comment_tagging_blocked_user_hidden(
        self, sess, tok, mdb, user_c,
    ):
        """user_c posts a comment '@chatUserB check this'. After A
        blocks B, that comment must NOT appear in A's /comments
        response — even though B is not the author."""
        _unblock_all(sess, tok)
        # C needs to vote so its comment is visible.
        _vote(sess, tok, "C", BLOCK_FEUD, "A")
        _vote(sess, tok, "A", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        r = sess.post(
            f"{API}/feuds/{BLOCK_FEUD}/comments",
            json={"text": f"hey @chatUserB check this iter117-{stamp}"},
            headers=_h(tok["C"]), timeout=15,
        )
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        try:
            # Baseline: A sees C's comment
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid) is not None, (
                "baseline: A must see C's tagging comment BEFORE block"
            )

            # A blocks B
            rb = sess.post(f"{API}/users/{B_ID}/block",
                           headers=_h(tok["A"]), timeout=15)
            assert rb.status_code == 200

            # A must NO LONGER see the comment
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid) is None, (
                "BUG: comment tagging blocked user (@chatUserB) must be "
                "entirely hidden from A after A blocks B"
            )

            # Unblock → comment reappears
            ru = sess.delete(f"{API}/users/{B_ID}/block",
                             headers=_h(tok["A"]), timeout=10)
            assert ru.status_code == 200
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid) is not None, (
                "REGRESSION: after unblock, tagged comment must reappear"
            )
        finally:
            mdb.comments.delete_many({"comment_id": cid})
            _unblock_all(sess, tok)

    def test_symmetric_comment_tagging_hidden_for_target(
        self, sess, tok, mdb, user_c,
    ):
        """Symmetric case: A blocks B → B must NOT see comments that
        tag @chat_a (author=C, tags A)."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "C", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        r = sess.post(
            f"{API}/feuds/{BLOCK_FEUD}/comments",
            json={"text": f"yo @chat_a look iter117-{stamp}"},
            headers=_h(tok["C"]), timeout=15,
        )
        assert r.status_code == 200
        cid = r.json()["comment"]["comment_id"]

        try:
            # Baseline: B sees C's comment tagging A
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert _find_comment(r.json(), cid) is not None

            # A blocks B (bi-directional block)
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)

            # B must not see comments tagging @chat_a anymore
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            assert _find_comment(r.json(), cid) is None, (
                "BUG: symmetric block failed — B still sees a comment "
                "tagging @chat_a authored by a 3rd party"
            )
        finally:
            mdb.comments.delete_many({"comment_id": cid})
            _unblock_all(sess, tok)

    def test_reply_tagging_blocked_user_hidden(
        self, sess, tok, mdb, user_c,
    ):
        """Reply from C tagging @chatUserB must be hidden from A
        after A blocks B."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "C", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        # A posts a parent comment
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter117 parent {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        parent_cid = r.json()["comment"]["comment_id"]

        # C replies tagging @chatUserB
        r = sess.post(f"{API}/comments/{parent_cid}/replies",
                      json={"text": f"@chatUserB nested iter117-{stamp}"},
                      headers=_h(tok["C"]), timeout=15)
        assert r.status_code == 200
        rid = r.json()["reply"]["reply_id"]

        try:
            # Baseline: A sees the reply
            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            assert any(x.get("reply_id") == rid for x in r.json().get("replies") or []), (
                "baseline: A must see the reply before block"
            )

            # A blocks B
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)

            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            assert r.status_code == 200
            reps = r.json().get("replies") or []
            assert not any(x.get("reply_id") == rid for x in reps), (
                "BUG: reply tagging blocked user must be entirely hidden"
            )
        finally:
            mdb.replies.delete_many({"reply_id": rid})
            mdb.comments.delete_many({"comment_id": parent_cid})
            _unblock_all(sess, tok)

    def test_reply_count_matches_visible_after_tag_filter(
        self, sess, tok, mdb, user_c,
    ):
        """When one of the replies tags a blocked user, the parent
        comment's reply_count must decrement to match the visible
        list length."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "C", BLOCK_FEUD, "A")

        stamp = uuid.uuid4().hex[:6]
        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter117 rc-parent {stamp}"},
                      headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        parent_cid = r.json()["comment"]["comment_id"]

        rid_clean = None
        rid_tagged = None
        try:
            # Reply 1: clean
            r = sess.post(f"{API}/comments/{parent_cid}/replies",
                          json={"text": f"clean reply {stamp}"},
                          headers=_h(tok["C"]), timeout=15)
            assert r.status_code == 200
            rid_clean = r.json()["reply"]["reply_id"]
            # Reply 2: tagged
            r = sess.post(f"{API}/comments/{parent_cid}/replies",
                          json={"text": f"@chatUserB tagged {stamp}"},
                          headers=_h(tok["C"]), timeout=15)
            assert r.status_code == 200
            rid_tagged = r.json()["reply"]["reply_id"]

            # Baseline (no block): reply_count should be 2
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            baseline = _find_comment(r.json(), parent_cid)
            assert baseline is not None
            assert baseline.get("reply_count", 0) == 2, (
                f"baseline reply_count expected 2; got {baseline.get('reply_count')}"
            )

            # A blocks B → tagged reply drops, count should be 1
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            after = _find_comment(r.json(), parent_cid)
            assert after is not None, "parent comment must still be visible"
            assert after.get("reply_count", 0) == 1, (
                f"BUG: reply_count must decrement to 1 after tag-filter; "
                f"got {after.get('reply_count')}"
            )

            # And the actual visible replies list must have exactly 1 entry.
            r = sess.get(f"{API}/comments/{parent_cid}/replies",
                         headers=_h(tok["A"]), timeout=15)
            reps = r.json().get("replies") or []
            assert len(reps) == 1, (
                f"BUG: visible replies must be 1 (only the clean one); "
                f"got {len(reps)}"
            )
            assert reps[0]["reply_id"] == rid_clean
        finally:
            if rid_clean:
                mdb.replies.delete_many({"reply_id": rid_clean})
            if rid_tagged:
                mdb.replies.delete_many({"reply_id": rid_tagged})
            mdb.comments.delete_many({"comment_id": parent_cid})
            _unblock_all(sess, tok)


# ═══════════════ GROUP C — /api/users/{user_id} history_counts ═══════════════


class TestHistoryCountsOnPublicProfile:

    def test_history_counts_present_and_typed(self, sess, tok):
        _unblock_all(sess, tok)
        r = sess.get(f"{API}/users/{B_ID}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "history_counts" in body, (
            "BUG: /api/users/{uid} response must include history_counts"
        )
        hc = body["history_counts"]
        for k in ("all", "majority", "minority"):
            assert k in hc, f"BUG: history_counts missing '{k}'"
            assert isinstance(hc[k], int), (
                f"BUG: history_counts['{k}'] must be int; got {type(hc[k])}"
            )

    def test_history_counts_invariant(self, sess, tok):
        """all == majority + minority."""
        r = sess.get(f"{API}/users/{B_ID}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        hc = r.json()["history_counts"]
        assert hc["all"] == hc["majority"] + hc["minority"], (
            f"BUG: invariant broken: all={hc['all']} != "
            f"majority({hc['majority']})+minority({hc['minority']})"
        )

    def test_history_counts_le_total_votes(self, sess, tok):
        """history_counts.all must be <= profile.total_votes
        (list may be shorter due to purged legacy feuds)."""
        r = sess.get(f"{API}/users/{B_ID}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["history_counts"]["all"] <= body.get("total_votes", 0), (
            f"BUG: history_counts.all ({body['history_counts']['all']}) must "
            f"be <= total_votes ({body.get('total_votes')})"
        )

    def test_history_counts_matches_actual_history_length(self, sess, tok):
        """Cross-check: hit /users/{uid}/history and compare its
        length to history_counts.all — they MUST match."""
        r = sess.get(f"{API}/users/{B_ID}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        hc_all = r.json()["history_counts"]["all"]

        r = sess.get(f"{API}/users/{B_ID}/history",
                     headers=_h(tok["A"]), timeout=15)
        # Endpoint may 200 with list or wrap in {items: [...]} — handle both.
        assert r.status_code in (200, 403), r.text
        if r.status_code == 200:
            body = r.json()
            items = body.get("items") if isinstance(body, dict) else body
            if items is None and isinstance(body, dict):
                items = body.get("history") or body.get("votes") or []
            actual_len = len(items) if isinstance(items, list) else None
            if actual_len is not None:
                assert hc_all == actual_len, (
                    f"BUG: history_counts.all ({hc_all}) != actual history "
                    f"length ({actual_len}) — badge/list mismatch"
                )

    def test_self_public_profile_has_history_counts(self, sess, tok):
        """GET /api/users/{my_id} as self must also include
        history_counts."""
        r = sess.get(f"{API}/users/{A_ID}",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200, r.text
        assert "history_counts" in r.json(), (
            "BUG: self public profile must also include history_counts"
        )

    def test_blocked_pair_returns_403_no_leak(self, sess, tok):
        """When A has blocked B, B fetching A's public profile must
        get 403 (or 404) and NEVER leak history_counts."""
        try:
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)
            r = sess.get(f"{API}/users/{A_ID}",
                         headers=_h(tok["B"]), timeout=15)
            # Accept 403/404 (block leakage guarded); if 200, ensure
            # NO history_counts (privacy).
            assert r.status_code in (200, 403, 404), r.text
            if r.status_code == 200:
                body = r.json()
                assert "history_counts" not in body, (
                    "BUG: blocked pair should not leak history_counts"
                )
        finally:
            _unblock_all(sess, tok)


# ═══════════════ GROUP D — Regression smoke (iter116 rules still hold) ═══════════════


class TestRegressionIter116:

    def test_authored_by_blocked_still_hidden(self, sess, tok, mdb):
        """iter116 rule: bi-directional block hides comments authored
        BY the blocked user entirely."""
        _unblock_all(sess, tok)
        _vote(sess, tok, "A", BLOCK_FEUD, "A")
        _vote(sess, tok, "B", BLOCK_FEUD, "A")
        stamp = uuid.uuid4().hex[:6]

        r = sess.post(f"{API}/feuds/{BLOCK_FEUD}/comments",
                      json={"text": f"iter117-regB {stamp}"},
                      headers=_h(tok["B"]), timeout=15)
        assert r.status_code == 200
        cid_b = r.json()["comment"]["comment_id"]
        try:
            sess.post(f"{API}/users/{B_ID}/block",
                      headers=_h(tok["A"]), timeout=15)
            r = sess.get(f"{API}/feuds/{BLOCK_FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            assert _find_comment(r.json(), cid_b) is None, (
                "REGRESSION: iter116 authored-by-blocked filter broken"
            )
        finally:
            mdb.comments.delete_many({"comment_id": cid_b})
            _unblock_all(sess, tok)
