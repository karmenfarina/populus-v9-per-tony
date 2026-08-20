"""
Iter 153 — Verify the story-caption fix:

Fix location: /app/backend/bot_engine.py::_generate_story_caption
  1) New signature: (persona, feud) — user_prompt now injects the feud
     title + party_a + party_b + category so the LLM cannot hallucinate
     unrelated content.
  2) Post-response refusal filter: if the LLM text contains any of the
     refusal markers ("non posso", "mi servirebbe", "post di riferimento"
     etc.) the function returns None so the caller stores comment=''
     rather than the refusal string.

Strategy: We stub sys.modules['emergentintegrations.llm.chat'] with a
fake LlmChat that captures the user_prompt AND returns a canned string
of our choice. That lets us test BOTH the prompt-construction contract
and the refusal filter without hitting the real LLM budget.

Also does regression on:
  * _bot_create_story anti-dup and 1/24h quota (iter146 fix)
  * GET /api/stories/feed (iter145)
  * GET /api/feuds, /api/auth/signup, /api/auth/anonymous
"""
from __future__ import annotations

import os
import sys
import uuid
import types
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

sys.path.insert(0, "/app/backend")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


# ── Fake LLM module ───────────────────────────────────────────────────
# Captured across a single test via module-level containers; each test
# resets them explicitly in setup.
CAPTURED_PROMPTS: list[str] = []
CAPTURED_SYSTEMS: list[str] = []
FAKE_RESPONSE = {"text": "una frase generica"}


class _FakeUserMessage:
    def __init__(self, text: str = ""):
        self.text = text


class _FakeChat:
    def __init__(self, *_, api_key=None, session_id=None, system_message=None):
        CAPTURED_SYSTEMS.append(system_message or "")

    def with_model(self, *_args, **_kwargs):
        return self

    async def send_message(self, msg):
        CAPTURED_PROMPTS.append(getattr(msg, "text", ""))
        return FAKE_RESPONSE["text"]


def _install_fake_llm():
    """Inject a stub emergentintegrations.llm.chat into sys.modules so
    the runtime import inside bot_engine picks it up.
    """
    root = types.ModuleType("emergentintegrations")
    llm = types.ModuleType("emergentintegrations.llm")
    chat = types.ModuleType("emergentintegrations.llm.chat")
    chat.LlmChat = _FakeChat
    chat.UserMessage = _FakeUserMessage
    sys.modules["emergentintegrations"] = root
    sys.modules["emergentintegrations.llm"] = llm
    sys.modules["emergentintegrations.llm.chat"] = chat


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture
def be():
    import bot_engine as _be
    import importlib
    # Iter146 tests (and potentially others) monkey-patch
    # `_generate_story_caption` at module level without cleanup. Reload
    # the module here so we always exercise the REAL implementation
    # with the fake LLM injected via sys.modules.
    _install_fake_llm()
    _be = importlib.reload(_be)
    os.environ.setdefault("EMERGENT_LLM_KEY", "sk-test")
    return _be


@pytest.fixture
def bot_user(mongo):
    b = mongo.users.find_one({"is_bot": True}, {"_id": 0})
    assert b, "No bot users seeded"
    return b


@pytest.fixture
def real_feud(mongo):
    f = mongo.feuds.find_one({"is_hidden": {"$ne": True}}, {"_id": 0})
    assert f, "No visible feuds in DB"
    return f


def _wire_db(be):
    from motor.motor_asyncio import AsyncIOMotorClient
    aclient = AsyncIOMotorClient(MONGO_URL)
    be._db = aclient[DB_NAME]
    return aclient


def _reset_captures(response_text: str):
    CAPTURED_PROMPTS.clear()
    CAPTURED_SYSTEMS.clear()
    FAKE_RESPONSE["text"] = response_text


# ══ Task 1: prompt now includes feud context ═════════════════════════

