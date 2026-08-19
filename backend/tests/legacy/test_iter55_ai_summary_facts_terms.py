"""Iteration 55 backend tests:
  1. POST /api/feuds/{feud_id}/ai-summary (Claude sonnet 4.6 summary of factions).
  2. Fact-checker pipeline (verifies db.feuds documents carry `fact_check`).
  3. Legal terms endpoints (`GET /api/legal/terms`, `POST /api/users/me/accept-terms`).
"""
import os
import time
import random
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://bot-burst-fix.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_KEY = os.environ.get("ADMIN_TOKEN", "populus-admin-42b8f3")

_MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
_DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = MongoClient(_MONGO_URL)
_db = _mongo[_DB_NAME]


def _signup_verified() -> dict:
    ts = int(time.time() * 1000)
    salt = random.randint(1000, 9999)
    email = f"iter55_{ts}_{salt}@test.dev"
    password = "Testing123!"
    nickname = f"i55u{ts % 100000}{salt}"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "nickname": nickname,
    })
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    _db.users.update_one({"email": email}, {"$set": {"email_verified": True}})
    rl = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert rl.status_code == 200, f"login failed: {rl.status_code} {rl.text}"
    body = rl.json()
    tok = body["token"]
    uid = body["user"]["user_id"]
    # complete onboarding
    r2 = requests.patch(
        f"{API}/auth/me/profile",
        json={"age": 27, "sex": "M", "region": "Lombardia", "favorite_categories": ["politica", "musica"]},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 200, f"onboarding failed: {r2.status_code} {r2.text}"
    return {"token": tok, "user_id": uid, "email": email, "nickname": nickname}


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _pick_feud(token):
    r = requests.get(f"{API}/feuds", headers=_hdr(token))
    assert r.status_code == 200, r.text
    data = r.json()
    if isinstance(data, dict):
        items = data.get("feuds") or data.get("items") or []
    else:
        items = data
    assert items, "no feuds available for testing"
    return items[0]


# =========================================================================
# Test 1 — AI faction summary
# =========================================================================
class TestAiSummary:
    """POST /api/feuds/{feud_id}/ai-summary."""

    @classmethod
    def setup_class(cls):
        cls.u1 = _signup_verified()
        cls.u2 = _signup_verified()
        cls.u3 = _signup_verified()
        cls.u4 = _signup_verified()
        cls.u5 = _signup_verified()
        cls.u6 = _signup_verified()
        cls.feud = _pick_feud(cls.u1["token"])
        cls.feud_id = cls.feud["feud_id"]
        for u, side in ((cls.u1, "A"), (cls.u2, "B"), (cls.u3, "A"), (cls.u4, "B"), (cls.u5, "A"), (cls.u6, "B")):
            requests.post(f"{API}/feuds/{cls.feud_id}/vote", json={"side": side}, headers=_hdr(u["token"]))
        pairs = [
            (cls.u1, f"Sostengo {cls.feud.get('party_a')}: i loro argomenti sono supportati dai dati recenti riportati dalla stampa e mostrano concretamente perché questa posizione tutela di più i cittadini comuni."),
            (cls.u3, f"La prima fazione ha ragione perché i precedenti storici dimostrano che l'approccio opposto ha già fallito almeno due volte in situazioni analoghe."),
            (cls.u5, f"Concordo con la prima parte: la loro proposta è pragmatica, misurabile, e non lascia spazio a interpretazioni ideologiche vaghe che confondono l'opinione pubblica."),
            (cls.u2, f"Voto {cls.feud.get('party_b')}: la seconda posizione riflette meglio la realtà quotidiana delle famiglie italiane e non ignora i numeri sui costi effettivi."),
            (cls.u4, f"Preferisco la seconda fazione perché i loro esperti hanno esperienza concreta sul campo e non promesse elettorali riscaldate."),
            (cls.u6, f"Voto la seconda parte: le loro conclusioni si basano su studi peer-reviewed pubblicati negli ultimi due anni, non su slogan."),
        ]
        for u, t in pairs:
            requests.post(f"{API}/feuds/{cls.feud_id}/comments", json={"text": t}, headers=_hdr(u["token"]))

    def test_summary_with_comments(self):
        # Sanity: comments should actually be visible before we ask the AI
        cr = requests.get(f"{API}/feuds/{self.feud_id}/comments", headers=_hdr(self.u1["token"]))
        cdat = cr.json()
        print(f"DEBUG comments visible: side_a={len(cdat.get('side_a') or [])} side_b={len(cdat.get('side_b') or [])} feud_id={self.feud_id}")
        r = requests.post(f"{API}/feuds/{self.feud_id}/ai-summary", headers=_hdr(self.u1["token"]))
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        assert isinstance(data.get("side_a"), list)
        assert isinstance(data.get("side_b"), list)
        assert isinstance(data.get("common"), list)
        assert data.get("empty") is False, f"AI returned empty despite comments: {data}"
        assert data.get("party_a")
        assert data.get("party_b")
        assert "generated_at" in data
        for bucket in ("side_a", "side_b", "common"):
            for b in data[bucket]:
                assert isinstance(b, str)
                assert len(b) <= 250, f"bullet too long: {b!r}"

    def test_summary_no_comments_returns_empty(self):
        # Create a fresh feud-less scenario by using another feud with no comments if any,
        # OR by simply asserting the endpoint returns empty:true when comments are wiped.
        # We create a fresh throwaway feud via admin generate would be slow; instead we
        # pick another feud with no comments if available.
        r = requests.get(f"{API}/feuds", headers=_hdr(self.u1["token"]))
        j = r.json()
        items = j.get("feuds") or j.get("items") or [] if isinstance(j, dict) else j
        target = None
        for f in items:
            if f["feud_id"] == self.feud_id:
                continue
            # check comments count
            cr = requests.get(f"{API}/feuds/{f['feud_id']}/comments", headers=_hdr(self.u1["token"]))
            if cr.status_code != 200:
                continue
            cd = cr.json()
            if not cd.get("side_a") and not cd.get("side_b"):
                target = f
                break
        if not target:
            pytest.skip("no feud without comments available")
        r2 = requests.post(f"{API}/feuds/{target['feud_id']}/ai-summary", headers=_hdr(self.u1["token"]))
        assert r2.status_code == 200
        d = r2.json()
        assert d.get("empty") is True
        assert d.get("side_a") == []
        assert d.get("side_b") == []
        assert d.get("common") == []

    def test_summary_requires_auth(self):
        r = requests.post(f"{API}/feuds/{self.feud_id}/ai-summary")
        assert r.status_code in (401, 403), f"{r.status_code} {r.text}"

    def test_summary_unknown_feud_404(self):
        r = requests.post(f"{API}/feuds/does-not-exist-xyz/ai-summary", headers=_hdr(self.u1["token"]))
        assert r.status_code == 404


# =========================================================================
# Test 2 — Fact-checker pipeline
# =========================================================================
class TestFactChecker:
    """Verifies db.feuds documents inserted via admin have `fact_check`."""

    def test_admin_generate_writes_fact_check(self):
        # Try to generate a new feud first (may return [] if dedupe kicks in)
        r = requests.post(
            f"{API}/admin/generate-daily?count=3",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=240,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        created = data.get("created") or []
        # If the generator produced fresh feuds, they MUST carry fact_check.
        # If not, we still verify recent AI-source feuds carry fact_check
        # (i.e. the pipeline hook is active for every insert).
        if created:
            for c in created:
                fid = c["feud_id"]
                d = _db.feuds.find_one({"feud_id": fid}, {"_id": 0})
                assert d, f"feud {fid} not persisted"
                fc = d.get("fact_check")
                assert isinstance(fc, dict), f"missing fact_check on {fid}"
                assert fc.get("decision") in ("PUBLISH", "CORRECT"), f"unexpected decision {fc.get('decision')}"
                assert fc.get("reason"), "fact_check must include reason"
                for field in ("title", "party_a", "party_b", "summary"):
                    assert d.get(field), f"{field} empty on {fid}"
        else:
            # Retrospective validation: at least one recent AI feud (from the
            # iteration under test) must have the fact_check field. This
            # confirms the pipeline hook is wired even when dedupe blocks
            # new creations.
            recent_with_fc = list(
                _db.feuds.find(
                    {"source": "ai", "fact_check": {"$exists": True, "$ne": None}},
                    {"_id": 0, "feud_id": 1, "fact_check": 1, "title": 1, "party_a": 1, "party_b": 1, "summary": 1},
                ).sort("created_at", -1).limit(5)
            )
            if not recent_with_fc:
                pytest.skip("no AI feuds with fact_check yet (feature just deployed / dedupe blocked generation)")
            for d in recent_with_fc:
                fc = d.get("fact_check")
                assert isinstance(fc, dict)
                assert fc.get("decision") in ("PUBLISH", "CORRECT"), f"unexpected decision {fc.get('decision')} on {d.get('feud_id')}"
                assert fc.get("reason"), f"fact_check missing reason on {d.get('feud_id')}"
                for field in ("title", "party_a", "party_b", "summary"):
                    assert d.get(field), f"{field} empty on {d.get('feud_id')}"


# =========================================================================
# Test 3 — Terms & Privacy
# =========================================================================
class TestTerms:
    """GET /api/legal/terms + POST /api/users/me/accept-terms."""

    def test_get_terms_public(self):
        r = requests.get(f"{API}/legal/terms")
        assert r.status_code == 200
        d = r.json()
        assert d.get("version") == "v1"
        text = d.get("text") or ""
        assert len(text) > 1000, f"terms text too short: {len(text)}"
        assert "Populus" in text
        assert ("GDPR" in text) or ("Garante" in text)
        assert d.get("updated_at")

    def test_terms_flow_full(self):
        u = _signup_verified()
        # Reset acceptance to simulate fresh user (in case onboarding auto-set it)
        _db.users.update_one({"user_id": u["user_id"]}, {"$unset": {"terms_accepted_version": ""}})
        r = requests.get(f"{API}/auth/me", headers=_hdr(u["token"]))
        assert r.status_code == 200
        me_before = r.json()["user"]
        assert me_before.get("terms_accepted") is False

        # accept
        r2 = requests.post(f"{API}/users/me/accept-terms", json={"version": "v1"}, headers=_hdr(u["token"]))
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get("terms_accepted") is True
        assert d2.get("terms_accepted_at")

        # verify persisted
        r3 = requests.get(f"{API}/auth/me", headers=_hdr(u["token"]))
        me_after = r3.json()["user"]
        assert me_after.get("terms_accepted") is True

    def test_accept_wrong_version(self):
        u = _signup_verified()
        r = requests.post(f"{API}/users/me/accept-terms", json={"version": "wrongversion"}, headers=_hdr(u["token"]))
        assert r.status_code == 400

    def test_accept_requires_auth(self):
        r = requests.post(f"{API}/users/me/accept-terms", json={"version": "v1"})
        assert r.status_code in (401, 403)
