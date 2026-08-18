# POPULUS — Regole di funzionamento dell'app

> Documento di riferimento per sviluppatori. Descrive **come si comporta**
> l'applicazione dal punto di vista funzionale, senza scendere nei dettagli
> implementativi. Aggiornare quando cambia la logica di business.

---

## 1. Concetti fondamentali

### 1.1 Cos'è una "faida" (feud)
- Ogni faida rappresenta uno **scontro binario** tra due posizioni (parte A vs
  parte B) su una notizia reale, di attualità italiana.
- Ogni faida ha: titolo, corpo/descrizione, categoria, immagine, `party_a`,
  `party_b`, lista sorgenti, e contatori voti/commenti.
- Le faide possono essere:
  - **AI-generated** (`source='ai'`) — create automaticamente dallo scheduler.
  - **Editoriali** — create manualmente dall'admin.
- **Ritenzione**: le faide più vecchie di **14 giorni** (`FEUD_RETENTION_DAYS`)
  vengono automaticamente eliminate insieme ai loro commenti/risposte. I voti
  invece vengono "congelati" con uno snapshot per l'archivio profilo.

### 1.2 Categorie disponibili
`politica`, `tv`, `musica`, `sport`, `cinema`, `social`, `gossip`, `cronaca`,
`tech`. Sono definite in `CATEGORIES` in `server.py`.

### 1.3 Ciclo di vita di una faida
1. Generata (AI o editoriale) → visibile nel feed live.
2. Riceve voti/commenti → può diventare **HOT** (vedi §4).
3. Dopo 14 giorni → viene rimossa dal DB, ma i voti restano nell'archivio
   utente (con snapshot metadata).

---

## 2. Utenti

### 2.1 Tipi di account
- **Anonimo** (`auth_provider='anonymous'`, `is_anonymous=true`): può votare,
  ma NON può commentare, mandare DM, creare storie, aggiungere alla cerchia.
- **Registrato via email + password**: accesso completo dopo verifica email
  (token via link).
- **Google (Emergent-managed OAuth)**: accesso immediato completo.
- **Bot** (`is_bot=true`, `is_dev_account=true`): 100 personas autonomi.
  Filtrati da ogni query analytics.
- **Admin/founder**: unico account con email `carlofarinapayme@gmail.com`.
  Ha poteri di edit/hide/restore feud e accesso al control panel.

### 2.2 Upgrade da anonimo → registrato
- Al momento del signup/login con un token anon attivo, il backend **fonde**
  automaticamente voti, commenti, risposte e messaggi dal profilo anonimo al
  nuovo account (`_migrate_anon_data`).
- L'account anonimo viene cancellato dopo la migrazione.

### 2.3 Nickname
- Formato Instagram-style: minuscolo, `[a-z0-9._]`, lunghezza 2-24.
- Unicità garantita a livello DB.
- Storati sempre lowercase.

### 2.4 Badge e allineamento
- **Utente di Buon Senso**: chi ha votato più volte con la maggioranza
  (`majority >= minority`, dopo almeno 10 voti totali).
- **Bastian Contrario**: chi ha votato più volte con la minoranza.
- **Category badges**: sbloccati votando N faide di una singola categoria.
  Trigger una notifica dedicata (`badge_notifications`).

---

## 3. Meccaniche di voto e commenti

### 3.1 Voto
- Un utente può votare **una sola volta** per faida (indice unico su
  `(feud_id, user_id)`).
- Può cambiare il proprio voto: viene decrementato il contatore precedente e
  incrementato il nuovo (evento `EVT_VOTE_CHANGE` loggato).
- I risultati (`pct_a`, `pct_b`) sono **nascosti** finché l'utente non vota
  (mantiene la parità informativa).
- Ogni voto ha uno snapshot immutabile della faida per l'archivio.

### 3.2 Commenti e risposte
- **Solo utenti registrati** possono commentare (anonimi bloccati).
- Un commento ha: `feud_id`, `user_id`, `side` (A o B), `text`, `parent_id`.
- Le **risposte** sono commenti annidati con `comment_id` come parent.
- Menzioni `@nickname` supportate → generano notifica al menzionato.
- Moderazione: filtro `BLOCKED_WORDS` + moderazione IA (`moderate_text`,
  `ai_moderate_comment`) prima dell'inserimento.
- **Cancellazione**: solo autore, admin, o utente in caso di segnalazione
  approvata.

### 3.3 Preferiti
- Aggiunta/rimozione feud dalla lista preferiti (`favorites` collection).
- Visualizzabile nella sezione dedicata del profilo.

