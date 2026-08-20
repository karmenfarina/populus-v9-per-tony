"""
Iter 154 — Verify the NEW anti-generic caption filter in
/app/backend/bot_engine.py::_generate_story_caption.

Fix on top of iter153's refusal filter: if the LLM returns a caption
that (a) is <90 chars long AND (b) does NOT contain at least one
significant keyword (>=4 chars, excluding a curated Italian stopword
list) taken from the feud's title / party_a / party_b, the function
returns None so the story is stored with an empty caption instead of
a bot-obvious generic filler like "questo mi piace".

Filter details validated here:
  * Case-insensitive + accent-insensitive (NFD normalisation)
  * Stopwords (questo/molto/fatto/bene/…) do not count as keywords
  * Long captions (>=90 chars) bypass the keyword requirement
  * Interaction with the iter153 refusal filter (both still apply)

Also does light regression on:
  * story_prompt_for reinforcement clauses (from iter153/iter154)
  * _bot_create_story anti-dup + 1/24h quota (iter146)
  * Public HTTP endpoints: /api/feuds, /api/auth/anonymous
"""
from __future__ import annotations

import os
import sys
import uuid
import types
import asyncio
import importlib
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


# ── Fake LlmChat stub (same shape as iter153) ─────────────────────────
CAPTURED_PROMPTS: list[str] = []
CAPTURED_SYSTEMS: list[str] = []
FAKE_RESPONSE = {"text": ""}


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
    root = types.ModuleType("emergentintegrations")
    llm = types.ModuleType("emergentintegrations.llm")
    chat = types.ModuleType("emergentintegrations.llm.chat")
    chat.LlmChat = _FakeChat
    chat.UserMessage = _FakeUserMessage
    sys.modules["emergentintegrations"] = root
    sys.modules["emergentintegrations.llm"] = llm
    sys.modules["emergentintegrations.llm.chat"] = chat


def _reset(text: str):
    CAPTURED_PROMPTS.clear()
    CAPTURED_SYSTEMS.clear()
    FAKE_RESPONSE["text"] = text


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
    """Reload bot_engine with fake LLM injected so we always exercise the
    real _generate_story_caption implementation (not any leaked patch)."""
    import bot_engine as _be
    _install_fake_llm()
    _be = importlib.reload(_be)
    os.environ.setdefault("EMERGENT_LLM_KEY", "sk-test")
    return _be


def _wire_db(be):
    from motor.motor_asyncio import AsyncIOMotorClient
    aclient = AsyncIOMotorClient(MONGO_URL)
    be._db = aclient[DB_NAME]
    return aclient


PERSONA = {
    "display_name": "T Persona",
    "tone": "serio",
    "verbosity": "breve",
    "political_lean": "centro",
    "main_topic": "politica",
    "age": 30, "profession": "utente", "city": "Roma",
}


def _run(be, persona, feud):
    async def _r():
        _wire_db(be)
        return await be._generate_story_caption(persona, feud)
    return asyncio.run(_r())


# ═══ Task A: generic caption WITHOUT keyword hook → REJECTED ══════════

class TestAntiGenericFilter:
    """Caption <90 chars AND no keyword match → returns None."""

    @pytest.mark.parametrize("generic", [
        "questo mi piace",
        "veramente interessante",
        "ma quanto e' vero questo",
        "mi ritrovo in questo",
        "mi tocca il cuore",
    ])
    def test_generic_short_caption_is_rejected(self, be, generic):
        _reset(generic)
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result is None, (
            f"Generic short caption {generic!r} must be filtered to None; got {result!r}"
        )

    def test_caption_with_keyword_passes(self, be):
        """Caption containing party_a token 'meloni' must PASS."""
        _reset("Meloni ha ragione sul PNRR, e ora?")
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == "Meloni ha ragione sul PNRR, e ora?", (
            f"Caption with keyword must pass; got {result!r}"
        )

    def test_caption_with_title_keyword_passes(self, be):
        """Caption referencing title token 'PNRR' must PASS."""
        _reset("questo PNRR e' un pasticcio")
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == "questo PNRR e' un pasticcio", (
            f"Caption with title keyword must pass; got {result!r}"
        )


# ═══ Task B: long caption (>=90 chars) bypasses keyword check ═════════

class TestLongCaptionBypass:
    def test_long_caption_without_keyword_passes(self, be):
        # 100+ chars, no keyword from title/party_a/party_b
        long_caption = (
            "una riflessione molto articolata su una questione che mi tocca "
            "profondamente e su cui vorrei tornare"
        )
        assert len(long_caption) >= 90, f"test setup: caption too short ({len(long_caption)})"
        _reset(long_caption)
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == long_caption, (
            f"Long caption should bypass keyword check; got {result!r}"
        )

    def test_short_caption_no_keyword_no_bypass(self, be):
        # exactly < 90 chars and no keyword → must be filtered
        short = "una riflessione articolata su una questione"  # ~43 chars
        assert len(short) < 90
        _reset(short)
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result is None, (
            f"Short caption without keyword must be rejected; got {result!r}"
        )


