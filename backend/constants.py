"""
Populus — Costanti globali.

Contiene liste statiche condivise fra endpoint e logica interna:
  - CATEGORIES: le 9 categorie ufficiali delle faide (id + label).
  - PROFESSIONS: le opzioni professione mostrate in onboarding/profilo.
  - VALID_PROFESSIONS: set derivato per validazione input veloce.

Importa da qui invece di ridichiarare. Modifica queste liste solo
consapevolmente: aggiungere una nuova categoria richiede anche:
  1. Aggiornare `_fetch_headlines_for_category` in `server.py`
     con i feed RSS della nuova categoria.
  2. Aggiornare `POPULUS_ALGORITMO_AI.md` §2.1.
  3. (Opzionale) aggiungere voci a `hot_topics.md`.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────
# Categorie faide
# ─────────────────────────────────────────────────────────────────
CATEGORIES = [
    {"id": "politica", "label": "Politica"},
    {"id": "tv", "label": "Programmi TV"},
    {"id": "musica", "label": "Musica"},
    {"id": "sport", "label": "Sport"},
    {"id": "cinema", "label": "Cinema"},
    {"id": "social", "label": "Social"},
    {"id": "gossip", "label": "Gossip"},
    {"id": "cronaca", "label": "Cronaca"},
    {"id": "tech", "label": "Tech"},
]


# ─────────────────────────────────────────────────────────────────
# Professioni (onboarding / profilo)
# ─────────────────────────────────────────────────────────────────
PROFESSIONS = [
    "Studente/Studentessa",
    "Impiegato/a",
    "Operaio/a",
    "Insegnante",
    "Dirigente / Manager",
    "Libero professionista",
    "Imprenditore/Imprenditrice",
    "Artigiano/a",
    "Commerciante",
    "Agricoltore/Agricoltrice",
    "Medico / Personale sanitario",
    "Avvocato / Notaio",
    "Ingegnere / Architetto",
    "Ricercatore/Ricercatrice",
    "Militare / Forze dell'ordine",
    "Artista / Creativo",
    "Giornalista / Comunicazione",
    "Informatico / Tecnologia",
    "Trasporti / Logistica",
    "Ristorazione / Turismo",
    "Casalingo/a",
    "In cerca di occupazione",
    "Pensionato/a",
    "Altro",
    "Preferisco non dirlo",
]

VALID_PROFESSIONS = set(PROFESSIONS)