### 3.4 Cronologia (history)
- Ogni voto entra nella cronologia pubblica dell'utente (se `history_public=true`).
- L'utente può oscurare la propria cronologia dalle impostazioni privacy.

---

## 4. Faide "hot" (notifiche di trend)

Una faida è considerata **hot** quando l'engagement raggiunge:
- `votes_a + votes_b >= 10` **AND** `comments >= 3`, **OR**
- Score combinato `votes + 2*comments >= 15`.

Costanti: `HOT_MIN_VOTES=10`, `HOT_MIN_COMMENTS=3`, `HOT_MIN_COMBINED_SCORE=15`.

**Importante**: le notifiche hot vengono attivate SOLO da attività di utenti
**reali** — mai da voti/commenti dei bot. Il fanout è nel path di voto/commento,
non alla creazione della faida.

---

## 5. Storie (Stories, 24h)

- Formato: immagine + testo breve (`STORY_COMMENT_MAX` char).
- TTL: **24 ore** (`STORY_TTL_HOURS=24`) — poi non più visibili nel feed.
- Solo utenti registrati possono creare storie.
- **Cerchia only**: le storie sono visibili solo agli amici della cerchia
  dell'autore (se privacy = "circle") oppure a tutti (se "public").
- **Hidden viewers**: l'autore può nascondere singoli spettatori.
- Reply alla storia → arriva come DM privato all'autore.

---

## 6. Cerchia (Circle)

