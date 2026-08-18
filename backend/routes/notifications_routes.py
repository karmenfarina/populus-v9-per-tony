"""
Populus — Notifications routes.

Endpoint feed notifiche in-app (inbox):
  GET  /api/notifications              → ultime 50, filtro blocchi
  GET  /api/notifications/unread-count → conteggio non lette
  POST /api/notifications/mark-read    → segna tutte come lette
  POST /api/notifications/{notif_id}/read → segna una come letta

Le notifiche vengono create da altri path (comment reply, mention,
hot_news, badge unlock, DM). Qui c'è solo la READ side dell'inbox.

Filtro blocchi: notifiche il cui `actor_id` è in `user_blocks` del viewer
NON compaiono (invariante "no public interaction").
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends

from helpers import now_utc, iso_utc as _iso_utc

logger = logging.getLogger("notifications_routes")


def build_notifications_router(
    db,
    get_current_user: Callable,
    blocked_ids_for: Callable,
) -> APIRouter:
    """Crea il router notifiche.

    Args:
      db: Motor client.
      get_current_user: FastAPI dep.
      blocked_ids_for: async `_blocked_ids_for(user_id) -> set[str]`.
    """
    r = APIRouter(tags=["notifications"])

    @r.get("/notifications")
    async def list_notifications(user: dict = Depends(get_current_user)):
        """Ultime 50 notifiche dell'utente, dalla più recente.

        Le notifiche autorate da utenti in blocco bidirezionale col viewer
        sono escluse (rispecchia "no public interaction" applicato altrove).
        """
        try:
            blocked = await blocked_ids_for(user["user_id"])
        except Exception as e:
            logger.warning(f"list_notifications block lookup failed: {e}")
            blocked = set()
        q: dict = {"user_id": user["user_id"]}
        if blocked:
            # `actor_id` è settato da _emit_notification per ogni evento social.
            # Notifiche legacy senza actor_id restano visibili (niente sensibile).
            q["$or"] = [
                {"actor_id": {"$exists": False}},
                {"actor_id": {"$nin": list(blocked)}},
            ]
        docs = (
            await db.notifications.find(q, {"_id": 0})
            .sort("created_at", -1)
            .to_list(50)
        )
        for d in docs:
            if isinstance(d.get("created_at"), datetime):
                d["created_at"] = _iso_utc(d["created_at"])
        return {"notifications": docs}

    @r.get("/notifications/unread-count")
    async def unread_count(user: dict = Depends(get_current_user)):
        """Conteggio non lette. Usa lo stesso filtro di /notifications per
        mantenere sincronizzati bell badge e lista renderizzata."""
        try:
            blocked = await blocked_ids_for(user["user_id"])
        except Exception:
            blocked = set()
        q: dict = {"user_id": user["user_id"], "read": False}
        if blocked:
            q["$or"] = [
                {"actor_id": {"$exists": False}},
                {"actor_id": {"$nin": list(blocked)}},
            ]
        n = await db.notifications.count_documents(q)
        return {"count": n}

    @r.post("/notifications/mark-read")
    async def mark_read(user: dict = Depends(get_current_user)):
        """Marca TUTTE le notifiche dell'utente come lette."""
        res = await db.notifications.update_many(
            {"user_id": user["user_id"], "read": False},
            {"$set": {"read": True, "read_at": now_utc()}},
        )
        return {"updated": res.modified_count}

    @r.post("/notifications/{notif_id}/read")
    async def mark_one_read(notif_id: str, user: dict = Depends(get_current_user)):
        """Marca una singola notifica come letta.

        Chiamato dallo schermo /notifications quando l'utente tappa una riga.
        Il frontend aggiorna già ottimisticamente il flag `read` in local
        state, questo endpoint rende persistente il cambio.
        """
        res = await db.notifications.update_one(
            {"notif_id": notif_id, "user_id": user["user_id"]},
            {"$set": {"read": True, "read_at": now_utc()}},
        )
        return {"updated": res.modified_count}

    return r
