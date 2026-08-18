# /app/docs — Documentazione sviluppatore Populus

Cartella con tutta la documentazione operativa dell'app **Populus**.
Scaricabile e consultabile da qualsiasi programmatore che prenda in mano
il progetto.

## File disponibili

| File | Contenuto |
|---|---|
| **[POPULUS_REGOLE_APP.md](./POPULUS_REGOLE_APP.md)** | Regole di funzionamento dell'app: voto, faide, commenti, storie, cerchia, DM, notifiche, admin, moderazione. |
| **[POPULUS_ALGORITMO_AI.md](./POPULUS_ALGORITMO_AI.md)** | Algoritmi AI: selezione notizie, generazione faide, fact-checker, moderazione, bot fleet, prompt, soglie. |
| **[POPULUS_ARCHITETTURA.md](./POPULUS_ARCHITETTURA.md)** | Mappa file/cartelle, schema DB MongoDB, comandi utili, debugging rapido. |

## Come mantenerli aggiornati

- Aggiornare a ogni cambio di **logica di business** (voti, badge, moderazione).
- Aggiornare a ogni cambio di **prompt AI**, modello LLM o soglie
  (`HOT_MIN_*`, `SCHEDULER_TICK_MIN`, ecc.).
- Aggiungere nuove sezioni se si introducono nuove features (non basta
  scrivere codice — documentare qui è parte del "definition of done").

## Configurazione hot-reloadable

- **`/app/backend/hot_topics.md`**: temi caldi da boostare nella selezione
  AI delle notizie. Modifiche hanno effetto in max 10 min senza restart.

## Endpoint API per consultazione online

I file di documentazione sono anche accessibili come endpoint API:
- `GET /api/docs/regole` → `POPULUS_REGOLE_APP.md`
- `GET /api/docs/algoritmo-ai` → `POPULUS_ALGORITMO_AI.md`
- `GET /api/docs/architettura` → `POPULUS_ARCHITETTURA.md`
