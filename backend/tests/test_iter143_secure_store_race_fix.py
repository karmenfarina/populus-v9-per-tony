"""
iter143 — Backend verification for the SecureStore race-condition fix in
/app/frontend/src/api.ts.

The fix relies on backend behaviour that this suite pins down:
  1. Auth endpoints (login / signup / anonymous) must keep working and be
     callable BOTH with and without an Authorization header.
  2. Feed / archive / hashtag / categories endpoints must respond WITHOUT
     an Authorization header (either optional-auth 200 or a real 401).
  3. Comment/reply delete continues to work for the owner.
"""
import os
import uuid
import time
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


def _rand(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _unwrap_list(body, key):
    """Backend returns either a bare list or `{key: [...]}`."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get(key), list):
        return body[key]
    return None


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module")
def anon_user(s):
    """Anonymous account = usable token without email verification."""
    r = s.post(f"{API}/auth/anonymous", json={"nickname": _rand("race")})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("token"), f"anonymous login must return a token: {body}"
    return {"token": body["token"], "user": body.get("user", {})}


# ═══════════ 1. Public endpoints — no header required ═══════════
class TestPublicEndpointsWithoutToken:
    def test_categories_no_auth(self, s):
        r = s.get(f"{API}/categories")
        assert r.status_code == 200
        cats = _unwrap_list(r.json(), "categories")
        assert cats and len(cats) > 0

    def test_professions_no_auth(self, s):
        r = s.get(f"{API}/professions")
        assert r.status_code == 200
        pros = _unwrap_list(r.json(), "professions")
        assert pros and len(pros) > 0

    def test_legal_terms_no_auth(self, s):
        r = s.get(f"{API}/legal/terms")
        assert r.status_code == 200
        body = r.json()
        assert any(k in body for k in ("version", "text", "body", "html"))

    def test_legal_nda_no_auth(self, s):
        r = s.get(f"{API}/legal/nda")
        assert r.status_code == 200


# ═══════════ 2. Auth flows: login / signup / anonymous ═══════════
class TestAuthFlows:
    def test_signup_new_user(self, s):
        """Signup accepted — either returns a token OR requires email
        verification. Both are valid backend behaviours; the frontend
        handles the branching. What must NOT happen is a 4xx/5xx."""
        email = f"TEST_{_rand('sig')}@populus-it.co"
        r = s.post(f"{API}/auth/signup", json={
            "email": email, "password": "P@ssw0rd_test", "nickname": _rand("sig"),
        })
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body.get("token") or body.get("requires_verification") is True

    def test_login_missing_email_400(self, s):
        r = s.post(f"{API}/auth/login", json={"password": "x"})
        assert r.status_code in (400, 401, 422)

    def test_login_wrong_password_401(self, s):
        r = s.post(f"{API}/auth/login", json={
            "email": "nope@example.com", "password": "wrong-password",
        })
        assert r.status_code in (400, 401, 403, 404), r.text

    def test_anonymous_login(self, s):
        r = s.post(f"{API}/auth/anonymous", json={"nickname": _rand("anon")})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body.get("token")

    def test_anonymous_can_login_twice(self, s):
        """Simulates: user logged out (_isLoggedOut=true in frontend),
        then taps 'continua senza account' again."""
        r1 = s.post(f"{API}/auth/anonymous", json={"nickname": _rand("relog")})
        r2 = s.post(f"{API}/auth/anonymous", json={"nickname": _rand("relog")})
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)
        assert r1.json()["token"] != r2.json()["token"]


# ═══════════ 3. Feed / archive / hashtag — no token ═══════════
class TestFeedEndpointsBehaviourWithoutToken:
    """The frontend fix drops the client-side 401 short-circuit on null
    token. Backend must not blow up (5xx) when called without an
    Authorization header — 2xx (optional-auth) or 401 is fine."""

    def test_feuds_no_auth(self, s):
        r = s.get(f"{API}/feuds")
        assert r.status_code in (200, 401), f"unexpected {r.status_code}: {r.text[:200]}"
        if r.status_code == 200:
            feuds = _unwrap_list(r.json(), "feuds")
            assert feuds is not None, "feuds response must be list or {feuds:[]}"

    def test_feuds_hype_no_auth(self, s):
        r = s.get(f"{API}/feuds/hype")
        assert r.status_code in (200, 401)

    def test_archive_dates_no_auth(self, s):
        r = s.get(f"{API}/feuds/archive/dates")
        assert r.status_code in (200, 401)

    def test_archive_feuds_no_auth(self, s):
        r_dates = s.get(f"{API}/feuds/archive/dates")
        date = None
        if r_dates.status_code == 200:
            dates = _unwrap_list(r_dates.json(), "dates") or []
            if dates:
                first = dates[0]
                date = first if isinstance(first, str) else first.get("date")
        if not date:
            pytest.skip("no archived dates available")
        r = s.get(f"{API}/feuds/archive", params={"date": date})
        assert r.status_code in (200, 401), r.text

    def test_hashtag_no_auth(self, s):
        r = s.get(f"{API}/hashtags/politica")
        assert r.status_code in (200, 401, 404)


# ═══════════ 4. Feed / archive / hashtag — WITH valid token ═══════════
class TestFeedEndpointsAuthenticated:
    def _hdr(self, u):
        return {"Authorization": f"Bearer {u['token']}"}

    def test_feuds_with_token(self, s, anon_user):
        r = s.get(f"{API}/feuds", headers=self._hdr(anon_user))
        assert r.status_code == 200, r.text
        feuds = _unwrap_list(r.json(), "feuds")
        assert feuds is not None

    def test_archive_dates_with_token(self, s, anon_user):
        r = s.get(f"{API}/feuds/archive/dates", headers=self._hdr(anon_user))
        assert r.status_code == 200, r.text

    def test_categories_with_token(self, s, anon_user):
        r = s.get(f"{API}/categories", headers=self._hdr(anon_user))
        assert r.status_code == 200
        cats = _unwrap_list(r.json(), "categories")
        assert cats and len(cats) > 0


# ═══════════ 5. /auth/me protection ═══════════
class TestProtectedEndpoint:
    def test_me_requires_token(self, s):
        r = s.get(f"{API}/auth/me")
        assert r.status_code in (401, 403), r.text

    def test_me_with_token(self, s, anon_user):
        r = s.get(f"{API}/auth/me",
                  headers={"Authorization": f"Bearer {anon_user['token']}"})
        assert r.status_code == 200
        body = r.json()
        user = body.get("user", body)  # accept both wrapped and bare shapes
        assert user.get("nickname")

    def test_me_with_invalid_token_rejected(self, s):
        r = s.get(f"{API}/auth/me",
                  headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code in (401, 403)


# ═══════════ 6. Comment / reply DELETE regression ═══════════
class TestCommentReplyDeleteRegression:
    def test_owner_can_delete_own_comment(self, s, anon_user):
        hdr = {"Authorization": f"Bearer {anon_user['token']}"}
        r_feuds = s.get(f"{API}/feuds", headers=hdr)
        feuds = _unwrap_list(r_feuds.json(), "feuds") or []
        if not feuds:
            pytest.skip("no feuds available")
        feud_id = feuds[0]["feud_id"]
        s.post(f"{API}/feuds/{feud_id}/vote", headers=hdr, json={"side": "A"})
        r_add = s.post(f"{API}/feuds/{feud_id}/comments",
                       headers=hdr, json={"text": f"TEST_race_{uuid.uuid4().hex[:6]}"})
        if r_add.status_code != 200:
            pytest.skip(f"cannot create comment: {r_add.status_code} {r_add.text[:200]}")
        add_body = r_add.json()
        cid = (add_body.get("comment") or add_body).get("comment_id")
        assert cid, f"missing comment_id in {add_body}"
        r_del = s.delete(f"{API}/comments/{cid}", headers=hdr)
        assert r_del.status_code == 200, r_del.text
        body = r_del.json()
        assert body.get("moderated") is False

    def test_owner_can_delete_own_reply(self, s, anon_user):
        hdr = {"Authorization": f"Bearer {anon_user['token']}"}
        r_feuds = s.get(f"{API}/feuds", headers=hdr)
        feuds = _unwrap_list(r_feuds.json(), "feuds") or []
        if not feuds:
            pytest.skip("no feuds available")
        feud_id = feuds[0]["feud_id"]
        s.post(f"{API}/feuds/{feud_id}/vote", headers=hdr, json={"side": "B"})
        r_add = s.post(f"{API}/feuds/{feud_id}/comments",
                       headers=hdr, json={"text": f"TEST_race_parent_{uuid.uuid4().hex[:6]}"})
        if r_add.status_code != 200:
            pytest.skip(f"cannot create parent: {r_add.status_code}")
        add_body = r_add.json()
        cid = (add_body.get("comment") or add_body).get("comment_id")
        assert cid
        r_rep = s.post(f"{API}/comments/{cid}/replies",
                       headers=hdr, json={"text": f"TEST_race_reply_{uuid.uuid4().hex[:6]}"})
        assert r_rep.status_code == 200, r_rep.text
        rep_body = r_rep.json()
        rid = (rep_body.get("reply") or rep_body).get("reply_id")
        assert rid, f"missing reply_id in {rep_body}"
        r_del = s.delete(f"{API}/replies/{rid}", headers=hdr)
        assert r_del.status_code == 200, r_del.text
        assert r_del.json().get("moderated") is False
        s.delete(f"{API}/comments/{cid}", headers=hdr)


# ═══════════ 7. Burst — simulate poller storm w/o auth ═══════════
class TestConcurrentAnonymousLoad:
    def test_burst_no_5xx(self, s):
        start = time.time()
        codes = []
        for _ in range(10):
            r = s.get(f"{API}/categories", timeout=8)
            codes.append(r.status_code)
        elapsed = time.time() - start
        assert all(c < 500 for c in codes), f"5xx in burst: {codes}"
        assert elapsed < 20, f"burst too slow: {elapsed:.1f}s"
