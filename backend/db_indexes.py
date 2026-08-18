"""
Populus — Ensure DB Indexes.

Modulo idempotente che crea tutti gli indici necessari per la scalabilità
delle query dell'app. Chiamato al boot del backend.

Ogni indice qui è motivato da una query "hot" nel codice:
- Se aggiungi una nuova query hot, aggiungi qui il relativo indice.
- Se rimuovi una query, l'indice può restare (costa poco).

`create_index` di MongoDB è idempotente: se l'indice esiste già viene
ignorato. Sicuro chiamare a ogni boot.

Filosofia:
  • Query su singolo campo con filtro esatto → indice singolo.
  • Query con filtro + sort → indice composto nella stessa direzione del sort.
  • Query con TTL naturale (expires_at) → indice TTL per auto-purge.
"""
from __future__ import annotations

import logging

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger("db_indexes")


async def ensure_indexes(db) -> dict:
    """Crea tutti gli indici. Ritorna un report con conteggi per collezione."""
    report: dict[str, list[str]] = {}

    async def _mk(coll: str, keys, **kw):
        """Wrapper che ignora errori (idempotente) e traccia il nome."""
        try:
            name = await db[coll].create_index(keys, **kw)
            report.setdefault(coll, []).append(name)
        except Exception as e:
            logger.warning(f"ensure_indexes[{coll}] failed on {keys}: {e}")

    # ─── users ──────────────────────────────────────────────────────
    # email/user_id/nickname unicità + lookup nickname veloce.
    await _mk("users", [("nickname", ASCENDING)])
    # Ricerca amici / suggerimenti circle con filtro provider/anon.
    await _mk("users", [("auth_provider", ASCENDING)])

    # ─── feuds ──────────────────────────────────────────────────────
    # Feed live: sort per created_at desc con filtri category/is_hidden.
    await _mk("feuds", [("created_at", DESCENDING)])
    await _mk("feuds", [("is_hidden", ASCENDING), ("created_at", DESCENDING)])
    await _mk("feuds", [("category", ASCENDING), ("created_at", DESCENDING)])
    await _mk("feuds", [("source", ASCENDING), ("category", ASCENDING), ("created_at", DESCENDING)])
    # Hot news trigger: fanout guardato da hot_notified.
    await _mk("feuds", [("hot_notified", ASCENDING)])

    # ─── votes ──────────────────────────────────────────────────────
    # Cronologia voti utente: filter user_id + sort created_at.
    await _mk("votes", [("user_id", ASCENDING), ("created_at", DESCENDING)])

    # ─── comments ───────────────────────────────────────────────────
    # Lista commenti per faida ordinati.
    await _mk("comments", [("feud_id", ASCENDING), ("created_at", DESCENDING)])
    # Delete cascade da user block filter (join).
    await _mk("comments", [("user_id", ASCENDING)])

    # ─── replies ────────────────────────────────────────────────────
    await _mk("replies", [("comment_id", ASCENDING), ("created_at", ASCENDING)])
    await _mk("replies", [("user_id", ASCENDING)])

    # ─── notifications ──────────────────────────────────────────────
    # Query principale: user_id + sort created_at (feed inbox).
    await _mk("notifications", [("user_id", ASCENDING), ("created_at", DESCENDING)])
    # Unread count: user_id + read (filter partial).
    await _mk("notifications", [("user_id", ASCENDING), ("read", ASCENDING)])
    # Block filter join con actor_id.
    await _mk("notifications", [("user_id", ASCENDING), ("actor_id", ASCENDING)])

    # ─── notification_locks ─────────────────────────────────────────
    # Anti-duplicati: upsert su `key`. Indice unico per prevenire race.
    await _mk("notification_locks", [("key", ASCENDING)], unique=True)
    # TTL: auto-purge dopo 48h (lock validi per ~24h + buffer).
    await _mk("notification_locks", [("created_at", ASCENDING)], expireAfterSeconds=48 * 3600)

    # ─── messages ───────────────────────────────────────────────────
    # Query per conversazione: pair {sender/recipient} + sort created_at.
    await _mk("messages", [("sender_id", ASCENDING), ("recipient_id", ASCENDING), ("created_at", DESCENDING)])
    await _mk("messages", [("recipient_id", ASCENDING), ("created_at", DESCENDING)])
    # Unread count destinatario.
    await _mk("messages", [("recipient_id", ASCENDING), ("read", ASCENDING)])

    # ─── conversations ──────────────────────────────────────────────
    # Lista conversazioni per utente ordinata per last_activity.
    await _mk("conversations", [("participants", ASCENDING), ("last_activity", DESCENDING)])
    # Lookup diretto per pair_id se presente.
    await _mk("conversations", [("pair_id", ASCENDING)])

    # ─── stories ────────────────────────────────────────────────────
    # Feed storie: filter user_id + sort created_at.
    # NB: TTL su expires_at è già gestito in on_startup (indice `expires_at_1`).
    await _mk("stories", [("user_id", ASCENDING), ("created_at", DESCENDING)])

    # ─── friendships ────────────────────────────────────────────────
    # Cerchia: query per user_id/friend_id in entrambe le direzioni.
    await _mk("friendships", [("user_id", ASCENDING), ("friend_id", ASCENDING)], unique=True)
    await _mk("friendships", [("friend_id", ASCENDING)])

    # ─── user_blocks ────────────────────────────────────────────────
    # Filter block su ogni feed/comment/DM. Unique previene duplicati.
    await _mk("user_blocks", [("blocker_id", ASCENDING), ("blocked_id", ASCENDING)], unique=True)
    await _mk("user_blocks", [("blocked_id", ASCENDING)])

    # ─── user_reports ───────────────────────────────────────────────
    # Admin review list ordinata cronologicamente.
    await _mk("user_reports", [("created_at", DESCENDING)])
    await _mk("user_reports", [("reported_id", ASCENDING), ("created_at", DESCENDING)])

    # ─── feud_views ─────────────────────────────────────────────────
    # Analytics view rollup: filter feud_id + user_id.
    await _mk("feud_views", [("feud_id", ASCENDING)])
    await _mk("feud_views", [("user_id", ASCENDING), ("viewed_at", DESCENDING)])

    # ─── favorites ──────────────────────────────────────────────────
    # Già indicizzato da server.py (user_id+feud_id, user_id+created_at).

    # ─── verification_tokens ────────────────────────────────────────
    # TTL su expires_at già gestito in on_startup (indice `expires_at_1`).
    await _mk("verification_tokens", [("user_id", ASCENDING)])

    # ─── flagged_comments ───────────────────────────────────────────
    await _mk("flagged_comments", [("created_at", DESCENDING)])
    await _mk("flagged_comments", [("comment_id", ASCENDING)])

    # ─── badge_notifications ────────────────────────────────────────
    await _mk("badge_notifications", [("user_id", ASCENDING), ("created_at", DESCENDING)])

    # ─── user_photos ────────────────────────────────────────────────
    # Già indicizzato (user_id, user_id+position).

    # ─── support_tickets ────────────────────────────────────────────
    await _mk("support_tickets", [("created_at", DESCENDING)])
    await _mk("support_tickets", [("user_id", ASCENDING), ("created_at", DESCENDING)])

    # ─── sponsors ───────────────────────────────────────────────────
    await _mk("sponsors", [("category", ASCENDING)])

    # ─── system_meta ────────────────────────────────────────────────
    # Lookup key esatto (upsert su config).
    await _mk("system_meta", [("key", ASCENDING)], unique=True)

    total = sum(len(v) for v in report.values())
    logger.info(f"ensure_indexes: {total} indexes ensured across {len(report)} collections")
    return report
