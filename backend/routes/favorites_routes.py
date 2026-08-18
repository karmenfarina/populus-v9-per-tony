"""
Populus — Favorites routes.

Endpoint per gestire i "preferiti" utente:
  POST   /api/feuds/{feud_id}/favorite  → aggiunge (idempotente, bump created_at)
  DELETE /api/feuds/{feud_id}/favorite  → rimuove (no-op se assente)
  GET    /api/favorites                 → lista preferiti, hydrated con voto utente

Nota architetturale:
  Gli helper `_is_favorited` e `_favorite_ids_for` restano in `server.py`
  perché usati da altri endpoint (feuds/{id}, hype). Solo gli endpoint
  REST specifici per la gestione dei preferiti sono qui.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from helpers import now_utc, iso_utc as _iso_utc


def build_favorites_router(
    db,
    get_current_user: Callable,
    log_event: Callable,
    evt_favorite_added: str,
    user_voted_ids: Callable,
    attach_percentages: Callable,
) -> APIRouter:
    """Crea il router `/favorites`.

    Args:
      db: Motor async client (esposto da server.py).
      get_current_user: dep FastAPI per estrarre l'utente autenticato.
      log_event: `analytics.log_event` async fn.
      evt_favorite_added: costante evento analytics.
      user_voted_ids: helper `_user_voted_ids(user_id, feud_ids) -> dict`.
      attach_percentages: helper `_attach_percentages(feud, revealed)` (in-place).
    """
    r = APIRouter(tags=["favorites"])

    @r.post("/feuds/{feud_id}/favorite")
    async def add_favorite(feud_id: str, user: dict = Depends(get_current_user)):
        """Aggiunge la faida ai preferiti dell'utente.

        Idempotente: se già presente aggiorna `created_at` così il ri-favorite
        porta la voce in cima alla lista (ordinamento cronologico).
        """
        f = await db.feuds.find_one({"feud_id": feud_id}, {"_id": 0, "feud_id": 1})
        if not f:
            raise HTTPException(status_code=404, detail="Faida non trovata")
        await db.favorites.update_one(
            {"user_id": user["user_id"], "feud_id": feud_id},
            {
                "$setOnInsert": {"user_id": user["user_id"], "feud_id": feud_id},
                "$set": {"created_at": now_utc()},
            },
            upsert=True,
        )
        asyncio.create_task(
            log_event(db, user["user_id"], evt_favorite_added, feud_id=feud_id)
        )
        return {"ok": True, "is_favorite": True}

    @r.delete("/feuds/{feud_id}/favorite")
    async def remove_favorite(feud_id: str, user: dict = Depends(get_current_user)):
        """Rimuove la faida dai preferiti (no-op se non presente)."""
        await db.favorites.delete_one(
            {"user_id": user["user_id"], "feud_id": feud_id}
        )
        return {"ok": True, "is_favorite": False}

    @r.get("/favorites")
    async def list_favorites(user: dict = Depends(get_current_user)):
        """Lista i preferiti dell'utente, dal più recente al meno recente.

        Le faide purgate dopo 14gg (`FEUD_RETENTION_DAYS`) vengono
        silenziosamente escluse: il client non vede riferimenti "morti".
        """
        fav_docs = (
            await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0})
            .sort("created_at", -1)
            .to_list(500)
        )
        if not fav_docs:
            return {"feuds": []}
        order = {d["feud_id"]: i for i, d in enumerate(fav_docs)}
        feud_ids = list(order.keys())
        feuds = await db.feuds.find(
            {"feud_id": {"$in": feud_ids}, "is_hidden": {"$ne": True}}, {"_id": 0}
        ).to_list(len(feud_ids))
        # Ripristina ordine (più recente prima).
        feuds.sort(key=lambda f: order.get(f["feud_id"], 10**9))
        voted_map = await user_voted_ids(user["user_id"], [f["feud_id"] for f in feuds])
        for d in feuds:
            my_vote = voted_map.get(d["feud_id"])
            attach_percentages(d, revealed=bool(my_vote))
            d["my_vote"] = my_vote
            d["is_favorite"] = True
            if isinstance(d.get("created_at"), datetime):
                d["created_at"] = _iso_utc(d["created_at"])
        return {"feuds": feuds}

    return r
