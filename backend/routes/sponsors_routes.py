"""
Populus — Sponsors module.

Contiene:
  • SEED_SPONSORS  → sponsor iniziali (uno per categoria).
  • seed_sponsors_if_empty(db) → upsert idempotente al boot.
  • build_sponsors_router(db) → GET /api/sponsors.

Modello dati (collezione `sponsors`):
    { sponsor_id, category, sponsor, headline, cta, image_url, created_at }

Endpoint pubblico, senza auth. Filtra opzionalmente per categoria via query string.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter

from helpers import new_id, now_utc

logger = logging.getLogger("sponsors")


SEED_SPONSORS = [
    {"category": "politica", "sponsor": "IlPost", "headline": "Approfondimenti quotidiani sulla politica.", "cta": "ABBONATI", "image_url": "https://images.unsplash.com/photo-1541872703-74c5e44368f6?w=800"},
    {"category": "tv", "sponsor": "Infinity+", "headline": "Rivedi ogni puntata del reality del momento.", "cta": "GUARDA ORA", "image_url": "https://images.unsplash.com/photo-1585951237318-9ea5e175b891?w=800"},
    {"category": "musica", "sponsor": "Spotify", "headline": "La playlist ufficiale della faida.", "cta": "ASCOLTA", "image_url": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=800"},
    {"category": "sport", "sponsor": "DAZN", "headline": "Rivedi il derby integrale con moviola.", "cta": "REPLAY", "image_url": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=800"},
    {"category": "cinema", "sponsor": "Netflix", "headline": "Il film della polemica: guardalo stasera.", "cta": "GUARDA", "image_url": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800"},
    {"category": "social", "sponsor": "TrendReport", "headline": "Analisi virali ogni 24 ore.", "cta": "ISCRIVITI", "image_url": "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=800"},
    {"category": "gossip", "sponsor": "Chi Magazine", "headline": "Tutti i retroscena in edicola.", "cta": "SFOGLIA", "image_url": "https://images.unsplash.com/photo-1561890244-e880c1e6d54e?w=800"},
    {"category": "tech", "sponsor": "Amazon Prime Day", "headline": "Le offerte tech del giorno, prima di tutti.", "cta": "SCOPRI", "image_url": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800"},
    {"category": "cronaca", "sponsor": "Cronache Italia", "headline": "Cronaca nera e casi mai risolti: inchieste in edicola.", "cta": "LEGGI", "image_url": "https://images.unsplash.com/photo-1495556650867-99590cea3657?w=800"},
]


async def seed_sponsors_if_empty(db) -> None:
    """Upsert one seed sponsor per category. Idempotent, safe at every boot."""
    for s in SEED_SPONSORS:
        existing = await db.sponsors.find_one({"category": s["category"]})
        if not existing:
            await db.sponsors.insert_one(
                {"sponsor_id": new_id("spo"), **s, "created_at": now_utc()}
            )
            logger.info(f"Seeded sponsor for category {s['category']}")


def build_sponsors_router(db) -> APIRouter:
    """Crea il router `/sponsors`. Riceve `db` come dep injection."""
    r = APIRouter(tags=["sponsors"])

    @r.get("/sponsors")
    async def get_sponsors(category: Optional[str] = None):
        q: dict = {}
        if category and category != "all":
            q["category"] = category
        docs = await db.sponsors.find(q, {"_id": 0}).to_list(50)
        return {"sponsors": docs}

    return r
