"""
Iter122 tests
==============
Backend regression + code-presence smoke for:
  1) Editorial-guidelines rules injected into the AI news→feud generator
     and the fact-checker LLM system prompts (server.py L~4290 + L~4745).
  2) Native store-review manager (frontend/src/utils/reviewManager.ts) —
     purely a source-level presence check plus package.json version pin.

We intentionally do NOT trigger the live LLM generator here — it is:
  - locked behind `X-Admin-Key`
  - expensive
  - non-deterministic
The editorial change is content-only (no API contract), so the acceptance
signal for iter122 is a source grep of the key phrases.
"""

import os
import re
import json
import pathlib
import requests
import pytest

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://localhost:8001").rstrip("/")
SERVER_PY = pathlib.Path("/app/backend/server.py")
REVIEW_TS = pathlib.Path("/app/frontend/src/utils/reviewManager.ts")
LAYOUT_TSX = pathlib.Path("/app/frontend/app/_layout.tsx")
FEUD_TSX = pathlib.Path("/app/frontend/app/(tabs)/feud/[id].tsx")
PACKAGE_JSON = pathlib.Path("/app/frontend/package.json")


# ── Feature 1: Editorial guidelines in AI system prompts ─────────────
class TestEditorialPromptPresence:
    """Grep-level check that the 'LINEA EDITORIALE' block was inserted."""

    @pytest.fixture(scope="class")
    def server_src(self):
        assert SERVER_PY.exists(), "backend/server.py missing"
        return SERVER_PY.read_text(encoding="utf-8")

    def test_generator_prompt_contains_impartiality_block(self, server_src):
        assert "LINEA EDITORIALE — IMPARZIALITÀ E NEUTRALITÀ" in server_src

    def test_generator_prompt_contains_simmetria_rule(self, server_src):
        assert "SIMMETRIA DELLE PARTI" in server_src

    def test_generator_prompt_lists_political_axis(self, server_src):
        # Both extremes must be enumerated for the symmetry contract.
        for axis in ("destra", "sinistra", "populista", "sovranista",
                     "progressista", "conservatore"):
            assert axis in server_src, f"missing political axis token: {axis}"

    def test_generator_prompt_bans_ai_value_judgments(self, server_src):
        # The banned-tone examples must be quoted in the prompt so the
        # LLM knows what NOT to say.
        assert "ovviamente sbagliato" in server_src
        assert "come al solito" in server_src

    def test_generator_prompt_has_minors_skip_rule(self, server_src):
        assert "MINORI E VITTIME" in server_src
        # The skip JSON contract is present.
        assert '"skip": true' in server_src

    def test_factcheck_prompt_contains_impartiality_block(self, server_src):
        assert "LINEA EDITORIALE — VERIFICA IMPARZIALITÀ" in server_src

    def test_factcheck_prompt_mentions_asymmetric_adjectives(self, server_src):
        # The reject-list should reference the coraggioso/folle example.
        assert "coraggioso" in server_src
        assert "folle" in server_src

    def test_factcheck_prompt_rejects_conspiracy_parity(self, server_src):
        assert "complottiste" in server_src or "pseudoscienza" in server_src


# ── Feature 2: reviewManager source-level smoke ─────────────────────
class TestReviewManagerSource:
    def test_review_manager_file_exists(self):
        assert REVIEW_TS.exists(), "src/utils/reviewManager.ts missing"

    def test_review_manager_exports_public_api(self):
        src = REVIEW_TS.read_text(encoding="utf-8")
        # Named export object must expose all three methods.
        assert re.search(r"export\s+const\s+reviewManager\s*=", src)
        for fn in ("markSessionOpen", "recordAction", "maybePrompt"):
            assert fn in src, f"reviewManager missing method: {fn}"

    def test_review_manager_uses_platform_web_noop(self):
        src = REVIEW_TS.read_text(encoding="utf-8")
        # Web must be a no-op inside maybePrompt.
        assert 'Platform.OS === "web"' in src or "Platform.OS === 'web'" in src

    def test_review_manager_uses_expo_store_review(self):
        src = REVIEW_TS.read_text(encoding="utf-8")
        assert "expo-store-review" in src
        assert "requestReview" in src
        assert "hasAction" in src
        assert "isAvailableAsync" in src

    def test_review_manager_gates_present(self):
        src = REVIEW_TS.read_text(encoding="utf-8")
        # Numeric gate constants — updates to the numbers must be
        # coordinated with this test.
        assert "MIN_SESSIONS = 3" in src
        assert "MIN_ACTIONS = 5" in src
        assert "MIN_DAYS_SINCE_INSTALL = 3" in src
        assert "MIN_DAYS_BETWEEN_PROMPTS = 120" in src

    def test_expo_store_review_pinned_in_package_json(self):
        pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        deps = pkg.get("dependencies", {})
        v = deps.get("expo-store-review")
        assert v is not None, "expo-store-review not installed"
        # Accept ~9.0.9 or 9.0.9.
        assert "9.0.9" in v, f"expo-store-review pinned to unexpected version {v!r}"

    def test_root_layout_calls_mark_session_open(self):
        src = LAYOUT_TSX.read_text(encoding="utf-8")
        assert 'from "@/src/utils/reviewManager"' in src
        assert "reviewManager.markSessionOpen()" in src

    def test_feud_screen_records_all_three_actions(self):
        src = FEUD_TSX.read_text(encoding="utf-8")
        assert 'from "@/src/utils/reviewManager"' in src
        # Each of vote/comment/reply must trigger recordAction.
        assert 'reviewManager.recordAction("vote")' in src
        assert 'reviewManager.recordAction("comment")' in src
        assert 'reviewManager.recordAction("reply")' in src


# ── Regression: liveness of the public API ──────────────────────────
class TestLiveApiRegression:
    """A single lightweight probe — full CRUD is covered by iter113-121."""

    def test_api_root_alive(self):
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True

    def test_feuds_list_endpoint_alive(self):
        # Feuds list is unauthenticated and is the highest-traffic route —
        # good canary for a regression.
        r = requests.get(f"{BASE_URL}/api/feuds", timeout=15)
        assert r.status_code in (200, 401), f"unexpected {r.status_code}"
        if r.status_code == 200:
            data = r.json()
            # Shape sanity — must be dict-like with at least one common key.
            assert isinstance(data, (list, dict))
