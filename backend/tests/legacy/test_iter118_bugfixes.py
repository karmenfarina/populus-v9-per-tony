"""Iteration 118 backend tests — mention-of-blocked-user REJECTION on POST.

New rule (iter118, extends iter117):
- POST /api/feuds/{fid}/comments and POST /api/comments/{cid}/replies
  MUST return HTTP 400 when the text contains an @mention of a user
  who is in a bi-directional block with the author.
- Error detail must contain both "Non puoi taggare" and the resolved
  nickname (case-preserving, e.g. "@chatUserB").
- All-or-nothing: mixed valid + blocked mention still rejects (no
  partial save).
- No mentions or unresolved mentions: not rejected.
- Regression: iter117 existing-mention scrub still works.

Fixtures reuse the pre-seeded chat_a/chat_b users and feud_7c6d16e4baee.
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
API = f"{BASE_URL}/api"

# Prefer the internal port for speed & to avoid tunnel flakiness.
INTERNAL = "http://localhost:8001/api"

A_EMAIL = "chat_a@test.it"
B_EMAIL = "chat_b@test.it"
PASS = "test123"
A_ID = "user_6e65e19525d5"
B_ID = "user_16f709708760"
A_NICK = "chat_a"
B_NICK_CANONICAL = "chatUserB"  # DB-cased
FEUD = "feud_7c6d16e4baee"


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


def _post_comment(sess, tok, key, text):
    return sess.post(
        f"{INTERNAL}/feuds/{FEUD}/comments",
        json={"text": text},
        headers=_h(tok[key]), timeout=15,
    )


def _post_reply(sess, tok, key, parent_cid, text):
    return sess.post(
        f"{INTERNAL}/comments/{parent_cid}/replies",
        json={"text": text},
        headers=_h(tok[key]), timeout=15,
    )


def _cleanup_comment(cid):
    """Directly delete a comment doc via internal admin key? No — we just
    leave test comments; they're prefixed with iter118. If needed,
    /api/comments/{cid} DELETE exists but not required for the suite."""
    pass


# ═══════════════ A) Rejection on POST ═══════════════


class TestTagBlockedRejection:

    def test_A_tags_B_after_A_blocks_B_returns_400(self, sess, tok):
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            r = _post_comment(sess, tok, "A",
                              f"hello @{B_NICK_CANONICAL} iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 400, (
                f"BUG: A→B tag while A blocks B must 400; got {r.status_code} "
                f"body={r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            assert "Non puoi taggare" in detail, (
                f"BUG: error detail must contain 'Non puoi taggare'; got {detail!r}"
            )
            # Nickname must be present, case-preserving (matches DB casing).
            assert f"@{B_NICK_CANONICAL}" in detail, (
                f"BUG: error detail must contain '@{B_NICK_CANONICAL}'; got {detail!r}"
            )
        finally:
            _unblock_all(sess, tok)

    def test_symmetric_B_tags_A_after_A_blocks_B_returns_400(self, sess, tok):
        """B tries to tag @chat_a while A→B block still exists — the
        block is bi-directional so this must also 400."""
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            r = _post_comment(sess, tok, "B",
                              f"hi @{A_NICK} iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 400, (
                f"BUG: symmetric B→A tag must 400; got {r.status_code} "
                f"body={r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            assert "Non puoi taggare" in detail and f"@{A_NICK}" in detail, (
                f"BUG: symmetric error detail malformed; got {detail!r}"
            )
        finally:
            _unblock_all(sess, tok)

    def test_unblock_then_retry_returns_200_with_mention(self, sess, tok):
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        text = f"hello @{B_NICK_CANONICAL} iter118-{uuid.uuid4().hex[:6]}"
        # Precondition: blocked → 400
        r = _post_comment(sess, tok, "A", text)
        assert r.status_code == 400
        # Now unblock and retry
        _unblock(sess, tok, "A", B_ID)
        r = _post_comment(sess, tok, "A", text)
        assert r.status_code == 200, (
            f"BUG: after unblock, retry must succeed; got {r.status_code} {r.text[:200]}"
        )
        body = r.json()
        cmt = body.get("comment") or {}
        mentions = cmt.get("mentions") or []
        assert any(m.get("user_id") == B_ID for m in mentions), (
            f"BUG: after unblock, mentions must resolve B; got {mentions}"
        )

    def test_reply_endpoint_rejects_blocked_mention(self, sess, tok):
        _unblock_all(sess, tok)
        # Parent comment from A (no mention).
        r = _post_comment(sess, tok, "A", f"iter118 parent {uuid.uuid4().hex[:6]}")
        assert r.status_code == 200, r.text
        parent_cid = r.json()["comment"]["comment_id"]

        _block(sess, tok, "A", B_ID)
        try:
            # A now tries to REPLY tagging @chatUserB → 400.
            r = _post_reply(sess, tok, "A", parent_cid,
                            f"@{B_NICK_CANONICAL} iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 400, (
                f"BUG: reply tagging blocked user must 400; got {r.status_code} {r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            assert "Non puoi taggare" in detail and f"@{B_NICK_CANONICAL}" in detail, (
                f"BUG: reply error detail malformed; got {detail!r}"
            )

            # Unblock → retry must succeed.
            _unblock(sess, tok, "A", B_ID)
            r = _post_reply(sess, tok, "A", parent_cid,
                            f"@{B_NICK_CANONICAL} iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 200, (
                f"BUG: reply retry after unblock must 200; got {r.status_code} {r.text[:200]}"
            )
            mentions = (r.json().get("reply") or {}).get("mentions") or []
            assert any(m.get("user_id") == B_ID for m in mentions), (
                f"BUG: reply after unblock must resolve mention; got {mentions}"
            )
        finally:
            _unblock_all(sess, tok)

    def test_mixed_valid_and_blocked_mentions_all_or_nothing(self, sess, tok):
        """Text mentioning @chatUserB (blocked) AND @eccociragazzi
        (valid) must be REJECTED as a whole — no partial save."""
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            text = f"hi @{B_NICK_CANONICAL} and @eccociragazzi iter118-{uuid.uuid4().hex[:6]}"
            r = _post_comment(sess, tok, "A", text)
            assert r.status_code == 400, (
                f"BUG: mixed valid+blocked must 400 (all-or-nothing); got {r.status_code} {r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            # Only the blocked nick should be surfaced.
            assert f"@{B_NICK_CANONICAL}" in detail, (
                f"BUG: detail must name the blocked user; got {detail!r}"
            )
            assert "Non puoi taggare" in detail
        finally:
            _unblock_all(sess, tok)

    def test_no_mentions_always_ok_regardless_of_block(self, sess, tok):
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            r = _post_comment(sess, tok, "A",
                              f"hello world iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 200, (
                f"BUG: plain text with no mentions must 200 even under block; "
                f"got {r.status_code} {r.text[:200]}"
            )
            mentions = (r.json().get("comment") or {}).get("mentions") or []
            assert mentions == [], f"BUG: mentions should be empty; got {mentions}"
        finally:
            _unblock_all(sess, tok)

    def test_case_insensitive_nickname_match(self, sess, tok):
        """User typed @CHATUSERB (all caps). Backend must still detect
        the block and 400 with the canonical nickname surfaced."""
        _unblock_all(sess, tok)
        _block(sess, tok, "A", B_ID)
        try:
            r = _post_comment(sess, tok, "A",
                              f"yo @CHATUSERB iter118-{uuid.uuid4().hex[:6]}")
            assert r.status_code == 400, (
                f"BUG: case-variant tag must still 400; got {r.status_code} {r.text[:200]}"
            )
            detail = (r.json() or {}).get("detail") or ""
            # Detail must carry the CANONICAL nickname (DB casing), not the
            # user's lowercased/upcased raw input.
            assert f"@{B_NICK_CANONICAL}" in detail, (
                f"BUG: detail must carry canonical nickname; got {detail!r}"
            )
        finally:
            _unblock_all(sess, tok)


# ═══════════════ B) Existing mentions scrub (iter117 regression) ═══════════════


class TestExistingMentionScrub:

    @pytest.mark.xfail(reason="Superseded by iter120: own comment tagging a blocked user is now HIDDEN entirely, not scrubbed.", strict=True)
    def test_pre_block_mention_scrubbed_after_block(self, sess, tok):
        """A posts a comment tagging @chatUserB while NOT blocked. Then
        A blocks B. Fetching /comments as A must render the tagged text
        as '[utente bloccato]' and mentions must not contain B.
        """
        _unblock_all(sess, tok)
        raw_marker = uuid.uuid4().hex[:6]
        text = f"cool @{B_NICK_CANONICAL} iter118-scrub-{raw_marker}"
        r = _post_comment(sess, tok, "A", text)
        assert r.status_code == 200, r.text
        cid = r.json()["comment"]["comment_id"]

        # Baseline (no block): mention resolved.
        r = sess.get(f"{INTERNAL}/feuds/{FEUD}/comments",
                     headers=_h(tok["A"]), timeout=15)
        assert r.status_code == 200
        found = None
        for c in (r.json().get("side_a") or []) + (r.json().get("side_b") or []):
            if c.get("comment_id") == cid:
                found = c
                break
        assert found is not None, "baseline comment not visible"
        assert "[utente bloccato]" not in (found.get("text") or ""), \
            "baseline must NOT be scrubbed"
        assert any(m.get("user_id") == B_ID for m in (found.get("mentions") or [])), \
            "baseline mentions must include B"

        # Now A blocks B — mention must be scrubbed for A.
        _block(sess, tok, "A", B_ID)
        try:
            r = sess.get(f"{INTERNAL}/feuds/{FEUD}/comments",
                         headers=_h(tok["A"]), timeout=15)
            found = None
            for c in (r.json().get("side_a") or []) + (r.json().get("side_b") or []):
                if c.get("comment_id") == cid:
                    found = c
                    break
            # Note: iter117 changed behaviour so 3rd-party mentions of B
            # are hidden entirely, but A's OWN comment mentioning B may
            # either be scrubbed (iter117 spec text) or hidden (iter117
            # actual impl). Accept both, but if visible, MUST be scrubbed.
            if found is None:
                # Comment hidden entirely — this contradicts iter118 spec
                # which says "text MUST read '[utente bloccato]'". Report.
                pytest.fail(
                    "iter118 spec violation: A's own comment tagging blocked B "
                    "was HIDDEN instead of scrubbed to '[utente bloccato]'. "
                    "Current backend (_comment_tags_blocked_user) is dropping "
                    "the entire comment; iter118 spec explicitly requires the "
                    "text to be rewritten. Main agent must relax the filter for "
                    "self-authored comments (option B from iter117 report)."
                )
            txt = found.get("text") or ""
            assert "[utente bloccato]" in txt, (
                f"BUG: text must be scrubbed to include '[utente bloccato]'; got {txt!r}"
            )
            assert f"@{B_NICK_CANONICAL}" not in txt, (
                f"BUG: raw @{B_NICK_CANONICAL} still in text after scrub: {txt!r}"
            )
            mentions = found.get("mentions") or []
            assert not any(m.get("user_id") == B_ID for m in mentions), (
                f"BUG: scrubbed comment mentions must not include B; got {mentions}"
            )
        finally:
            _unblock_all(sess, tok)

    def test_bidirectional_scrub_for_target(self, sess, tok):
        """Same setup but this time B fetches — must also see the scrub
        (bi-directional). Because iter117 may hide the comment entirely
        from the blocked user, accept either 'hidden' OR 'scrubbed'."""
        _unblock_all(sess, tok)
        text = f"cool @{B_NICK_CANONICAL} iter118-bidir-{uuid.uuid4().hex[:6]}"
        r = _post_comment(sess, tok, "A", text)
        assert r.status_code == 200
        cid = r.json()["comment"]["comment_id"]

        _block(sess, tok, "A", B_ID)
        try:
            r = sess.get(f"{INTERNAL}/feuds/{FEUD}/comments",
                         headers=_h(tok["B"]), timeout=15)
            found = None
            for c in (r.json().get("side_a") or []) + (r.json().get("side_b") or []):
                if c.get("comment_id") == cid:
                    found = c
                    break
            if found is None:
                # Iter116/117: entire comment hidden from blocked B — this is
                # also acceptable (strictly stronger than scrubbing). Log and
                # proceed.
                return
            txt = found.get("text") or ""
            assert "[utente bloccato]" in txt, (
                f"BUG: B-view text must be scrubbed; got {txt!r}"
            )
            mentions = found.get("mentions") or []
            assert not any(m.get("user_id") == B_ID for m in mentions)
        finally:
            _unblock_all(sess, tok)

    def test_unblock_restores_mention(self, sess, tok):
        _unblock_all(sess, tok)
        text = f"cool @{B_NICK_CANONICAL} iter118-restore-{uuid.uuid4().hex[:6]}"
        r = _post_comment(sess, tok, "A", text)
        assert r.status_code == 200
        cid = r.json()["comment"]["comment_id"]

        _block(sess, tok, "A", B_ID)
        _unblock(sess, tok, "A", B_ID)

        r = sess.get(f"{INTERNAL}/feuds/{FEUD}/comments",
                     headers=_h(tok["A"]), timeout=15)
        found = None
        for c in (r.json().get("side_a") or []) + (r.json().get("side_b") or []):
            if c.get("comment_id") == cid:
                found = c
                break
        assert found is not None, "comment must be visible after unblock"
        assert "[utente bloccato]" not in (found.get("text") or ""), (
            f"BUG: after unblock, scrub must be undone; got {found.get('text')!r}"
        )
        assert any(m.get("user_id") == B_ID for m in (found.get("mentions") or [])), (
            f"BUG: after unblock, mentions must resolve B; got {found.get('mentions')}"
        )