class TestPromptContainsFeudContext:
    """The critical fix: _generate_story_caption(persona, feud) must
    include title / party_a / party_b in the user_prompt sent to the LLM.
    """

    def test_signature_accepts_persona_and_feud(self, be):
        import inspect
        sig = inspect.signature(be._generate_story_caption)
        params = list(sig.parameters.keys())
        assert params[:2] == ["persona", "feud"], (
            f"_generate_story_caption signature must be (persona, feud, ...); got {params}"
        )

    def test_user_prompt_contains_title_and_parties(self, be):
        _reset_captures("una frase pertinente")
        persona = {
            "display_name": "Test Persona",
            "tone": "serio",
            "verbosity": "breve",
            "political_lean": "centro",
            "main_topic": "politica",
            "age": 30, "profession": "utente", "city": "Roma",
        }
        feud = {
            "title": "UNICO_TITLE_ABCDEF",
            "party_a": "PARTY_ALPHA_XYZ",
            "party_b": "PARTY_BETA_XYZ",
            "category_label": "Politica",
            "category": "politica",
        }

        async def run():
            _wire_db(be)
            return await be._generate_story_caption(persona, feud)

        result = asyncio.run(run())
        assert result == "una frase pertinente"
        assert len(CAPTURED_PROMPTS) == 1, "LLM was not called exactly once"
        prompt = CAPTURED_PROMPTS[0]
        assert "UNICO_TITLE_ABCDEF" in prompt, f"title missing from user_prompt: {prompt!r}"
        assert "PARTY_ALPHA_XYZ" in prompt, f"party_a missing from user_prompt: {prompt!r}"
        assert "PARTY_BETA_XYZ" in prompt, f"party_b missing from user_prompt: {prompt!r}"

    def test_system_prompt_reinforces_no_intro(self, be):
        """story_prompt_for must instruct the LLM not to ask for context."""
        _reset_captures("frase")
        persona = {
            "display_name": "X", "tone": "serio", "verbosity": "breve",
            "political_lean": "centro", "main_topic": "politica",
            "age": 30, "profession": "utente", "city": "Roma",
        }
        feud = {"title": "t", "party_a": "a", "party_b": "b",
                "category_label": "Politica", "category": "politica"}

        async def run():
            _wire_db(be)
            return await be._generate_story_caption(persona, feud)

        asyncio.run(run())
        assert CAPTURED_SYSTEMS
        sys_prompt = CAPTURED_SYSTEMS[0].lower()
        # Reinforcement clauses added in iter153 fix
        assert "non introdurre" in sys_prompt or "non chiedere contesto" in sys_prompt, (
            f"story_prompt_for must forbid intro/context-asking; got: {sys_prompt[:300]!r}"
        )


# ══ Task 2: refusal marker filter returns None ═══════════════════════

class TestRefusalFilter:
    """When the LLM outputs a refusal-style caption, the function must
    return None instead of the raw refusal (which was previously being
    stored as the story caption)."""

    @pytest.mark.parametrize("refusal_text", [
        "Non posso commentare senza il post di riferimento.",
        "Mi servirebbe il post di riferimento per rispondere",
        "Non ho abbastanza contesto per commentare",
        "Puoi darmi il post di cui parlo?",
        "Non riesco a scrivere una story senza sapere il riferimento a un post",
    ])
    def test_refusal_returns_none(self, be, refusal_text):
        _reset_captures(refusal_text)
        persona = {
            "display_name": "X", "tone": "serio", "verbosity": "breve",
            "political_lean": "centro", "main_topic": "politica",
            "age": 30, "profession": "utente", "city": "Roma",
        }
        feud = {"title": "t", "party_a": "a", "party_b": "b",
                "category_label": "Politica", "category": "politica"}

        async def run():
            _wire_db(be)
            return await be._generate_story_caption(persona, feud)

        result = asyncio.run(run())
        assert result is None, (
            f"Refusal text must be filtered → None; got: {result!r}"
        )

    def test_valid_caption_passes_through(self, be):
        _reset_captures("Che spettacolo assurdo")
        persona = {
            "display_name": "X", "tone": "serio", "verbosity": "breve",
            "political_lean": "centro", "main_topic": "politica",
            "age": 30, "profession": "utente", "city": "Roma",
        }
        feud = {"title": "t", "party_a": "a", "party_b": "b",
                "category_label": "Politica", "category": "politica"}

        async def run():
            _wire_db(be)
            return await be._generate_story_caption(persona, feud)

        result = asyncio.run(run())
        assert result == "Che spettacolo assurdo"

    def test_empty_response_returns_none(self, be):
        _reset_captures("")
        persona = {
            "display_name": "X", "tone": "serio", "verbosity": "breve",
            "political_lean": "centro", "main_topic": "politica",
            "age": 30, "profession": "utente", "city": "Roma",
        }
        feud = {"title": "t", "party_a": "a", "party_b": "b",
                "category_label": "Politica", "category": "politica"}

        async def run():
            _wire_db(be)
            return await be._generate_story_caption(persona, feud)

        assert asyncio.run(run()) is None


# ══ Task 3: end-to-end via _bot_create_story ═════════════════════════

