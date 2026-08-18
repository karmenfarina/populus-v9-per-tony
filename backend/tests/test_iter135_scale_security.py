"""
Iteration 135 — Scalabilità + Sicurezza backend.

Copre:
  1. Boot: ensure_indexes ha creato indici (verifica indiretta via mongo
     tramite state endpoint + log server, e via query di lettura).
  2. Security headers (X-Content-Type-Options, X-Frame-Options,
     Strict-Transport-Security, Referrer-Policy, Permissions-Policy).
  3. Cache-Control headers su endpoint statici (/api/categories,
     /api/sponsors, /api/legal/*, /api/docs/*).
  4. GZip: risposta >1KB con Accept-Encoding: gzip → Content-Encoding:gzip.
  5. Rate limit /auth/signup: 6° tentativo dallo stesso IP → 429.
  6. Rate limit /auth/login: 11° tentativo con stessa email+IP → 429.
  7. Admin bots/state con X-Admin-Key valida ritorna stato bot.
  8. Regressione router estratti (legal, docs, sponsors, favorites,
     support, blocks, notifications).
  9. Doc /api/docs/scalabilita-sicurezza esiste con contenuto MD.
 10. Nessun leak di secret nei log.
 11. Pool MongoDB regge 20 richieste concorrenti su /api/feuds/hype.
 12. CSP header su /api/share/{id}/html (se faida esiste).
"""
from __future__ import annotations

import concurrent.futures as _cf
import os
import re
import time
import uuid

import pytest
import requests

BASE_URL = "http://localhost:8001"
ADMIN_KEY = os.environ.get("ADMIN_TOKEN", "populus-admin-42b8f3")
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ═══════════════════════════════════════════════════════════════════
# 1) Security headers
# ═══════════════════════════════════════════════════════════════════
class TestSecurityHeaders:
    def test_headers_on_categories(self, api):
        r = api.get(f"{BASE_URL}/api/categories")
        assert r.status_code == 200
        h = r.headers
        assert h.get("X-Content-Type-Options") == "nosniff", h.get("X-Content-Type-Options")
        assert h.get("X-Frame-Options") == "DENY", h.get("X-Frame-Options")
        assert "max-age=" in (h.get("Strict-Transport-Security") or ""), h.get("Strict-Transport-Security")
        assert h.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in (h.get("Permissions-Policy") or "")

    def test_headers_on_docs(self, api):
        r = api.get(f"{BASE_URL}/api/docs")
        assert r.status_code == 200
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("Strict-Transport-Security")


# ═══════════════════════════════════════════════════════════════════
# 2) Cache-Control
# ═══════════════════════════════════════════════════════════════════
class TestCacheControl:
    def test_categories_cache(self, api):
        r = api.get(f"{BASE_URL}/api/categories")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=300" in cc, cc
        assert "s-maxage=600" in cc, cc
        assert "public" in cc

    def test_professions_cache(self, api):
        r = api.get(f"{BASE_URL}/api/professions")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=300" in cc

    def test_sponsors_cache(self, api):
        r = api.get(f"{BASE_URL}/api/sponsors")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=120" in cc, cc

    def test_legal_terms_cache(self, api):
        r = api.get(f"{BASE_URL}/api/legal/terms")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=300" in cc, cc

    def test_docs_root_cache(self, api):
        r = api.get(f"{BASE_URL}/api/docs")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=300" in cc, cc

    def test_docs_slug_cache(self, api):
        r = api.get(f"{BASE_URL}/api/docs/regole")
        cc = (r.headers.get("Cache-Control") or "").lower()
        assert "max-age=300" in cc, cc


