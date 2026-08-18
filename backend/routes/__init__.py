"""
Populus — Backend routes.

Router modulari estratti da `server.py` per migliorare la manutenibilità.
Ogni modulo espone una factory `build_*_router()` che riceve le sue
dipendenze come parametri (dependency injection): questo evita import
circolari con `server.py` e mantiene i router testabili in isolamento.

Router disponibili:
  - `legal_routes` → /api/legal/*, /api/docs/*  (documenti pubblici)
"""
