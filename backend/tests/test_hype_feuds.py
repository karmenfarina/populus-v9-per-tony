"""Tests for GET /api/feuds/hype (Populus HYPE rail).

Covers the iteration-42 fix:
- score = votes_a + votes_b + 2*comments + replies (score <= 0 excluded)
- Sort: score DESC, created_at DESC
- 7-day window filter
- Anonymous & authenticated caller behavior (my_vote)
- Live update: inserting a comment on a zero-engagement feud makes it appear
"""
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://vote-ui-polish.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

USER_A_EMAIL = "chat_a@test.it"
USER_A_PASS = "test123"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


@pytest.fixture(scope="module")
def anon_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": USER_A_EMAIL, "password": USER_A_PASS}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"auth login failed ({r.status_code}): {r.text[:200]}")
    token = r.json().get("token")
    assert token, "no token in login response"
    s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def hype_response(anon_session):
    r = anon_session.get(f"{BASE_URL}/api/feuds/hype", timeout=30)
    assert r.status_code == 200, f"/feuds/hype failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert isinstance(body.get("feuds"), list), "feuds must be a list"
    return body


# ---------------------------------------------------------------------------
# Helpers — engagement score computed directly from DB (source of truth)
# ---------------------------------------------------------------------------
def _db_engagement(db, feud_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """Returns {feud_id: {'votes': X, 'comments': Y, 'replies': Z, 'score': S}}"""
    out: Dict[str, Dict[str, int]] = {}
    feuds = list(db.feuds.find({"feud_id": {"$in": feud_ids}}, {"_id": 0}))
    for f in feuds:
        v = int(f.get("votes_a", 0) or 0) + int(f.get("votes_b", 0) or 0)
        out[f["feud_id"]] = {"votes": v, "comments": 0, "replies": 0, "score": v,
                             "created_at": f.get("created_at")}
    for row in db.comments.aggregate([
        {"$match": {"feud_id": {"$in": feud_ids}}},
        {"$group": {"_id": "$feud_id", "count": {"$sum": 1}}}]):
        if row["_id"] in out:
            out[row["_id"]]["comments"] = int(row["count"])
    for row in db.replies.aggregate([
        {"$lookup": {"from": "comments", "localField": "comment_id",
                     "foreignField": "comment_id", "as": "c"}},
        {"$unwind": "$c"},
        {"$match": {"c.feud_id": {"$in": feud_ids}}},
        {"$group": {"_id": "$c.feud_id", "count": {"$sum": 1}}}]):
        if row["_id"] in out:
            out[row["_id"]]["replies"] = int(row["count"])
    for fid, v in out.items():
        v["score"] = v["votes"] + 2 * v["comments"] + v["replies"]
    return out


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------
class TestHypeContract:
    def test_endpoint_ok(self, hype_response):
        assert hype_response.get("source") == "hype"
        assert hype_response.get("personalized") is False

    def test_response_shape(self, hype_response):
        for f in hype_response["feuds"][:5]:
            assert "feud_id" in f
            assert "created_at" in f
            assert "my_vote" in f  # explicit key, anon → None


# ---------------------------------------------------------------------------
# Rule 1 — no zero-engagement feud
# ---------------------------------------------------------------------------
class TestNoZeroEngagement:
    def test_no_zero_engagement_feud_returned(self, db, hype_response):
        feuds = hype_response["feuds"]
        assert feuds, "HYPE is empty — cannot validate zero-engagement rule"
        ids = [f["feud_id"] for f in feuds]
        engagement = _db_engagement(db, ids)
        offenders = [fid for fid in ids if engagement.get(fid, {}).get("score", 0) <= 0]
        assert not offenders, f"Zero-engagement feuds leaked into HYPE: {offenders[:5]}"


# ---------------------------------------------------------------------------
# Rule 2 — completeness (every voted feud in 7d window must appear)
# ---------------------------------------------------------------------------
class TestCompleteness:
    def test_all_voted_feuds_in_7d_are_present(self, db, hype_response):
        since = datetime.now(timezone.utc) - timedelta(days=7)
        # created_at may be stored as datetime (usual case) - handle both.
        voted = list(db.feuds.find(
            {"created_at": {"$gte": since},
             "$expr": {"$gt": [{"$add": [
                 {"$ifNull": ["$votes_a", 0]},
                 {"$ifNull": ["$votes_b", 0]}]}, 0]}},
            {"_id": 0, "feud_id": 1, "votes_a": 1, "votes_b": 1}))
        voted_ids = {f["feud_id"] for f in voted}
        returned_ids = {f["feud_id"] for f in hype_response["feuds"]}

        if len(voted_ids) >= 80:
            pytest.skip(f"{len(voted_ids)} voted feuds >= 80 cap; completeness not enforced")

        missing = voted_ids - returned_ids
        assert not missing, (f"{len(missing)} voted feud(s) missing from HYPE: "
                             f"sample={list(missing)[:5]} "
                             f"voted_total={len(voted_ids)} returned={len(returned_ids)}")


# ---------------------------------------------------------------------------
# Rule 3 — ordering monotonic on score, ties broken by created_at DESC
# ---------------------------------------------------------------------------
class TestOrdering:
    def test_ordering_by_score_then_created_at(self, db, hype_response):
        feuds = hype_response["feuds"]
        if len(feuds) < 2:
            pytest.skip("need at least 2 feuds to test ordering")
        ids = [f["feud_id"] for f in feuds]
        eng = _db_engagement(db, ids)

        # Build (score, created_at) tuples in the order the API returned them.
        seq = []
        for f in feuds:
            e = eng.get(f["feud_id"], {})
            ca = e.get("created_at")
            if isinstance(ca, datetime):
                ca_key = ca
            else:
                ca_key = datetime.min.replace(tzinfo=timezone.utc)
            seq.append((f["feud_id"], e.get("score", 0), ca_key))

        # Monotonic non-increasing on score
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            assert a[1] >= b[1], (
                f"score order broken at idx {i}: {a[0]}(s={a[1]}) then {b[0]}(s={b[1]})")
            if a[1] == b[1]:
                # tie ⇒ a.created_at must be >= b.created_at (newer first).
                # Compare with defensive normalization.
                if a[2].tzinfo is None:
                    a_ts = a[2].replace(tzinfo=timezone.utc)
                else:
                    a_ts = a[2]
                if b[2].tzinfo is None:
                    b_ts = b[2].replace(tzinfo=timezone.utc)
                else:
                    b_ts = b[2]
                assert a_ts >= b_ts, (
                    f"tie-break broken at idx {i}: {a[0]}@{a_ts} then {b[0]}@{b_ts}")


# ---------------------------------------------------------------------------
# Rule 4 — feuds with 0 votes but >=1 comment ARE included
# ---------------------------------------------------------------------------
class TestCommentEngagementCounts:
    def test_comment_only_feud_is_included(self, db, hype_response):
        since = datetime.now(timezone.utc) - timedelta(days=7)
        returned_ids = {f["feud_id"] for f in hype_response["feuds"]}

        # Look for any 7-day feud with votes==0 but comments>=1.
        candidates = list(db.feuds.find(
            {"created_at": {"$gte": since},
             "$expr": {"$eq": [{"$add": [
                 {"$ifNull": ["$votes_a", 0]},
                 {"$ifNull": ["$votes_b", 0]}]}, 0]}},
            {"_id": 0, "feud_id": 1}))
        cand_ids = [c["feud_id"] for c in candidates]
        if not cand_ids:
            pytest.skip("no zero-vote feuds in 7d window to test")
        eng = _db_engagement(db, cand_ids)
        comment_only = [fid for fid, v in eng.items() if v["comments"] > 0 or v["replies"] > 0]
        if not comment_only:
            pytest.skip("no comment-only feuds available to test")

        # If we didn't hit the 80-cap for HIGHER-scored items, they must appear.
        # Just assert at least one comment-only feud is in the returned list.
        overlap = set(comment_only) & returned_ids
        assert overlap, (f"comment-only feuds missing from HYPE. "
                         f"candidates={comment_only[:5]}")


# ---------------------------------------------------------------------------
# Rule 5 — nothing outside the 7-day window
# ---------------------------------------------------------------------------
class TestWindow:
    def test_no_feud_older_than_7d(self, db, hype_response):
        ids = [f["feud_id"] for f in hype_response["feuds"]]
        if not ids:
            pytest.skip("empty response")
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        docs = list(db.feuds.find({"feud_id": {"$in": ids}},
                                  {"_id": 0, "feud_id": 1, "created_at": 1}))
        stale = []
        for d in docs:
            ca = d.get("created_at")
            if isinstance(ca, datetime):
                if ca.tzinfo is None:
                    ca = ca.replace(tzinfo=timezone.utc)
                if ca < cutoff:
                    stale.append(d["feud_id"])
        assert not stale, f"feuds older than 7d present in HYPE: {stale[:5]}"


# ---------------------------------------------------------------------------
# Rule 6/7 — anonymous & authenticated user behavior
# ---------------------------------------------------------------------------
class TestAnonymousVsAuth:
    def test_anonymous_returns_null_my_vote(self, hype_response):
        for f in hype_response["feuds"]:
            assert f.get("my_vote") is None, (
                f"anonymous caller must get my_vote=None, got {f.get('my_vote')} on {f['feud_id']}")

    def test_authenticated_call_ok(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/feuds/hype", timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body.get("feuds"), list)
        # Should still be non-empty (same underlying data), my_vote may be non-null
        # for any feuds this user has voted on.
        # We don't require any vote to be present — just that the field exists.
        for f in body["feuds"]:
            assert "my_vote" in f


# ---------------------------------------------------------------------------
# Rule 8 — live update: inserting a comment on a zero-engagement feud
# makes it appear on the next call.
# ---------------------------------------------------------------------------
class TestLiveUpdate:
    def test_inserting_comment_promotes_feud(self, db, anon_session):
        since = datetime.now(timezone.utc) - timedelta(days=7)
        # Find a zero-engagement feud in the 7-day window
        zero_feuds = list(db.feuds.find(
            {"created_at": {"$gte": since},
             "$expr": {"$eq": [{"$add": [
                 {"$ifNull": ["$votes_a", 0]},
                 {"$ifNull": ["$votes_b", 0]}]}, 0]}},
            {"_id": 0, "feud_id": 1}).limit(50))
        target_id = None
        for f in zero_feuds:
            # confirm no comments and no replies
            if db.comments.count_documents({"feud_id": f["feud_id"]}) == 0:
                # Also confirm it is not currently in HYPE
                target_id = f["feud_id"]
                break
        if not target_id:
            pytest.skip("no zero-engagement feud available for live-update test")

        r0 = anon_session.get(f"{BASE_URL}/api/feuds/hype", timeout=30)
        assert r0.status_code == 200
        before_ids = {f["feud_id"] for f in r0.json()["feuds"]}
        # It might already be in HYPE if the 7d cap is loose — assert absence
        # only if truly zero-engagement.
        assert target_id not in before_ids, (
            f"pre-condition: {target_id} unexpectedly already in HYPE")

        # Insert one synthetic comment directly. Use a marker for cleanup.
        marker = f"TEST_HYPE_LIVE_{int(time.time())}"
        cid = f"cmt_{marker}"
        db.comments.insert_one({
            "comment_id": cid,
            "feud_id": target_id,
            "user_id": "chat_a_uid",
            "text": marker,
            "side": "A",
            "created_at": datetime.now(timezone.utc),
        })
        try:
            time.sleep(1)
            r1 = anon_session.get(f"{BASE_URL}/api/feuds/hype", timeout=30)
            assert r1.status_code == 200
            after_ids = {f["feud_id"] for f in r1.json()["feuds"]}
            assert target_id in after_ids, (
                f"feud {target_id} not surfaced after inserting comment. "
                f"HYPE size before={len(before_ids)} after={len(after_ids)}")
        finally:
            db.comments.delete_one({"comment_id": cid})