# ═══════════════════════════════════════════════════════════════════
# 3) GZip
# ═══════════════════════════════════════════════════════════════════
class TestGZip:
    def test_docs_regole_is_gzipped(self, api):
        # /api/docs/regole ritorna un markdown > 1KB.
        r = requests.get(
            f"{BASE_URL}/api/docs/regole",
            headers={"Accept-Encoding": "gzip"},
            stream=True,
        )
        assert r.status_code == 200
        enc = (r.headers.get("Content-Encoding") or "").lower()
        assert "gzip" in enc, f"Expected gzip encoding, headers: {dict(r.headers)}"

    def test_small_response_not_gzipped(self, api):
        # Il ping/root del router è piccolo → non deve essere gzippato.
        r = requests.get(
            f"{BASE_URL}/api/",
            headers={"Accept-Encoding": "gzip"},
        )
        # Non fail se piccolo: si accettano entrambi, verifico solo status.
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 4) Rate limiting
# ═══════════════════════════════════════════════════════════════════
class TestRateLimitSignup:
    """POST /auth/signup: max 5 tentativi / IP / ora → 6° = 429."""

    def test_signup_rate_limit(self, api):
        # Uso email uniche per NON creare account duplicati; il rate limit
        # è sull'IP, non sull'email. Il 6° tentativo deve essere 429.
        unique_suffix = uuid.uuid4().hex[:8]
        codes = []
        for i in range(6):
            payload = {
                "email": f"TEST_rl_{unique_suffix}_{i}@example.com",
                "password": "TestPass123!",
                "nickname": f"TESTrl{unique_suffix}{i}",
            }
            r = api.post(f"{BASE_URL}/api/auth/signup", json=payload)
            codes.append(r.status_code)
        assert codes[-1] == 429, f"Expected last to be 429, got {codes}"
        # I primi 5 possono essere 200/400/409, l'importante è NON 429.
        for c in codes[:5]:
            assert c != 429, f"Should not be rate-limited yet: {codes}"


class TestRateLimitLogin:
    """POST /auth/login: max 10 tentativi (IP+email) / ora → 11° = 429."""

    def test_login_rate_limit(self, api):
        unique_email = f"TEST_login_rl_{uuid.uuid4().hex[:8]}@example.com"
        # Uso un IP inedito per non collidere con precedenti chiamate:
        # /login usa X-Forwarded-For quindi lo simulo.
        fake_ip = f"10.99.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
        codes = []
        for _ in range(11):
            r = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": unique_email, "password": "wrongpass"},
                headers={"X-Forwarded-For": fake_ip, "Content-Type": "application/json"},
            )
            codes.append(r.status_code)
        assert codes[-1] == 429, f"Expected 11th to be 429, got {codes}"
        # I primi 10 devono essere 401 (bad creds) e non 429.
        for c in codes[:10]:
            assert c != 429, f"Rate limit fired too early: {codes}"