class TestBotCreateStoryIntegration:
    """Full flow: driving _bot_create_story with the fake LLM."""

    def _prep(self, mongo, bot_id, feud_id):
        now = datetime.now(timezone.utc)
        mongo.stories.delete_many({
            "user_id": bot_id,
            "created_at": {"$gte": now - timedelta(hours=24)},
        })
        mongo.stories.delete_many({"user_id": bot_id, "feud_id": feud_id})

    def test_refusal_produces_empty_comment(self, be, bot_user, real_feud, mongo):
        """LLM refuses → story is still created (function catches None
        with `caption or ''`), but comment must be empty, NOT the refusal."""
        _reset_captures("Non posso commentare, mi servirebbe il post di riferimento")
        bot_id = bot_user["user_id"]
        feud_id = real_feud["feud_id"]
        self._prep(mongo, bot_id, feud_id)
        try:
            async def run():
                _wire_db(be)
                await be._bot_create_story(bot_user, real_feud)

            asyncio.run(run())
            doc = mongo.stories.find_one(
                {"user_id": bot_id, "feud_id": feud_id}, {"_id": 0}
            )
            assert doc is not None, "story should still be created (empty caption path)"
            assert doc["comment"] == "", (
                f"Refusal must NOT be persisted as comment; got: {doc['comment']!r}"
            )
        finally:
            mongo.stories.delete_many({"user_id": bot_id, "feud_id": feud_id})

    def test_valid_caption_persisted(self, be, bot_user, real_feud, mongo):
        _reset_captures("dibattito acceso questo")
        bot_id = bot_user["user_id"]
        feud_id = real_feud["feud_id"]
        self._prep(mongo, bot_id, feud_id)
        try:
            async def run():
                _wire_db(be)
                await be._bot_create_story(bot_user, real_feud)

            asyncio.run(run())
            doc = mongo.stories.find_one(
                {"user_id": bot_id, "feud_id": feud_id}, {"_id": 0}
            )
            assert doc is not None
            assert doc["comment"] == "dibattito acceso questo"
            assert doc["kind"] == "feud"
        finally:
            mongo.stories.delete_many({"user_id": bot_id, "feud_id": feud_id})


# ══ Regression: iter146 anti-dup + 1/24h quota still hold ═══════════

class TestBotStoryQuotaRegression:
    def test_anti_dup_same_feud(self, be, bot_user, real_feud, mongo):
        _reset_captures("caption ok")
        bot_id = bot_user["user_id"]
        feud_id = real_feud["feud_id"]
        now = datetime.now(timezone.utc)
        mongo.stories.delete_many({"user_id": bot_id,
                                   "created_at": {"$gte": now - timedelta(hours=24)}})
        mongo.stories.delete_many({"user_id": bot_id, "feud_id": feud_id})
        try:
            async def run():
                _wire_db(be)
                await be._bot_create_story(bot_user, real_feud)
                await be._bot_create_story(bot_user, real_feud)

            asyncio.run(run())
            n = mongo.stories.count_documents({
                "user_id": bot_id,
                "feud_id": feud_id,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            })
            assert n == 1, f"Anti-dup: expected 1 active story, got {n}"
        finally:
            mongo.stories.delete_many({"user_id": bot_id, "feud_id": feud_id})

    def test_quota_one_per_24h(self, be, bot_user, mongo):
        _reset_captures("caption ok")
        bot_id = bot_user["user_id"]
        feuds = list(mongo.feuds.find({"is_hidden": {"$ne": True}}, {"_id": 0}).limit(2))
        assert len(feuds) >= 2
        f_a, f_b = feuds[0], feuds[1]
        now = datetime.now(timezone.utc)
        mongo.stories.delete_many({"user_id": bot_id,
                                   "created_at": {"$gte": now - timedelta(hours=24)}})
        mongo.stories.delete_many({"user_id": bot_id,
                                   "feud_id": {"$in": [f_a["feud_id"], f_b["feud_id"]]}})
        try:
            async def run():
                _wire_db(be)
                await be._bot_create_story(bot_user, f_a)
                await be._bot_create_story(bot_user, f_b)

            asyncio.run(run())
            n = mongo.stories.count_documents({
                "user_id": bot_id,
                "created_at": {"$gte": now - timedelta(hours=24)},
            })
            assert n == 1, f"Quota 1/24h: expected 1 story, got {n}"
        finally:
            mongo.stories.delete_many({
                "user_id": bot_id,
                "feud_id": {"$in": [f_a["feud_id"], f_b["feud_id"]]},
            })


# ══ Regression: _generate_comment is NOT affected ═══════════════════

class TestGenerateCommentUntouched:
    def test_comment_signature_still_three_args(self, be):
        import inspect
        sig = inspect.signature(be._generate_comment)
        params = list(sig.parameters.keys())
        assert params[:3] == ["persona", "feud", "side"], (
            f"_generate_comment signature must remain (persona, feud, side); got {params}"
        )


# ══ Regression: HTTP endpoints ═══════════════════════════════════════

class TestHttpRegression:
    def test_stories_feed_reachable(self, api):
        # Unauthenticated should give 401/403 — endpoint alive, not 5xx.
        r = api.get(f"{BASE_URL}/api/stories/feed", timeout=10)
        assert r.status_code < 500, r.text

    def test_feuds_endpoint_ok(self, api):
        r = api.get(f"{BASE_URL}/api/feuds?limit=3", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))

    def test_signup_and_anonymous(self, api):
        email = f"TEST_iter153_{uuid.uuid4().hex[:8]}@populus-it.co"
        r = api.post(
            f"{BASE_URL}/api/auth/signup",
            json={"email": email, "password": "TestPass123!",
                  "nickname": f"i153{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r.status_code in (200, 201), r.text

        r2 = api.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": f"a153{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r2.status_code in (200, 201), r2.text
