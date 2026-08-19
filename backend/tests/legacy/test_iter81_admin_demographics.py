"""
Iteration 81 — Populus admin panel fixes:
1. GET /api/admin/stats: anonymous users must NOT contribute to demographic
   aggregates (by_region / by_sex / by_age) even if legacy rows have those
   fields populated. total_users & total_votes must also exclude anonymous.
2. DB integrity: no anonymous users left with region/sex/age populated.
"""
import os
import time
import pytest
import requests
import pymongo

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://feud-governance.preview.emergentagent.com").rstrip("/")
ADMIN_HEADERS = {"X-Admin-Key": "populus-admin-42b8f3"}
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def db():
    client = pymongo.MongoClient(MONGO_URL)
    return client[DB_NAME]


def _run(x):
    # Sync passthrough — kept so we don't have to rewrite every callsite.
    return x


class TestAnonymousDbHygiene:
    """The one-shot script should have removed region/sex/age from all anon users."""

    def test_no_anonymous_users_with_region(self, db):
        cnt = _run(db.users.count_documents({
            "auth_provider": "anonymous",
            "region": {"$exists": True, "$nin": [None, ""]},
        }))
        assert cnt == 0, f"Found {cnt} anonymous users with region populated"

    def test_no_anonymous_users_with_sex(self, db):
        cnt = _run(db.users.count_documents({
            "auth_provider": "anonymous",
            "sex": {"$exists": True, "$nin": [None, ""]},
        }))
        assert cnt == 0, f"Found {cnt} anonymous users with sex populated"

    def test_no_anonymous_users_with_age(self, db):
        cnt = _run(db.users.count_documents({
            "auth_provider": "anonymous",
            "age": {"$exists": True, "$ne": None},
        }))
        assert cnt == 0, f"Found {cnt} anonymous users with age populated"


class TestAdminStatsExcludesAnonymous:
    """Even if we deliberately re-inject demo data on an anonymous user, the
    /api/admin/stats aggregates must ignore it."""

    ANON_UID = None
    ANON_VOTE_IDS: list = []

    def test_stats_endpoint_reachable(self, session):
        r = session.get(f"{BASE_URL}/api/admin/stats", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("total_users", "total_votes", "by_region", "by_sex", "by_age", "top_feuds"):
            assert k in data, f"missing {k} in response"
        assert isinstance(data["by_region"], list)
        assert isinstance(data["by_sex"], dict)
        assert isinstance(data["by_age"], dict)

    def test_anonymous_vote_not_in_demographics(self, session, db):
        # Baseline snapshot (before we do anything)
        before = session.get(f"{BASE_URL}/api/admin/stats", headers=ADMIN_HEADERS).json()

        # Create fresh anonymous user
        nick = f"TEST_anon81_{int(time.time())}"
        r = session.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick})
        assert r.status_code == 200, r.text
        payload = r.json()
        token = payload["token"]
        uid = payload["user"]["user_id"]
        TestAdminStatsExcludesAnonymous.ANON_UID = uid

        # Force-populate demographic fields on the anonymous user (simulating
        # the legacy corruption the one-shot script cleaned up).
        _run(db.users.update_one(
            {"user_id": uid},
            {"$set": {"region": "Liguria", "sex": "F", "age": 42}},
        ))

        # Cast a vote as the anonymous user (get a feud first)
        auth = {"Authorization": f"Bearer {token}"}
        feuds = session.get(f"{BASE_URL}/api/feuds?limit=3", headers=auth)
        assert feuds.status_code == 200
        items = feuds.json()
        items = items if isinstance(items, list) else items.get("feuds") or items.get("items") or []
        if not items:
            pytest.skip("no feuds available to vote on")
        fid = items[0].get("feud_id") or items[0].get("id")
        v = session.post(f"{BASE_URL}/api/feuds/{fid}/vote", headers=auth, json={"side": "A"})
        assert v.status_code in (200, 201), v.text
        TestAdminStatsExcludesAnonymous.ANON_VOTE_IDS.append((uid, fid))

        # Confirm the vote landed in the DB
        vote_docs = list(db.votes.find({"user_id": uid}).limit(50))
        assert vote_docs, "expected at least one vote for the anonymous user"

        # Now hit /api/admin/stats again — the anonymous vote must NOT appear
        after = session.get(f"{BASE_URL}/api/admin/stats", headers=ADMIN_HEADERS).json()

        # by_region must not contain 'Liguria' contribution from this user.
        # We check by comparing counts before/after.
        def region_count(agg, key):
            return next((x["count"] for x in agg.get("by_region", []) if x["region"] == key), 0)

        assert region_count(after, "Liguria") == region_count(before, "Liguria"), (
            f"Liguria count changed after anonymous vote: before={region_count(before,'Liguria')} "
            f"after={region_count(after,'Liguria')}"
        )
        # by_sex.F must not increment
        assert after["by_sex"].get("F", 0) == before["by_sex"].get("F", 0)
        # by_age 35-44 bucket must not increment (age=42)
        assert after["by_age"].get("35-44", 0) == before["by_age"].get("35-44", 0)

        # total_users and total_votes must not include the anonymous user/vote
        # (total_users is baseline-scoped so may still be same). We assert the
        # user is NOT counted:
        # Direct DB check: total_users returned matches count of registered users only
        registered_cnt = _run(db.users.count_documents({
            "auth_provider": {"$ne": "anonymous"},
            "is_anonymous": {"$ne": True},
        }))
        # After a baseline reset, total_users is a subset — must be <= registered_cnt
        assert after["total_users"] <= registered_cnt, (
            f"total_users={after['total_users']} > registered_cnt={registered_cnt}"
        )

    def test_cleanup_anonymous_test_user(self, db):
        uid = TestAdminStatsExcludesAnonymous.ANON_UID
        if not uid:
            return
        _run(db.votes.delete_many({"user_id": uid}))
        _run(db.users.delete_one({"user_id": uid}))
        left = _run(db.users.count_documents({"user_id": uid}))
        assert left == 0
