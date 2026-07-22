"""
Text moderation helpers — extracted from server.py.

Two-layer moderation:
  1. `_moderate_text` — deterministic keyword blocklist for slurs, hate
     speech and threats. Runs synchronously and returns the flagged
     tokens (if any) so the caller can 400 the request.
  2. `_ai_moderate_comment` — LLM-based moderation (Claude Haiku 4.5)
     that catches paraphrased slurs, coded language and calls to harm
     the keyword layer misses. Fails OPEN (returns SAFE) on any
     provider error so the app doesn't hard-fail moderation when the
     LLM is briefly unavailable — the keyword filter already ran.

Both layers are pure with respect to database state — audit logging
lives in server.py where the Mongo client is defined.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


BLOCKED_WORDS = {
    # Slurs + hate — keep this list conservative but non-empty for MVP
    'negro', 'frocio', 'finocchio', 'terrone', 'zingaro', 'ebreo di merda',
    'checca', 'ricchione', 'crucco', 'polentone', 'marocchino di merda',
    # Common Italian profanity/insults (strong)
    'vaffanculo', 'stronzo', 'stronza', 'coglione', 'coglioni', 'puttana', 'troia',
    'bastardo', 'bastarda', 'cazzo', 'cazzone', 'merda', 'porco dio', 'porca madonna',
    'figlio di puttana', 'figlia di puttana', 'mongoloide', 'ritardato', 'handicappato',
    'idiota di merda', 'schifoso', 'sfigato',
    # Threats
    'ti ammazzo', 'ti uccido', 'devi morire',
}


def moderate_text(text: str) -> tuple[str, list[str]]:
    """Return (cleaned_text, flagged_words). If flagged non-empty, caller should reject."""
    original = (text or '').strip()
    if not original:
        return original, ['vuoto']
    lower = original.lower()
    hits = []
    for word in BLOCKED_WORDS:
        # Match whole substring; use \b when word is single token to avoid
        # false positives on unrelated substrings.
        if ' ' in word:
            if word in lower:
                hits.append(word)
        else:
            if re.search(r'\b' + re.escape(word) + r'\b', lower):
                hits.append(word)
    return original, hits


async def ai_moderate_comment(text: str) -> tuple[bool, Optional[str]]:
    """AI-based moderation — catches hate speech, threats and violence
    incitement that the keyword filter misses (paraphrased slurs, coded
    language, insinuations, calls to harm etc.).

    Returns (is_safe, reason). `reason` is a short Italian label for the
    audit log when the text is unsafe.

    Uses Claude Haiku 4.5 via emergentintegrations for latency (< 1s) and
    cost. Falls back to `is_safe=True` on any provider error so the app
    doesn't hard-fail moderation when the LLM is down — the keyword
    filter already ran and caught the low-hanging fruit.
    """
    emergent_llm_key = os.environ.get('EMERGENT_LLM_KEY', '')
    if not emergent_llm_key:
        return True, None
    original = (text or '').strip()
    if not original or len(original) < 3:
        return True, None
    # Cap payload to keep latency low. Long rants are truncated but the
    # first 800 chars are more than enough for a hate/violence classifier.
    payload = original[:800]
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        logger.warning(f"ai-moderation: emergentintegrations import failed: {e}")
        return True, None
    try:
        chat = LlmChat(
            api_key=emergent_llm_key,
            session_id=f"mod-mod_{uuid.uuid4().hex[:12]}",
            system_message=(
                "Sei un moderatore di contenuti per una community italiana. "
                "Il tuo compito: classificare un commento come SAFE o UNSAFE. "
                "UNSAFE se e solo se contiene una di queste cose: "
                "(1) hate speech verso una categoria protetta (razza, etnia, "
                "religione, orientamento sessuale, identità di genere, disabilità); "
                "(2) minaccia diretta o indiretta a una persona; "
                "(3) incitamento alla violenza o al danno fisico/psicologico; "
                "(4) molestia o doxxing (rivelazione di dati privati). "
                "Le opinioni forti, la critica politica anche aspra, la satira, "
                "il turpiloquio generico e i toni polemici sono SAFE. "
                "Rispondi ESCLUSIVAMENTE con una riga in questo formato: "
                "SAFE oppure UNSAFE|<categoria breve>. "
                "Esempi validi di risposta UNSAFE: "
                "UNSAFE|hate_speech, UNSAFE|minaccia, UNSAFE|incitamento_violenza."
            ),
        ).with_model('anthropic', 'claude-haiku-4-5-20251001')
        reply = await chat.send_message(UserMessage(text=payload))
        raw = (str(reply) if reply is not None else '').strip().upper()
        if raw.startswith('UNSAFE'):
            # Extract the short category after the pipe.
            parts = raw.split('|', 1)
            reason = parts[1].strip().lower() if len(parts) > 1 else 'unsafe'
            return False, reason
        return True, None
    except Exception as e:
        logger.warning(f"ai-moderation failed (allow-listing text): {e}")
        return True, None
