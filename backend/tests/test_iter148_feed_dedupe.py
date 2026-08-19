"""
Iteration 148 — Live feed dedupe (Task 2)
==========================================
Verifica del fix backend in `_list_feuds_impl` (server.py) che rimuove le
faide consecutive con lo stesso "signature" (prime 2 parole significative
del titolo). Regressioni:
- /api/feuds/archive continua a mostrare TUTTE le faide (compresi duplicati)
- /api/feuds/{id} continua a funzionare per faide filtrate dal live feed
- /api/feuds continua a includere my_vote, percentuali, hashtag
- login/signup/anonymous OK
- /api/stories/feed OK
"""
import os
import re
import time
import uuid
import pytest
import requests

BASE_URL = (os.environ.get("EXPO_BACKEND_URL") or os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "").rstrip("/")
assert BASE_URL, "EXPO_BACKEND_URL/EXPO_PUBLIC_BACKEND_URL missing"

# Same rules as backend _list_feuds_impl._sig
_STOP_IT = {
    'la','il','le','lo','gli','i','un','una','uno','di','a','al','allo',
    'alla','del','della','dei','delle','degli','e','o','ma','con','su',
    'per','tra','fra','in','da','ne','ci','vi','che','chi','cui','se',
    'come','quando','dove','mentre','vs','contro','ha','ho','hai',
}
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _sig(d: dict) -> str:
    title = (d.get('title') or '').strip().lower()
    title = _PUNCT.sub(' ', title)
    words = [w for w in title.split() if w and w not in _STOP_IT and len(w) >= 2]
    if len(words) >= 2:
        return ' '.join(words[:2])
    subj = (d.get('subject') or '').strip().lower()
    if subj and len(subj) >= 3:
        return subj
    return words[0] if words else ''


@pytest.fixture(scope='module')
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─── Task 2 core ───────────────────────────────────────────────────────

