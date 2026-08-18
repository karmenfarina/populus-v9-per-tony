"""
Populus — Block / Report routes.

Gestione dei blocchi utente-utente e delle segnalazioni:
  POST   /api/users/{user_id}/block    → blocca (idempotente, cascade friendships)
  DELETE /api/users/{user_id}/block    → sblocca
  GET    /api/users/me/blocks          → lista utenti bloccati (con mini profile)
  POST   /api/users/{user_id}/report   → segnala un utente

Semantica del block:
  - Impedisce DM in entrambe le direzioni.
  - Nasconde commenti/risposte del bloccato dall'esperienza del bloccante.
  - Rompe la relazione di Cerchia esistente in entrambi i sensi.
  - Rimuove il target dalle `story_hidden_viewers` (diventano moot).
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from helpers import new_id, now_utc, iso_utc as _iso_utc
from models import ReportUserBody

logger = logging.getLogger("blocks_routes")


def build_blocks_router(
    db,
    get_current_user: Callable,
    mini_user: Callable,
) -> APIRouter:
    """Crea il router blocks/reports.

    Args:
      db: Motor client.
      get_current_user: FastAPI dep.
      mini_user: helper async `_mini_user(user_id) -> dict` per hydrate lista blocchi.
    """
    r = APIRouter(tags=["blocks"])

    @r.post("/users/{user_id}/block")
    async def block_user(user_id: str, user: dict = Depends(get_current_user)):
        """Blocca `user_id`. Cascade: rompe la Cerchia in entrambi i sensi."""
        if user_id == user["user_id"]:
            raise HTTPException(
                status_code=400, detail="Non puoi bloccare te stesso"
            )
        target = await db.users.find_one(
            {"user_id": user_id}, {"_id": 0, "user_id": 1}
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        await db.user_blocks.update_one(
            {"blocker_id": user["user_id"], "blocked_id": user_id},
            {
                "$setOnInsert": {
                    "blocker_id": user["user_id"],
                    "blocked_id": user_id,
                    "created_at": now_utc(),
                }
            },
            upsert=True,
        )
        # Cascade: rimuovi eventuali amicizie in entrambe le direzioni così
        # nessuna delle due parti vede l'altra in circle/story/ranking.
        # Rispecchia l'invariante "no public interaction" applicato in
        # commenti/reply/mention altrove.
        try:
            await db.friendships.delete_many(
                {
                    "$or": [
                        {"user_id": user["user_id"], "friend_id": user_id},
                        {"user_id": user_id, "friend_id": user["user_id"]},
                    ]
                }
            )
        except Exception as e:
            logger.warning(f"block_user: friendship cascade delete failed: {e}")
        # Rimuovi il target da eventuali story_hidden_viewers — diventa moot
        # una volta rotta l'amicizia.
        try:
            await db.users.update_one(
                {"user_id": user["user_id"]},
                {"$pull": {"story_hidden_viewers": user_id}},
            )
        except Exception:
            pass
        return {"ok": True, "blocked": True}

    @r.delete("/users/{user_id}/block")
    async def unblock_user(user_id: str, user: dict = Depends(get_current_user)):
        """Sblocca `user_id`. No-op se non era bloccato."""
        await db.user_blocks.delete_one(
            {"blocker_id": user["user_id"], "blocked_id": user_id}
        )
        return {"ok": True, "blocked": False}

    @r.get("/users/me/blocks")
    async def my_blocks(user: dict = Depends(get_current_user)):
        """Lista degli utenti bloccati dall'utente corrente (hydrated con mini profile)."""
        docs = await db.user_blocks.find(
            {"blocker_id": user["user_id"]}, {"_id": 0}
        ).to_list(500)
        users_list = []
        for d in docs:
            mini = await mini_user(d["blocked_id"])
            users_list.append(
                {
                    **mini,
                    "blocked_at": _iso_utc(d["created_at"])
                    if d.get("created_at")
                    else None,
                }
            )
        return {"blocked_users": users_list}

    @r.post("/users/{user_id}/report")
    async def report_user(
        user_id: str,
        body: ReportUserBody,
        user: dict = Depends(get_current_user),
    ):
        """Segnala un utente. Il report finisce in `user_reports` per review admin."""
        if user_id == user["user_id"]:
            raise HTTPException(
                status_code=400, detail="Non puoi segnalare te stesso"
            )
        target = await db.users.find_one(
            {"user_id": user_id}, {"_id": 0, "user_id": 1}
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        await db.user_reports.insert_one(
            {
                "report_id": new_id("rep"),
                "reporter_id": user["user_id"],
                "reported_id": user_id,
                "reason": body.reason[:500],
                "message_id": body.message_id,
                "created_at": now_utc(),
            }
        )
        return {"ok": True}

    return r
