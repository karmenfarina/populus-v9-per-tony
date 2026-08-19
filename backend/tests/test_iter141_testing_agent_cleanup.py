"""
iter141 — Verify on-startup `testing_agent` cleanup block in server.py.

The block (server.py ~5385-5431) deletes ephemeral users whose:
  - nickname matches ^(test_[ab]_[a-f0-9]+|test_agent_\\d+|test_fresh_\\d+|
    test_regr_\\d+|test_relog_\\d+|test_user_\\d+|test_signup|
    testrl[a-f0-9]+\\d+)$  (case-insensitive), OR
  - email ends with @example.com (case-insensitive)

…and MUST NOT touch real users, admin (carlofarinapayme@gmail.com) or bots
(is_bot: True). Comments/replies/votes/notifications authored by the removed
users must also be cleared.
"""
import os
import time
import subprocess
import pytest
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://bot-burst-fix.preview.emergentagent.com",
).rstrip("/")

# Sentinel identifiers for this test run — unique so we can clean up if needed.
RUN_TAG = "iter141"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


def _now():
    return datetime.now(timezone.utc)


def _restart_backend(wait_sec: float = 10.0):
    """Restart backend via supervisorctl and wait for the cleanup block."""
    subprocess.run(
        ["sudo", "supervisorctl", "restart", "backend"],
        check=True,
        capture_output=True,
    )
    time.sleep(wait_sec)
    # Poll /api until it's alive
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/api/", timeout=5)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(1)


def _seed_test_users(db):
    """Insert a matrix of user rows (matching + non-matching) directly into
    the DB, along with a comment/reply/vote/notification per user so we can
    verify cascade cleanup."""
    fixtures = [
        # Matching by nickname pattern
        {"user_id": f"user_{RUN_TAG}_a_deadbeef", "nickname": "test_a_deadbeef", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_b_cafe01", "nickname": "test_b_cafe01", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_agent42", "nickname": "test_agent_42", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_fresh7", "nickname": "test_fresh_7", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_regr3", "nickname": "test_regr_3", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_relog9", "nickname": "test_relog_9", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_userN", "nickname": "test_user_5", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_signup", "nickname": "test_signup", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_testrl", "nickname": "testrl9ab2f1", "email": None,
         "auth_provider": "anonymous", "should_be_removed": True},

        # Matching by email
        {"user_id": f"user_{RUN_TAG}_examplemail", "nickname": "normalnick_iter141",
         "email": "validuser_iter141@example.com", "auth_provider": "email",
         "should_be_removed": True},

        # Case-insensitivity checks
        {"user_id": f"user_{RUN_TAG}_upcase", "nickname": "TEST_A_ABCDEF",
         "email": None, "auth_provider": "anonymous", "should_be_removed": True},
        {"user_id": f"user_{RUN_TAG}_upmail", "nickname": "someone_iter141",
         "email": "Someone_Iter141@Example.COM", "auth_provider": "email",
         "should_be_removed": True},

        # NON-matching (must survive)
        {"user_id": f"user_{RUN_TAG}_real1", "nickname": "realuser_iter141_alpha",
         "email": "realuser_iter141_alpha@gmail.com", "auth_provider": "email",
         "should_be_removed": False},
        {"user_id": f"user_{RUN_TAG}_real2", "nickname": "mario_rossi_iter141",
         "email": None, "auth_provider": "anonymous", "should_be_removed": False},
        # Nickname CONTAINS test_ but doesn't fully match (regex is anchored)
        {"user_id": f"user_{RUN_TAG}_real3", "nickname": "protest_a_deadbeef",
         "email": None, "auth_provider": "anonymous", "should_be_removed": False},
        # test_ab but not test_a_ or test_b_
        {"user_id": f"user_{RUN_TAG}_real4", "nickname": "test_c_xxxxxx",
         "email": None, "auth_provider": "anonymous", "should_be_removed": False},
    ]

    now = _now()
    for f in fixtures:
        doc = {
            "user_id": f["user_id"],
            "nickname": f["nickname"],
            "email": f["email"],
            "auth_provider": f["auth_provider"],
            "created_at": now,
            "majority_votes": 0, "minority_votes": 0, "total_votes": 0,
            "_iter141_seed": True,
        }
        db.users.replace_one({"user_id": f["user_id"]}, doc, upsert=True)
        # Attach a comment, reply, vote, notification per user
        db.comments.replace_one(
            {"comment_id": f"cmt_{f['user_id']}"},
            {"comment_id": f"cmt_{f['user_id']}", "user_id": f["user_id"],
             "feud_id": "feud_iter141", "text": "seeded",
             "nickname": f["nickname"], "side": "A", "created_at": now,
             "_iter141_seed": True},
            upsert=True,
        )
        db.replies.replace_one(
            {"reply_id": f"rep_{f['user_id']}"},
            {"reply_id": f"rep_{f['user_id']}", "user_id": f["user_id"],
             "comment_id": f"cmt_{f['user_id']}", "text": "seeded",
             "nickname": f["nickname"], "created_at": now,
             "_iter141_seed": True},
            upsert=True,
        )
        db.votes.replace_one(
            {"vote_id": f"vt_{f['user_id']}"},
            {"vote_id": f"vt_{f['user_id']}", "user_id": f["user_id"],
             "feud_id": "feud_iter141", "side": "A", "created_at": now,
             "_iter141_seed": True},
            upsert=True,
        )
        db.notifications.replace_one(
            {"notif_id": f"n_{f['user_id']}"},
            {"notif_id": f"n_{f['user_id']}", "actor_id": f["user_id"],
             "user_id": "user_recipient_iter141", "type": "mention",
             "created_at": now, "_iter141_seed": True},
            upsert=True,
        )
    return fixtures