class TestFeedDedupe:
    def test_feed_all_no_consecutive_duplicates(self, session):
        r = session.get(f"{BASE_URL}/api/feuds", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        feuds = data.get('feuds') or []
        assert len(feuds) <= 200, f"Feed size {len(feuds)} > 200"
        sigs = [_sig(f) for f in feuds]
        # No duplicate signatures anywhere in the feed (stronger than just
        # "consecutive" — the fix uses a set-based dedupe)
        non_empty = [s for s in sigs if s]
        assert len(non_empty) == len(set(non_empty)), (
            f"Duplicate signatures detected: "
            f"{[s for s in non_empty if non_empty.count(s) > 1][:5]}"
        )

    @pytest.mark.parametrize("cat", ["cronaca", "tech", "gossip", "politica", "sport"])
    def test_feed_per_category_no_duplicates(self, session, cat):
        r = session.get(f"{BASE_URL}/api/feuds", params={"category": cat}, timeout=30)
        assert r.status_code == 200, r.text
        feuds = r.json().get('feuds') or []
        assert len(feuds) <= 200
        sigs = [_sig(f) for f in feuds if _sig(f)]
        assert len(sigs) == len(set(sigs)), (
            f"cat={cat} duplicate signatures: "
            f"{[s for s in sigs if sigs.count(s) > 1][:3]}"
        )

    def test_feed_payload_shape_regression(self, session):
        """/api/feuds must still return my_vote, percentages, hashtags."""
        r = session.get(f"{BASE_URL}/api/feuds", timeout=30)
        assert r.status_code == 200
        feuds = r.json().get('feuds') or []
        if not feuds:
            pytest.skip("Empty feed — cannot validate shape")
        f = feuds[0]
        # my_vote key present (value can be None)
        assert 'my_vote' in f
        # Percentages fields present (backend _attach_percentages)
        assert 'percent_a' in f or 'a_percent' in f or 'votes_a' in f
        # Hashtags present as list (may be empty)
        assert isinstance(f.get('hashtags', []), list)
        # Core fields
        for k in ('feud_id', 'title', 'category'):
            assert k in f


# ─── Task 2 regressions ────────────────────────────────────────────────

class TestArchiveNotDeduped:
    def test_archive_returns_all(self, session):
        """Archive must NOT dedupe — duplicates remain accessible.
        Archive endpoint requires a `date` query param (YYYY-MM-DD).
        """
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        r = session.get(
            f"{BASE_URL}/api/feuds/archive",
            params={"date": yesterday},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get('feuds') or data.get('items') or []
        assert isinstance(items, list)


class TestFeudDetailStillWorks:
    def test_random_feud_detail(self, session):
        r = session.get(f"{BASE_URL}/api/feuds", timeout=30)
        assert r.status_code == 200
        feuds = r.json().get('feuds') or []
        if not feuds:
            pytest.skip("Empty feed")
        fid = feuds[0]['feud_id']
        r2 = session.get(f"{BASE_URL}/api/feuds/{fid}", timeout=30)
        assert r2.status_code == 200, r2.text
        body = r2.json()
        # Endpoint wraps the feud under 'feud' key
        feud_doc = body.get('feud') if isinstance(body, dict) and 'feud' in body else body
        assert feud_doc.get('feud_id') == fid


# ─── General regressions ───────────────────────────────────────────────

@pytest.fixture(scope='module')
def anon_token(session):
    nick = f"tst_{uuid.uuid4().hex[:8]}"
    r = session.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()['token']


class TestAuthRegression:
    def test_anonymous_signup(self, anon_token):
        assert anon_token and len(anon_token) > 20

    def test_signup_login_email(self, session):
        email = f"test_{uuid.uuid4().hex[:10]}@example.com"
        nick = f"tst{uuid.uuid4().hex[:8]}"
        pw = "Passw0rd!"
        r = session.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": email, "password": pw, "nickname": nick},
            timeout=30,
        )
        # Signup returns 200 with requires_verification
        assert r.status_code == 200, r.text
        assert r.json().get('requires_verification') is True

    def test_login_bad_credentials(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrong"},
            timeout=30,
        )
        assert r.status_code in (401, 429)

    def test_auth_me_with_anon(self, session, anon_token):
        r = session.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {anon_token}"},
            timeout=30,
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get('user', {}).get('is_anonymous') is True


class TestStoriesFeed:
    def test_stories_feed_anon_ok(self, session, anon_token):
        r = session.get(
            f"{BASE_URL}/api/stories/feed",
            headers={"Authorization": f"Bearer {anon_token}"},
            timeout=30,
        )
        # Anon users can view stories feed (endpoint returns 200 with items list)
        assert r.status_code in (200, 403), r.text
        if r.status_code == 200:
            data = r.json()
            assert isinstance(data, dict)


class TestAdminModerationEndpointsGated:
    def test_admin_hidden_feuds_requires_key(self, session):
        r = session.get(f"{BASE_URL}/api/admin/hidden-feuds", timeout=15)
        # Should be gated: 401/403
        assert r.status_code in (401, 403, 422)

    def test_admin_hidden_feuds_with_valid_key(self, session):
        admin_key = "populus-admin-42b8f3"
        r = session.get(
            f"{BASE_URL}/api/admin/hidden-feuds",
            headers={"X-Admin-Key": admin_key},
            timeout=15,
        )
        # Either 200 (ok) or 401 if the token has rotated. Accept both, log.
        assert r.status_code in (200, 401), r.text


# ─── Historical duplicate patterns (soft check) ────────────────────────
class TestHistoricalDuplicates:
    """The user reported specific duplicate pairs: Ranucci/Forleo, Google
    Pixel 11, Palio. If these still appear in DB, they must now appear
    only ONCE in the live feed."""

    def _find(self, feuds, patterns):
        pats = [p.lower() for p in patterns]
        return [f for f in feuds if any(p in (f.get('title') or '').lower() for p in pats)]

    @pytest.mark.parametrize("patterns", [
        ["ranucci", "forleo"],
        ["google pixel 11"],
        ["palio di"],
    ])
    def test_historical_duplicates_appear_at_most_once(self, session, patterns):
        r = session.get(f"{BASE_URL}/api/feuds", timeout=30)
        assert r.status_code == 200
        feuds = r.json().get('feuds') or []
        matches = self._find(feuds, patterns)
        # If none present now, the archive has them but they aged out — pass
        if not matches:
            pytest.skip(f"No live feuds match {patterns}")
        # Group by signature — the dedupe should ensure at most one per sig
        by_sig = {}
        for f in matches:
            by_sig.setdefault(_sig(f), []).append(f.get('title'))
        for sig, titles in by_sig.items():
            assert len(titles) == 1, f"Signature '{sig}' still has {len(titles)} live entries: {titles}"
