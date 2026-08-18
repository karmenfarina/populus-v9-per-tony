"""
Populus — Reset "Day One" script
==================================================================

Ripristina il database ad uno stato "vergine" come al giorno 1 di lancio,
MA PRESERVA:
  - L'account admin/founder (email in FOUNDER_ADMIN_EMAIL).
  - I 100 bot fleet (is_bot=True).
  - Gli sponsors.
  - La configurazione runtime (system_meta: bot_config, ecc.).
  - Schema e indici delle collezioni.

Cancella:
  - Tutti gli utenti umani non-founder e le loro sessioni/foto/verification.
  - Tutte le faide, voti, commenti, risposte, preferiti, view.
  - Tutte le storie e i relativi metadata.
  - Tutti i DM, conversazioni, blocchi, segnalazioni, report.
  - Tutte le notifiche, badge, ticket, moderazione.

USO:
    cd /app/backend && python scripts/reset_to_day_one.py

Il comando chiede conferma esplicita prima di eseguire.
==================================================================
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Rendi importabili i moduli backend anche eseguendo da /app/backend
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

FOUNDER_ADMIN_EMAIL = "carlofarinapayme@gmail.com"

# Collezioni che vengono SVUOTATE COMPLETAMENTE.
FULL_WIPE_COLLECTIONS = [
    "feuds",
    "votes",
    "comments",
    "replies",
    "favorites",
    "feud_views",
    "notifications",
    "notification_locks",
    "badge_notifications",
    "stories",
    "messages",
    "conversations",
    "user_blocks",
    "user_reports",
    "support_tickets",
    "flagged_comments",
    "friendships",
    "verification_tokens",
    "user_photos",   # foto profilo — anche quelle admin: verranno ricreate al primo login
    "user_sessions",
]

# Collezioni PRESERVATE (non toccate).
PRESERVED_COLLECTIONS = [
    "sponsors",     # sponsor a rotazione
    "system_meta",  # config runtime (bot_config, ecc.)
]


async def reset(db, *, dry_run: bool = False) -> dict:
    """Esegue il reset. Ritorna un report riassuntivo."""
    report: dict = {"deleted": {}, "preserved_users": 0, "preserved_bots": 0}

    # 1. Identifica gli utenti da PRESERVARE (admin + bot).
    admin = await db.users.find_one(
        {"email": FOUNDER_ADMIN_EMAIL}, {"_id": 0, "user_id": 1, "email": 1, "nickname": 1}
    )
    if not admin:
        print(f"⚠️  Nessun account founder trovato con email {FOUNDER_ADMIN_EMAIL}")
        print("    Lo script continuerà comunque, ma dovrai ricreare l'account admin.")

    bots_count = await db.users.count_documents({"is_bot": True})

    print("─" * 60)
    print("PIANO DI RESET:")
    print("─" * 60)
    print(f"  ✅ PRESERVA admin:  {admin}")
    print(f"  ✅ PRESERVA bots:   {bots_count} (is_bot=True)")
    print(f"  ✅ PRESERVA sponsors, system_meta, schema e indici")
    print()
    print("  🗑️  ELIMINA (per intero):")
    for c in FULL_WIPE_COLLECTIONS:
        n = await db[c].count_documents({})
        print(f"      - {c:<24} {n:>6} doc")
    print(f"  🗑️  ELIMINA users non-admin non-bot:")
    users_to_delete = await db.users.count_documents(
        {
            "$and": [
                {"email": {"$ne": FOUNDER_ADMIN_EMAIL}},
                {"is_bot": {"$ne": True}},
            ]
        }
    )
    print(f"      - users                    {users_to_delete:>6} doc")
    print("─" * 60)

    if dry_run:
        print("DRY-RUN: nessuna modifica applicata.")
        return report

    # 2. Wipe collezioni "intere".
    for c in FULL_WIPE_COLLECTIONS:
        res = await db[c].delete_many({})
        report["deleted"][c] = int(getattr(res, "deleted_count", 0) or 0)

    # 3. Rimuovi utenti non-admin non-bot.
    res_u = await db.users.delete_many(
        {
            "$and": [
                {"email": {"$ne": FOUNDER_ADMIN_EMAIL}},
                {"is_bot": {"$ne": True}},
            ]
        }
    )
    report["deleted"]["users"] = int(getattr(res_u, "deleted_count", 0) or 0)

    # 4. Reset campi engagement/counters sull'admin (torna "vergine").
    if admin:
        await db.users.update_one(
            {"email": FOUNDER_ADMIN_EMAIL},
            {
                "$set": {
                    "total_votes": 0,
                    "majority_votes": 0,
                    "minority_votes": 0,
                    "category_votes": {},
                    "category_badges_seen": [],
                    "badge_type": None,
                },
                "$unset": {
                    "last_seen_at": "",
                },
            },
        )

    # 5. Reset counters/state sui bot: mantieni identità ma azzera engagement history.
    await db.users.update_many(
        {"is_bot": True},
        {
            "$set": {
                "total_votes": 0,
                "majority_votes": 0,
                "minority_votes": 0,
                "category_votes": {},
            }
        },
    )

    # 6. Conteggi finali.
    report["preserved_users"] = await db.users.count_documents(
        {"email": FOUNDER_ADMIN_EMAIL}
    )
    report["preserved_bots"] = await db.users.count_documents({"is_bot": True})

    return report


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("❌ MONGO_URL o DB_NAME mancanti in .env")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    force = "--yes" in sys.argv or "-y" in sys.argv

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    if not dry_run and not force:
        # Prima mostra il piano in dry-run, poi chiedi conferma.
        await reset(db, dry_run=True)
        print()
        answer = input("⚠️  Confermi il RESET completo? (scrivi 'RESET' per procedere): ")
        if answer.strip() != "RESET":
            print("Reset annullato.")
            return

    print()
    print("🔄 Esecuzione reset in corso...")
    report = await reset(db, dry_run=dry_run)
    print()
    if dry_run:
        return
    print("─" * 60)
    print("✅ RESET COMPLETATO")
    print("─" * 60)
    print("Documenti eliminati per collezione:")
    for k, v in report["deleted"].items():
        print(f"  - {k:<24} {v:>6}")
    print()
    print(f"Utenti preservati (admin): {report['preserved_users']}")
    print(f"Bot preservati:            {report['preserved_bots']}")
    print()
    print("💡 Prossimi step consigliati:")
    print("   1. Riavvia il backend:  sudo supervisorctl restart backend")
    print("   2. Lo scheduler AI genererà nuove faide entro 10-20 minuti.")
    print("   3. Il pannello admin è raggiungibile con l'account founder invariato.")


if __name__ == "__main__":
    asyncio.run(main())