# ─────────────────────────── Test 1 — API path ─────────────────────────────
def test_1_api_created_test_user_and_comment_get_cleaned(db):
    """Fresh anonymous user via API, then restart backend, user must vanish."""
    nickname = "test_a_deadbeef"
    r = requests.post(
        f"{BASE_URL}/api/auth/anonymous",
        json={"nickname": nickname},
        timeout=15,
    )
    assert r.status_code == 200, f"anonymous auth failed: {r.status_code} {r.text}"
    payload = r.json()
    user_id = payload["user"]["user_id"]
    token = payload["token"]

    # Sanity: user is in DB before restart
    pre = db.users.find_one({"user_id": user_id}, {"_id": 0})
    assert pre is not None, "user should be present pre-restart"
    assert pre["nickname"] == nickname

    # Insert a comment for that user directly (avoids requiring a vote+real feud)
    db.comments.insert_one({
        "comment_id": f"cmt_api_{user_id}",
        "user_id": user_id,
        "feud_id": "feud_iter141_api",
        "text": "seeded via api-path test",
        "nickname": nickname,
        "side": "A",
        "created_at": _now(),
        "_iter141_seed": True,
    })

    _restart_backend()

    # After startup cleanup, user + comment should be gone
    post_user = db.users.find_one({"user_id": user_id})
    post_comment = db.comments.find_one({"comment_id": f"cmt_api_{user_id}"})
    assert post_user is None, f"test user should be removed, got {post_user}"
    assert post_comment is None, "test user's comment should be removed"

    # Token should now be useless (user gone)
    r_me = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert r_me.status_code in (401, 404), (
        f"stale token should be rejected, got {r_me.status_code} {r_me.text}"
    )


# ────────────────────── Test 2/3/4 — Batch matrix path ─────────────────────
def test_2_batch_matrix_and_admin_bots_preserved(db):
    """Seed matrix of users, restart backend, verify:
       - all should_be_removed=True users are gone (with their comments/replies/
         votes/notifications), including case-insensitive & @example.com
       - all should_be_removed=False users survive
       - admin (carlofarinapayme@gmail.com) survives
       - bots (is_bot: True) survive
    """
    fixtures = _seed_test_users(db)

    admin_pre = db.users.find_one({"email": "carlofarinapayme@gmail.com"}, {"_id": 0})
    assert admin_pre is not None, "admin must exist before test"
    bot_count_pre = db.users.count_documents({"is_bot": True})
    assert bot_count_pre > 0, "there must be bots seeded before restart"

    _restart_backend()

    removed_ids = [f["user_id"] for f in fixtures if f["should_be_removed"]]
    kept_ids = [f["user_id"] for f in fixtures if not f["should_be_removed"]]

    # Users removed
    for uid in removed_ids:
        u = db.users.find_one({"user_id": uid})
        assert u is None, f"user {uid} should have been removed by cleanup"

    # Users preserved
    for uid in kept_ids:
        u = db.users.find_one({"user_id": uid})
        assert u is not None, f"real user {uid} was wrongly removed"

    # Cascade — comments/replies/votes must be gone for removed users
    for uid in removed_ids:
        assert db.comments.find_one({"user_id": uid}) is None, (
            f"comment by removed user {uid} still present"
        )
        assert db.replies.find_one({"user_id": uid}) is None, (
            f"reply by removed user {uid} still present"
        )
        assert db.votes.find_one({"user_id": uid}) is None, (
            f"vote by removed user {uid} still present"
        )

    # And PRESERVED for kept users
    for uid in kept_ids:
        assert db.comments.find_one({"user_id": uid}) is not None, (
            f"comment for real user {uid} was wrongly removed"
        )
        assert db.replies.find_one({"user_id": uid}) is not None, (
            f"reply for real user {uid} was wrongly removed"
        )
        assert db.votes.find_one({"user_id": uid}) is not None, (
            f"vote for real user {uid} was wrongly removed"
        )

    # Admin & bots preserved
    admin_post = db.users.find_one({"email": "carlofarinapayme@gmail.com"}, {"_id": 0})
    assert admin_post is not None, "admin was wrongly removed by cleanup"
    bot_count_post = db.users.count_documents({"is_bot": True})
    assert bot_count_post == bot_count_pre, (
        f"bot count changed: {bot_count_pre} -> {bot_count_post}"
    )


# ─────────────────────── Test 5 — Log line assertion ────────────────────────
def test_5_cleanup_log_line_present():
    """The cleanup block must emit its info log at startup."""
    # Trigger a fresh restart to guarantee the log line appears in the tail
    # even if tests ran quickly and we missed it.
    # Seed a single test_ user so N>0 and the block enters the log branch.
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    db.users.replace_one(
        {"user_id": "user_iter141_logcheck"},
        {"user_id": "user_iter141_logcheck",
         "nickname": "test_a_logcheck0",
         "email": None,
         "auth_provider": "anonymous",
         "created_at": _now(),
         "_iter141_seed": True},
        upsert=True,
    )
    client.close()

    _restart_backend()

    # Grep both stdout & stderr logs
    result = subprocess.run(
        ["bash", "-lc",
         "tail -n 400 /var/log/supervisor/backend.out.log /var/log/supervisor/backend.err.log "
         "| grep -E 'testing_agent cleanup: removed [0-9]+ users' | tail -n 5"],
        capture_output=True, text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert "testing_agent cleanup: removed" in output, (
        "Expected 'testing_agent cleanup: removed N users, …' log line missing.\n"
        f"tail returned: {output[-500:] if output else '<empty>'}"
    )