# ═══ Task C: case + accent insensitivity (NFD normalisation) ══════════

class TestAccentAndCase:
    def test_case_insensitive_match(self, be):
        _reset("MELONI ha detto qualcosa")
        feud = {
            "title": "Meloni vs Schlein",
            "party_a": "meloni",  # lowercase in pool
            "party_b": "schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == "MELONI ha detto qualcosa"

    def test_accent_insensitive_match(self, be):
        """Caption 'perché è così importante?' on feud with party_a='Perché'
        must match after NFD accent stripping. Uses real Italian é/è."""
        caption = "perché è così importante ora?"
        _reset(caption)
        feud = {
            "title": "un titolo qualsiasi",
            "party_a": "Perché",  # accented in feud
            "party_b": "Perciò",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == caption, (
            f"Accented keyword match must work via NFD; got {result!r}"
        )


# ═══ Task D: Italian stopwords do NOT count as keywords ═══════════════

class TestStopwords:
    def test_questo_is_stopword_no_match(self, be):
        """Feud title contains 'Questo' as a token. A caption using
        'questo' MUST NOT satisfy the keyword requirement (stopword)."""
        _reset("questo e' fatto molto bene")  # 26 chars, all stopwords
        feud = {
            "title": "Questo e' un test",  # only >=4-char words: 'questo' (stopword), 'test' (4 chars, OK)
            "party_a": "",
            "party_b": "",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result is None, (
            f"Caption made of stopwords should NOT match 'questo' pool token; "
            f"got {result!r}"
        )

    def test_non_stopword_token_matches(self, be):
        """Same feud, but caption now references 'test' which is NOT a
        stopword and IS in pool (>=4 chars) → must pass."""
        _reset("il test dice altro")
        feud = {
            "title": "Questo e' un test",
            "party_a": "",
            "party_b": "",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result == "il test dice altro"


# ═══ Task E: iter153 refusal filter still runs BEFORE the anti-generic ═

class TestRefusalFilterStillWorks:
    @pytest.mark.parametrize("refusal", [
        "Non posso commentare senza il post di riferimento.",
        "Mi serve il post per rispondere",
        "Quale post stai condividendo?",
    ])
    def test_refusal_returns_none(self, be, refusal):
        _reset(refusal)
        feud = {
            "title": "Meloni vs Schlein sul PNRR",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        result = _run(be, PERSONA, feud)
        assert result is None, (
            f"Refusal must still be filtered; got {result!r}"
        )


# ═══ Task F: system prompt reinforcement (iter154 additions) ══════════

class TestSystemPromptStrengthened:
    def test_system_prompt_forbids_generic_phrases(self, be):
        """story_prompt_for must list the banned generic phrases."""
        from bot_personas import story_prompt_for
        sp = story_prompt_for(PERSONA).lower()
        # iter154 additions
        assert "vietate" in sp or "vietato" in sp, "no ban keyword"
        assert "mi piace" in sp, "must list 'mi piace' as banned"
        assert "interessante" in sp, "must list 'interessante' as banned"
        assert "120 caratteri" in sp, "max length must be 120 chars"

    def test_user_prompt_contains_ban_and_120_cap(self, be):
        _reset("Meloni ha ragione")
        feud = {
            "title": "Meloni vs Schlein",
            "party_a": "Meloni",
            "party_b": "Schlein",
            "category_label": "Politica",
            "category": "politica",
        }
        _run(be, PERSONA, feud)
        assert CAPTURED_PROMPTS
        up = CAPTURED_PROMPTS[0].lower()
        assert "120 caratteri" in up, f"user prompt must specify 120 char cap; got {up[:200]!r}"
        assert "vietato" in up or "vietate" in up, "user prompt must forbid generics"


# ═══ Task G: _bot_create_story integration ═══════════════════════════

class TestBotCreateStoryIntegration:
    def _pick(self, mongo):
        bot = mongo.users.find_one({"is_bot": True}, {"_id": 0})
        feud = mongo.feuds.find_one({"is_hidden": {"$ne": True}}, {"_id": 0})
        assert bot and feud
        return bot, feud

    def test_generic_caption_stored_as_empty(self, be, mongo):
        bot, feud = self._pick(mongo)
        # Clean any pre-existing story from this (bot, feud) and any recent
        mongo.stories.delete_many({"user_id": bot["user_id"]})
        _reset("questo mi piace")  # generic, no keyword → filter → None
        async def _r():
            _wire_db(be)
            await be._bot_create_story(bot, feud)
        try:
            asyncio.run(_r())
            doc = mongo.stories.find_one(
                {"user_id": bot["user_id"], "feud_id": feud["feud_id"]},
                {"_id": 0},
            )
            assert doc is not None, "story should still be created with empty caption"
            assert doc.get("comment", "") == "", (
                f"generic caption must be stored as empty; got {doc.get('comment')!r}"
            )
            assert doc.get("kind") == "feud"
        finally:
            mongo.stories.delete_many({"user_id": bot["user_id"]})

    def test_valid_caption_stored(self, be, mongo):
        bot, feud = self._pick(mongo)
        mongo.stories.delete_many({"user_id": bot["user_id"]})
        # Build a caption guaranteed to contain a keyword from feud
        # Prefer party_a token, fall back to first >=4-char title token.
        import re as _re
        _stop = {'della','delle','degli','sono','stato','stata','questo',
                 'questa','molto','ancora','anche','dopo','prima','tutti',
                 'contro','vero','bene','male','fatto','cosa','punto'}
        pool = f"{feud.get('title','')} {feud.get('party_a','')} {feud.get('party_b','')}"
        tokens = [t for t in _re.findall(r"[A-Za-zÀ-ÿ]+", pool) if len(t) >= 4 and t.lower() not in _stop]
        assert tokens, f"cannot pick a keyword from feud pool: {pool!r}"
        keyword = tokens[0]
        caption = f"secondo me {keyword} ha torto totalmente"
        _reset(caption)
        async def _r():
            _wire_db(be)
            await be._bot_create_story(bot, feud)
        try:
            asyncio.run(_r())
            doc = mongo.stories.find_one(
                {"user_id": bot["user_id"], "feud_id": feud["feud_id"]},
                {"_id": 0},
            )
            assert doc is not None
            assert doc.get("comment") == caption, (
                f"valid caption must be persisted verbatim; got {doc.get('comment')!r}"
            )
        finally:
            mongo.stories.delete_many({"user_id": bot["user_id"]})

    def test_anti_dup_same_bot_same_feud(self, be, mongo):
        """iter146 regression: at most 1 active story per (bot, feud)."""
        bot, feud = self._pick(mongo)
        mongo.stories.delete_many({"user_id": bot["user_id"]})
        _reset(f"secondo me {feud.get('party_a', 'test')} ha torto totalmente")
        async def _r():
            _wire_db(be)
            await be._bot_create_story(bot, feud)
            await be._bot_create_story(bot, feud)  # second call: must be no-op
        try:
            asyncio.run(_r())
            n = mongo.stories.count_documents(
                {"user_id": bot["user_id"], "feud_id": feud["feud_id"]}
            )
            assert n == 1, f"expected 1 story after dup call; got {n}"
        finally:
            mongo.stories.delete_many({"user_id": bot["user_id"]})

    def test_24h_quota_one_story_per_bot(self, be, mongo):
        """iter146 regression: max 1 story per bot per 24h."""
        bots = list(mongo.users.find({"is_bot": True}, {"_id": 0}).limit(2))
        feuds = list(mongo.feuds.find({"is_hidden": {"$ne": True}}, {"_id": 0}).limit(2))
        assert len(bots) >= 1 and len(feuds) >= 2
        bot = bots[0]
        mongo.stories.delete_many({"user_id": bot["user_id"]})
        # Insert one recent story manually
        now = datetime.now(timezone.utc)
        mongo.stories.insert_one({
            "story_id": f"story_test_{uuid.uuid4().hex[:8]}",
            "user_id": bot["user_id"],
            "kind": "feud",
            "feud_id": feuds[0]["feud_id"],
            "comment": "seed",
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
            "viewers": [],
        })
        _reset(f"secondo me {feuds[1].get('party_a', 'test')} ha ragione qui")
        async def _r():
            _wire_db(be)
            await be._bot_create_story(bot, feuds[1])
        try:
            asyncio.run(_r())
            n = mongo.stories.count_documents({"user_id": bot["user_id"]})
            assert n == 1, f"quota broken: expected 1 story for bot, got {n}"
        finally:
            mongo.stories.delete_many({"user_id": bot["user_id"]})


# ═══ Task H: HTTP regression ═════════════════════════════════════════

class TestHTTPRegression:
    def test_get_feuds(self, api):
        r = api.get(f"{BASE_URL}/api/feuds", timeout=15)
        assert r.status_code == 200, f"/api/feuds returned {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_anonymous_signup(self, api):
        nick = f"TESTiter154_{uuid.uuid4().hex[:8]}"
        r = api.post(
            f"{BASE_URL}/api/auth/anonymous",
            json={"nickname": nick},
            timeout=15,
        )
        assert r.status_code == 200, f"anon signup failed: {r.status_code} {r.text[:200]}"
        j = r.json()
        assert "token" in j or "access_token" in j or "user" in j

    def test_stories_feed(self, api):
        r = api.get(f"{BASE_URL}/api/stories/feed", timeout=15)
        # public endpoint may or may not require auth — accept 200 or 401
        assert r.status_code in (200, 401), (
            f"/api/stories/feed unexpected {r.status_code}: {r.text[:200]}"
        )
