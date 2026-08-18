"""
Populus — Legal & Docs routes.

Contiene endpoint di sola lettura per:
  - Termini di Servizio  →  GET /api/legal/terms
  - NDA (Non-Disclosure) →  GET /api/legal/nda
  - Documentazione dev   →  GET /api/docs, GET /api/docs/{slug}

Nessuno di questi endpoint tocca il DB: leggono file Markdown da disco
e li restituiscono al client. Sono quindi il candidato perfetto per un
router isolato senza dipendenze da `server.py`.

L'endpoint `POST /users/me/accept-terms` NON è qui perché:
  - richiede autenticazione utente
  - scrive sul DB (aggiorna `users.terms_accepted_*`)
  - Rimane in `server.py` finché non estraiamo `routes/users.py`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from legal_content import (
    TERMS_VERSION,
    NDA_VERSION,
    load_terms_text,
    load_nda_text,
)


# ─────────────────────────────────────────────────────────────────
# Developer documentation
# ─────────────────────────────────────────────────────────────────
# I file Markdown vivono in /app/docs/. Esporli come endpoint permette
# di consultarli con curl/wget o dal client mobile.
_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
_DOCS_MAP = {
    "regole": "POPULUS_REGOLE_APP.md",
    "algoritmo-ai": "POPULUS_ALGORITMO_AI.md",
    "architettura": "POPULUS_ARCHITETTURA.md",
}


def build_legal_router() -> APIRouter:
    """Crea il router con gli endpoint legali + docs. Nessuna dep."""
    r = APIRouter(tags=["legal", "docs"])

    # ─── Legal ────────────────────────────────────────────────────
    @r.get("/legal/terms")
    async def get_legal_terms():
        """Ritorna i Termini di Servizio correnti in Markdown.

        Endpoint pubblico usato sia in onboarding (schermata accettazione
        obbligatoria) sia da Impostazioni.
        """
        return {
            "version": TERMS_VERSION,
            "text": load_terms_text(),
            "updated_at": "2026-06-01",
        }

    @r.get("/legal/nda")
    async def get_legal_nda():
        """Ritorna l'NDA corrente in Markdown.

        Endpoint pubblico usato dalla schermata onboarding e da Impostazioni.
        """
        return {
            "version": NDA_VERSION,
            "text": load_nda_text(),
            "updated_at": "2026-02-01",
        }

    # ─── Developer docs ───────────────────────────────────────────
    @r.get("/docs")
    async def list_docs():
        """Elenco della documentazione sviluppatore disponibile."""
        return {
            "docs": [
                {"slug": slug, "filename": fname, "url": f"/api/docs/{slug}"}
                for slug, fname in _DOCS_MAP.items()
            ]
        }

    @r.get("/docs/{slug}")
    async def get_doc(slug: str):
        """Ritorna il contenuto Markdown del documento richiesto.

        Slug validi: `regole`, `algoritmo-ai`, `architettura`.
        """
        fname = _DOCS_MAP.get(slug)
        if not fname:
            raise HTTPException(status_code=404, detail="Documento non trovato")
        try:
            text = (_DOCS_DIR / fname).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File documento mancante")
        return {"slug": slug, "filename": fname, "text": text}

    return r
