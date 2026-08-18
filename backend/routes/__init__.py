"""
Populus — Backend routes.

Router modulari estratti da `server.py` per migliorare la manutenibilità.
Ogni modulo espone una factory `build_*_router()` che riceve le sue
dipendenze come parametri (dependency injection): questo evita import
circolari con `server.py` e mantiene i router testabili in isolamento.

Router disponibili:
  - `legal_routes`         → /api/legal/*, /api/docs/*   (documenti pubblici)
  - `sponsors_routes`      → /api/sponsors                (+ seed idempotente)
  - `favorites_routes`     → /api/favorites, /feuds/{id}/favorite
  - `support_routes`       → /api/support/submit          (Resend + Mongo)
  - `blocks_routes`        → /api/users/{id}/block, /api/users/{id}/report
  - `notifications_routes` → /api/notifications/*         (inbox)

Router NON in questa cartella:
  - `../bot_routes.py`     → /api/admin/bots/*  (già estratto prima)
"""