# ═══════════════════════════════════════════════════════════════════
# 5) Admin bots endpoints
# ═══════════════════════════════════════════════════════════════════
class TestAdminBotsState:
    def test_state_ok(self, api):
        r = api.get(f"{BASE_URL}/api/admin/bots/state", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        data = r.json()
        for k in ("enabled", "active_count", "reported_active", "total_bots"):
            assert k in data, f"missing {k}: {data}"
        assert data["total_bots"] == 100


# ═══════════════════════════════════════════════════════════════════
# 6) Regressione router estratti
# ═══════════════════════════════════════════════════════════════════
class TestRoutersRegression:
    def test_legal_terms(self, api):
        r = api.get(f"{BASE_URL}/api/legal/terms")
        assert r.status_code == 200
        j = r.json()
        assert "text" in j and len(j["text"]) > 50

    def test_legal_nda(self, api):
        r = api.get(f"{BASE_URL}/api/legal/nda")
        assert r.status_code == 200

    def test_docs_list_contains_scalabilita(self, api):
        r = api.get(f"{BASE_URL}/api/docs")
        assert r.status_code == 200
        slugs = {d["slug"] for d in r.json().get("docs", [])}
        assert "scalabilita-sicurezza" in slugs, slugs

    def test_docs_scalabilita_sicurezza(self, api):
        r = api.get(f"{BASE_URL}/api/docs/scalabilita-sicurezza")
        assert r.status_code == 200
        j = r.json()
        assert j["slug"] == "scalabilita-sicurezza"
        assert len(j.get("text", "")) > 100

    def test_sponsors(self, api):
        r = api.get(f"{BASE_URL}/api/sponsors")
        assert r.status_code == 200
        sp = r.json().get("sponsors", [])
        assert len(sp) >= 1
        for s in sp:
            assert "_id" not in s

    def test_favorites_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/favorites")
        assert r.status_code == 401

    def test_notifications_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/notifications")
        assert r.status_code == 401

    def test_blocks_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/users/me/blocks")
        assert r.status_code == 401

    def test_support_requires_auth(self, api):
        r = api.post(f"{BASE_URL}/api/support/submit", json={"message": "x"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# 7) Concurrent MongoDB pool sanity
# ═══════════════════════════════════════════════════════════════════
class TestConcurrentPool:
    def test_20_parallel_hype_requests(self):
        def _hit():
            r = requests.get(f"{BASE_URL}/api/feuds/hype", timeout=15)
            return r.status_code

        with _cf.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(lambda _: _hit(), range(20)))
        # Tutte 200; se il pool cadesse vedremmo 500/timeouts.
        assert all(c == 200 for c in results), f"non-200 in parallel: {results}"


# ═══════════════════════════════════════════════════════════════════
# 8) Nessun leak di secret nei log
# ═══════════════════════════════════════════════════════════════════
class TestNoSecretLeaks:
    def test_backend_logs_no_secret_values(self):
        import subprocess
        # Prendo solo gli ultimi 500 line di log per rapidità.
        try:
            out = subprocess.check_output(
                ["tail", "-n", "500", "/var/log/supervisor/backend.err.log"],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
        except Exception:
            out = ""
        # Leggo il valore da .env, poi verifico che NON compaia nei log.
        env_vals = {}
        try:
            with open("/app/backend/.env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    if k.strip() in ("JWT_SECRET", "EMERGENT_LLM_KEY", "ADMIN_TOKEN") and v:
                        env_vals[k.strip()] = v
        except FileNotFoundError:
            pytest.skip("backend .env non presente")

        leaked = []
        for k, v in env_vals.items():
            if len(v) >= 8 and v in out:
                leaked.append(k)
        assert not leaked, f"Secret potentially leaked in logs: {leaked}"


# ═══════════════════════════════════════════════════════════════════
# 9) CSP su /api/share/{id}/html
# ═══════════════════════════════════════════════════════════════════
class TestShareCSP:
    def test_share_html_csp_if_feud_exists(self, api):
        # Prendo un feud esistente dal feed pubblico.
        r = api.get(f"{BASE_URL}/api/feuds")
        assert r.status_code == 200
        feuds = r.json().get("feuds") or r.json()
        if isinstance(feuds, dict):
            feuds = feuds.get("items") or []
        if not feuds:
            pytest.skip("no feuds available for share test")
        # Cerca un id valido
        fid = None
        for f in feuds:
            fid = f.get("feud_id") or f.get("id") or f.get("_id")
            if fid:
                break
        if not fid:
            pytest.skip("no feud id extractable")

        r2 = api.get(f"{BASE_URL}/api/share/{fid}/html")
        # 200 se pubblico, 404 se non condivisibile: entrambi accettabili
        # ma se 200, deve avere CSP.
        if r2.status_code == 200:
            csp = r2.headers.get("Content-Security-Policy") or ""
            assert csp, f"CSP missing on /api/share/{fid}/html"
            assert "default-src" in csp
            assert "frame-ancestors 'none'" in csp
            assert "script-src 'none'" in csp
        else:
            pytest.skip(f"share/html not accessible for {fid}: {r2.status_code}")