- Sistema di amicizia bidirezionale (`friendships` collection).
- Un utente può aggiungere/rimuovere amici alla propria cerchia.
- La cerchia determina visibilità di storie e alcune funzioni sociali.
- Privacy della cerchia: pubblica o privata (impostabile dall'utente).

---

## 7. Messaggi diretti (DM)

- Solo utenti registrati possono scambiare DM.
- Struttura: `messages` collection con `sender_id`, `recipient_id`, `text`,
  `image` (opzionale, max `MAX_MSG_IMAGE_BYTES`).
- **Reazioni** ai messaggi (emoji singola).
- **Blocco utente**: impedisce DM in entrambe le direzioni + nasconde
  commenti/risposte del bloccato (filtro applicato su ogni feed).
- **Share to users**: condivisione faide direttamente in chat.
- **Segnalazione utente**: report inviato all'admin per revisione.

---

## 8. Notifiche

Tipi di notifica:
- `reply` — qualcuno risponde a un tuo commento
- `mention` — qualcuno ti menziona
- `hot_feud` — una faida che hai votato/commentato è diventata hot
- `badge_unlocked` — hai sbloccato un badge (buon senso, bastian, categoria)
- `dm` — nuovo messaggio diretto
- `story_reply` — qualcuno ha risposto a una tua storia

**Deep-link**: ogni notifica porta al contesto specifico. Le notifiche di
`reply`/`mention` aprono la faida, espandono automaticamente la sezione
commenti e fanno scroll animato al commento target (con highlight giallo
1.2s + fade 1s).

**Notification locks**: `notification_locks` previene doppie notifiche in
finestre temporali brevi.

**Push**: gestite via Emergent-managed push notifications. Funziona solo
dopo il deploy su build reale (non in Expo Go).

---

## 9. Ricerca e navigazione

- **Search globale**: cerca faide per titolo/parti/hashtag.
- **Search utenti**: per nickname/display name.
- **Hashtag**: `#tag` estratti automaticamente da titolo/corpo faida. Cliccabili
  → filtrano faide correlate.
- **Menzioni**: `@nickname` in commenti/DM → suggeritore autocomplete.

---

## 10. Moderazione

- **BLOCKED_WORDS**: lista in `moderation.py` — blocco secco su match.
- **AI moderation** (`ai_moderate_comment`): revisione LLM per contenuti al
  limite (odio, sessismo, minacce).
- **Flagged comments**: `flagged_comments` collection per commenti trattenuti
  in review manuale.
- **User reports**: `user_reports` — segnalazioni utente-vs-utente per admin.

---

## 11. Admin control panel

Accessibile solo a `FOUNDER_ADMIN_EMAIL`. Funzioni:

- **Feud controls**: edit, hide, restore, delete di qualsiasi faida.
- **Bot fleet**:
  - Enable/disable master switch
  - Regolazione `active_count` (0-100)
  - Trigger `POST /api/admin/bot/burst` per attività immediata
  - Reset commenti/risposte dei bot (`POST /api/admin/bot/reset`)
- **Analytics dashboard**: metriche di ingaggio, retention, demografia
  (bot esclusi automaticamente).
- **Support tickets**: gestione richieste supporto utenti.
- **Hidden feuds**: lista di feud nascoste manualmente.

---

## 12. Legal (Terms + NDA)

- Onboarding: schermata unificata `terms.tsx` con accettazione ToS + NDA.
- File sorgente: `/app/backend/legal/terms_v1.md` e `nda_v1.md`.
- Endpoint di consultazione: `GET /api/legal/terms`, `GET /api/legal/nda`.
- Accettazione salvata su `users.accepted_terms_at`.

---

## 13. Auto-generazione faide (scheduler)

Loop continuo (`_daily_generation_loop`) che:
- Ogni **10 minuti** (`SCHEDULER_TICK_MIN`) prova a generare una faida per
  ciascuna categoria.
- Salta la categoria se ha prodotto una faida negli ultimi **20 minuti**
  (`CATEGORY_COOLDOWN_MIN`).
- Usa RSS + LLM per selezione notizie, `hot_topics.md` per boost tematici.
- Ogni faida passa un **fact-check IA** prima della pubblicazione.

Per i dettagli AI: vedi `POPULUS_ALGORITMO_AI.md`.

---

## 14. Bot Fleet (100 personas autonomi)

Per la logica bot completa: vedi `POPULUS_ALGORITMO_AI.md` §5.

Riepilogo:
- 100 bot con persona unica (età, nickname, città, allineamento).
- Tick ogni **30 minuti**: subset random compie azioni (vota, commenta,
  risponde, crea storia occasionale).
- **Distribuzione voti/commenti bilanciata** rispetto alla reale distribuzione
  umana (`_pick_comment_side`) — evita accumulo tutto su un lato.
- Commenti generati via **Claude Haiku 4.5** con banned openers per evitare
  pattern IA riconoscibili.

---

## 15. File di configurazione runtime (hot-reload)

- **`/app/backend/hot_topics.md`**: temi caldi per boost priorità AI. Modifiche
  hanno effetto al prossimo tick (max 10 min) senza restart.

---

## 16. Environment variables critiche

Backend (`/app/backend/.env`):
- `MONGO_URL` — connessione MongoDB (NON modificare)
- `DB_NAME` — nome database
- `JWT_SECRET` — chiave firma JWT
- `EMERGENT_LLM_KEY` — chiave universale AI (Claude/GPT/Gemini)
- `ADMIN_TOKEN` — token per header `X-Admin-Key` su endpoint admin protetti

Frontend (`/app/frontend/.env`):
- `EXPO_PUBLIC_BACKEND_URL` — url backend (auto-configurato da Emergent)
- `EXPO_PACKAGER_PROXY_URL`, `EXPO_PACKAGER_HOSTNAME` — Metro bundler (NON modificare)

---

## 17. Schema database (collezioni principali)

| Collezione | Contenuto |
|---|---|
| `users` | Anagrafica utenti (umani + bot). Chiave: `user_id` (uuid) |
| `feuds` | Faide. Chiave: `feud_id` |
| `votes` | Voti. Indice unico `(feud_id, user_id)` |
| `comments` | Commenti alle faide |
| `replies` | Risposte ai commenti |
| `favorites` | Preferiti utente |
| `feud_views` | Analytics view faide |
| `messages` | DM |
| `conversations` | Cache metadata conversazioni |
| `notifications` | Feed notifiche |
| `notification_locks` | Anti-duplicazione notifiche |
| `stories` | Storie 24h |
| `user_photos` | Foto profilo (base64) |
| `user_sessions` | Sessioni token (Google) |
| `verification_tokens` | Token email verification |
| `friendships` | Cerchia amici |
| `badge_notifications` | Log sblocco badge |
| `user_blocks` | Blocchi utente-utente |
| `user_reports` | Segnalazioni |
| `support_tickets` | Ticket supporto |
| `flagged_comments` | Commenti in review moderazione |
| `system_meta` | Config runtime (last scheduler run, ecc.) |
| `sponsors` | Sponsor a rotazione nel feed |

---

## 18. File di riferimento chiave

- **Backend logic**: `/app/backend/server.py`, `bot_engine.py`, `bot_personas.py`
- **Helpers**: `/app/backend/helpers.py`, `moderation.py`, `media_extractor.py`,
  `models.py`, `analytics.py`
- **Frontend router**: `/app/frontend/app/(tabs)/` (Expo Router)
- **Componenti riutilizzabili**: `/app/frontend/src/components/`
- **Utils frontend**: `/app/frontend/src/utils/`
- **API client**: `/app/frontend/src/api.ts`

---

*Ultimo aggiornamento: Feb 2026. Aggiornare a ogni cambio di logica di business.*
