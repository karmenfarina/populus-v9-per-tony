"""
Populus — Legal content loaders.

Contiene le costanti di versione e i loader dei documenti legali
(Termini di Servizio + NDA). Isolati qui perché usati sia:
  - dagli endpoint pubblici (`routes/legal.py`) che restituiscono il MD
  - dall'endpoint `/users/me/accept-terms` (in `server.py`) che confronta
    la versione accettata dall'utente con quella corrente
  - da `/auth/me` che espone i flag `terms_accepted` / `nda_accepted`

Bump della versione ⇒ tutti gli utenti dovranno riaccettare al prossimo login.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("legal_content")

_ROOT_DIR = Path(__file__).parent
_LEGAL_DIR = _ROOT_DIR / "legal"

# ─────────────────────────────────────────────────────────────────
# Versioning
# ─────────────────────────────────────────────────────────────────
TERMS_VERSION = "v1"
NDA_VERSION = "v1"

_TERMS_PATH = _LEGAL_DIR / f"terms_{TERMS_VERSION}.md"
_NDA_PATH = _LEGAL_DIR / f"nda_{NDA_VERSION}.md"

# In-memory cache (single-process). Miss ⇒ ri-legge dal disco al prossimo call
# ⇒ ridistribuzione del container aggiorna il testo senza restart.
_TERMS_CACHE: dict = {"text": None}
_NDA_CACHE: dict = {"text": None}


def load_terms_text() -> str:
    """Legge (e memoizza) il markdown dei Termini di Servizio."""
    cached = _TERMS_CACHE.get("text")
    if cached:
        return cached
    try:
        text = _TERMS_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"terms file not readable at {_TERMS_PATH}: {e}")
        text = "Termini di Servizio non disponibili al momento. Riprova più tardi."
    _TERMS_CACHE["text"] = text
    return text


def load_nda_text() -> str:
    """Legge (e memoizza) il markdown dell'NDA."""
    cached = _NDA_CACHE.get("text")
    if cached:
        return cached
    try:
        text = _NDA_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"nda file not readable at {_NDA_PATH}: {e}")
        text = "Accordo di Riservatezza non disponibile al momento. Riprova più tardi."
    _NDA_CACHE["text"] = text
    return text
