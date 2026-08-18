"""
Populus — Support / Assistance routes.

Endpoint per l'invio di ticket di supporto:
  POST /api/support/submit

Il payload viene:
  1. Inviato via email al developer (via Resend API) con Reply-To
     impostato all'email dell'utente per risposta diretta.
  2. Archiviato su Mongo (`support_tickets`) per consultazione admin.

Requisiti env:
  - RESEND_API_KEY: chiave Resend per l'invio email.
  - SUPPORT_EMAIL: indirizzo destinatario dei ticket.

Regole di business:
  - Gli account anonimi NON possono inviare ticket (richiediamo un
    account reale per poter rispondere e per evitare spam).
"""
from __future__ import annotations

import html as html_lib
import logging
import os
from typing import Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException

from helpers import new_id, now_utc
from models import SupportBody

logger = logging.getLogger("support_routes")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")


def build_support_router(db, get_current_user: Callable) -> APIRouter:
    """Crea il router `/support/*`. Deps: db + auth."""
    r = APIRouter(tags=["support"])

    @r.post("/support/submit")
    async def support_submit(
        body: SupportBody, user: dict = Depends(get_current_user)
    ):
        """Form ticket multi-campo. Invia email al developer via Resend.

        Reply-To è impostato sull'email registrata dell'utente (o sul campo
        di contatto opzionale) così il developer può rispondere direttamente
        dalla propria casella.

        Gli account anonimi ricevono 403: serve un account reale sia per
        raggiungere l'utente sia per anti-spam.
        """
        is_anon = bool(user.get("is_anonymous")) or (
            user.get("auth_provider") == "anonymous"
        )
        if is_anon:
            raise HTTPException(
                status_code=403,
                detail="Devi registrarti con un account per inviare una richiesta di assistenza.",
            )

        if not RESEND_API_KEY or not SUPPORT_EMAIL:
            raise HTTPException(
                status_code=500,
                detail="Servizio email non configurato. Riprova più tardi.",
            )

        reply_to = (user.get("email") or (body.contact_email or "").strip()) or None
        provider = user.get("auth_provider") or (
            "anonymous" if user.get("is_anonymous") else "unknown"
        )

        def esc(v: str) -> str:
            return html_lib.escape(str(v or ""))

        reply_note = (
            ("(Reply-To impostato su " + reply_to + ")")
            if reply_to
            else (
                "— nessun contatto disponibile, l"
                + chr(0x2019)
                + " utente è anonimo senza email opzionale"
            )
        )
        html_body = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:640px;line-height:1.5">
          <h2 style="color:#F01A1A;border-bottom:2px solid #F01A1A;padding-bottom:6px">
            Populus — Nuova richiesta di assistenza
          </h2>
          <p><b>Categoria:</b> {esc(body.category)}<br>
             <b>Frequenza:</b> {esc(body.frequency)}<br>
             <b>Sezione app:</b> {esc(body.section)}</p>
          <h3>Descrizione</h3>
          <blockquote style="border-left:3px solid #ccc;padding-left:12px;color:#333;white-space:pre-wrap">{esc(body.description)}</blockquote>
          <hr>
          <h3>Identificativo utente</h3>
          <p><b>Nickname:</b> {esc(user.get('nickname', '-'))}<br>
             <b>User ID:</b> <code>{esc(user.get('user_id', '-'))}</code><br>
             <b>Auth provider:</b> {esc(provider)}<br>
             <b>Email registrata:</b> {esc(user.get('email') or '(nessuna)')}<br>
             <b>Email contatto (opzionale):</b> {esc(body.contact_email or '(non fornita)')}</p>
          <p style="font-size:12px;color:#888">
            Rispondi direttamente a questa email per contattare l&rsquo;utente
            {esc(reply_note)}.
          </p>
        </div>
        """.strip()

        payload: dict = {
            "from": "Populus Support <onboarding@resend.dev>",
            "to": [SUPPORT_EMAIL],
            "subject": f"[Populus] {body.category} — {user.get('nickname', 'utente')}",
            "html": html_body,
        }
        if reply_to:
            payload["reply_to"] = reply_to

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {RESEND_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        f"Resend error {resp.status_code}: {resp.text[:200]}"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail="Impossibile inviare la richiesta ora. Riprova tra qualche minuto.",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"support email error: {e}")
            raise HTTPException(
                status_code=502, detail="Servizio email non raggiungibile."
            )

        # Archivia il ticket su Mongo per consultazione admin.
        await db.support_tickets.insert_one(
            {
                "ticket_id": new_id("tkt"),
                "user_id": user.get("user_id"),
                "nickname": user.get("nickname"),
                "category": body.category,
                "frequency": body.frequency,
                "section": body.section,
                "description": body.description,
                "contact_email": body.contact_email,
                "created_at": now_utc(),
            }
        )
        return {"sent": True}

    return r
