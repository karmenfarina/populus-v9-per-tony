"""
Populus — Bot admin routes.

Endpoints (all under /api/admin/bots/*)
─────────────────────────────────────────────────────────────────────
  GET  /state             → current config + counts
  POST /toggle            → { enabled: bool }
  POST /count             → { count: int }
  POST /burst             → trigger an immediate activity burst

Auth: reuses the existing X-Admin-Key header (`require_admin`). The
frontend already carries that header once the founder unlocks the
admin panel.
"""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import bot_engine


class ToggleBody(BaseModel):
    enabled: bool


class CountBody(BaseModel):
    count: int = Field(ge=0, le=100)


class ResetBody(BaseModel):
    # Any subset of {"comments", "stories", "votes"}. Empty defaults to
    # comments+stories which is what the founder needs after a persona
    # rename to purge stale name snapshots.
    kinds: Optional[List[str]] = None


def build_bot_admin_router(require_admin) -> APIRouter:
    r = APIRouter(prefix="/admin/bots", tags=["bots"])

    @r.get("/state")
    async def state(_: bool = Depends(require_admin)):
        return await bot_engine.get_state()

    @r.post("/toggle")
    async def toggle(body: ToggleBody, _: bool = Depends(require_admin)):
        return await bot_engine.set_enabled(bool(body.enabled))

    @r.post("/count")
    async def count(body: CountBody, _: bool = Depends(require_admin)):
        return await bot_engine.set_active_count(int(body.count))

    @r.post("/burst")
    async def burst(_: bool = Depends(require_admin)):
        # Runs asynchronously; return current state immediately.
        import asyncio
        asyncio.create_task(bot_engine.run_initial_burst())
        return await bot_engine.get_state()

    @r.post("/reset")
    async def reset(body: ResetBody = Body(default_factory=ResetBody), _: bool = Depends(require_admin)):
        """Delete existing bot-authored comments/stories (and optionally votes).

        Payload: `{"kinds": ["comments", "stories"]}` — omitted → defaults
        to comments+stories. Passing `["comments","stories","votes"]`
        additionally rolls back feud vote counters and resets per-bot
        tallies.
        """
        kinds = body.kinds if body and body.kinds else ["comments", "stories"]
        # Whitelist to guard against typos.
        allowed = {"comments", "stories", "votes"}
        kinds = [k for k in kinds if k in allowed]
        return await bot_engine.reset_content(kinds)

    return r
