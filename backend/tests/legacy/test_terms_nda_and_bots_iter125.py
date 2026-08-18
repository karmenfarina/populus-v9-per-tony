"""
Iteration 125 — Testing:
  1) BUG FIX: 100 bots must all have UNIQUE surnames + display_names.
  2) FEATURE: NDA endpoint + combined ToS/NDA acceptance.

Uses the public preview URL (EXPO_PUBLIC_BACKEND_URL) so tests exercise the
same ingress the mobile app uses.
"""
import os
import uuid
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# Load env files so credentials are available under both pytest and CLI runs.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "").rstrip("/")
ADMIN_KEY = "populus-admin-42b8f3"
MONGO_URL = (os.environ.get("MONGO_URL") or "").strip('"').strip("'")
DB_NAME = (os.environ.get("DB_NAME") or "").strip('"').strip("'")

assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set for tests"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    return c[DB_NAME]


# ─── Bots: 100 unique surnames ───────────────────────────────────
class TestBotSurnamesUnique:
    def test_admin_bots_state_reports_100(self, api):
        r = api.get(
            f"{BASE_URL}/api/admin/bots/state",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert int(data.get("total_bots", 0)) == 100, data

    def test_bot_surnames_all_unique(self, mongo):
        bots = list(mongo.users.find({"is_bot": True}, {"_id": 0, "display_name": 1, "last_name": 1}))
        assert len(bots) == 100, f"Expected 100 bots, got {len(bots)}"
        display_names = [b.get("display_name") for b in bots]
        # Surname = last space-delimited word if last_name absent
        surnames = [
            (b.get("last_name") or (b.get("display_name") or "").rsplit(" ", 1)[-1]).strip()
            for b in bots
        ]
        assert len(set(display_names)) == 100, (
            f"Non-unique display_names: {len(set(display_names))} unique of 100"
        )
        assert len(set(surnames)) == 100, (
            f"Non-unique surnames: {len(set(surnames))} unique of 100. "
            f"Duplicates: {sorted({s for s in surnames if surnames.count(s) > 1})}"
        )


# ─── Legal endpoints ─────────────────────────────────────────────
class TestLegalEndpoints:
    def test_terms_endpoint(self, api):
        r = api.get(f"{BASE_URL}/api/legal/terms")
        assert r.status_code == 200
        d = r.json()
        assert d.get("version") == "v1"
        assert isinstance(d.get("text"), str) and len(d["text"]) > 0

    def test_nda_endpoint(self, api):
        r = api.get(f"{BASE_URL}/api/legal/nda")
        assert r.status_code == 200
        d = r.json()
        assert d.get("version") == "v1"
        assert isinstance(d.get("text"), str) and len(d["text"]) > 0
        # updated_at optional but described in spec
        # (don't fail if server does not surface it — just log)


# ─── Combined accept-terms ───────────────────────────────────────
@pytest.fixture(scope="module")
def fresh_user_token(api, mongo):
    """Create a fresh verified user directly in DB, return JWT via login helper."""
    # Simplest: use anonymous signup — accept-terms works for any authed user.
    nick = "t_" + uuid.uuid4().hex[:8]
    r = api.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    uid = r.json()["user"]["user_id"]
    yield tok, uid
    # Cleanup
    try:
        mongo.users.delete_one({"user_id": uid})
    except Exception:
        pass


class TestAcceptTerms:
    def _reset(self, mongo, uid):
        mongo.users.update_one(
            {"user_id": uid},
            {"$unset": {
                "terms_accepted_version": "",
                "terms_accepted_at": "",
                "nda_accepted_version": "",
                "nda_accepted_at": "",
            }},
        )

    def test_me_initial_state(self, api, fresh_user_token, mongo):
        tok, uid = fresh_user_token
        self._reset(mongo, uid)
        r = api.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        u = r.json()["user"]
        assert u.get("terms_accepted") is False
        assert not u.get("terms_accepted_version")
        assert not u.get("nda_accepted_version")

    def test_empty_body_rejected(self, api, fresh_user_token):
        tok, _ = fresh_user_token
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 400, r.text

    def test_bad_terms_version_rejected(self, api, fresh_user_token):
        tok, _ = fresh_user_token
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "wrong"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 400

    def test_bad_nda_version_rejected(self, api, fresh_user_token):
        tok, _ = fresh_user_token
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"nda_version": "wrong"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 400

    def test_only_tos_accepted_leaves_flag_false(self, api, fresh_user_token, mongo):
        tok, uid = fresh_user_token
        self._reset(mongo, uid)
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "v1"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("terms_accepted") is False
        assert d.get("terms_accepted_version") == "v1"
        assert not d.get("nda_accepted_version")

    def test_only_nda_accepted_leaves_flag_false(self, api, fresh_user_token, mongo):
        tok, uid = fresh_user_token
        self._reset(mongo, uid)
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"nda_version": "v1"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("terms_accepted") is False
        assert d.get("nda_accepted_version") == "v1"
        assert not d.get("terms_accepted_version")

    def test_both_accepted_sets_flag_true_and_me_reflects(self, api, fresh_user_token, mongo):
        tok, uid = fresh_user_token
        self._reset(mongo, uid)
        r = api.post(
            f"{BASE_URL}/api/users/me/accept-terms",
            json={"version": "v1", "nda_version": "v1"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("terms_accepted") is True
        assert d.get("terms_accepted_version") == "v1"
        assert d.get("nda_accepted_version") == "v1"
        assert d.get("terms_accepted_at")
        assert d.get("nda_accepted_at")

        # /auth/me must mirror it
        r2 = api.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r2.status_code == 200
        u = r2.json()["user"]
        assert u.get("terms_accepted") is True
        assert u.get("terms_accepted_version") == "v1"
        assert u.get("nda_accepted_version") == "v1"
