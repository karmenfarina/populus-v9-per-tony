# POPULUS — Architettura del progetto

> Mappa file/cartelle e schema DB per orientarsi rapidamente nel codice.

---

## 1. Stack tecnologico

- **Frontend**: Expo SDK (React Native + Web via Metro), TypeScript,
  Expo Router (file-based routing).
- **Backend**: FastAPI (Python 3.x), APScheduler per task in background.
- **Database**: MongoDB (via Motor async driver).
- **Integrazioni**:
  - Emergent LLM Key (Claude/GPT/Gemini) per AI.
  - Emergent-managed Google OAuth.
  - Firebase Auth (per session Google alternativa).
  - YouTube Data API (media extraction).
- **Realtime**: WebSocket via FastAPI (chat, notifiche live).

---

## 2. Layout repository

```
/app
├── backend/                    FastAPI backend
│   ├── server.py               Router principale (monolitico, in refactoring)
│   ├── models.py               Pydantic request/response bodies
│   ├── helpers.py              Utility: hashing, id gen, timestamp, nickname
│   ├── moderation.py           Filtro parole vietate + moderazione IA
│   ├── media_extractor.py      Estrazione og:image, YouTube, ecc.
│   ├── analytics.py            Event logging + dashboard endpoints
│   ├── bot_engine.py           Scheduler + logica azioni bot fleet
│   ├── bot_personas.py         Costruzione 100 personas + prompt bot
│   ├── bot_routes.py           Endpoint admin per gestire i bot
│   ├── hot_topics.md           Config hot-reload boost tematici AI
│   ├── legal/                  ToS + NDA in markdown
│   │   ├── terms_v1.md
│   │   └── nda_v1.md
│   ├── scripts/                Script utility (reset DB, migrazioni)
│   │   └── reset_to_day_one.py
│   ├── tests/                  Test pytest
│   │   └── legacy/             Test iterativi storici
│   ├── .env                    Vars ambiente (MONGO_URL, JWT_SECRET, ...)
│   └── requirements.txt        Dipendenze Python (gestito via pip freeze)
│
├── frontend/                   Expo app
│   ├── app/                    Expo Router (file-based)
│   │   ├── _layout.tsx         Root layout + provider auth
│   │   ├── index.tsx           Entry redirect
│   │   ├── auth.tsx            Login/signup
│   │   ├── onboarding.tsx      Setup profilo iniziale
│   │   ├── terms.tsx           Accettazione ToS + NDA unificata
│   │   ├── verify-email.tsx    Verifica email dopo signup
│   │   └── (tabs)/             Layout tab principale
│   │       ├── _layout.tsx     Bottom tab bar
│   │       ├── index.tsx       Feed principale
│   │       ├── top.tsx         Faide "hot"
│   │       ├── archive.tsx     Archivio storico
│   │       ├── profile.tsx     Profilo utente
│   │       ├── notifications.tsx
│   │       ├── admin.tsx       Control panel founder
│   │       ├── support.tsx     Supporto
│   │       ├── feud/[id].tsx   Dettaglio faida + commenti
│   │       ├── user/[id].tsx   Profilo pubblico altrui
│   │       ├── circle/         Cerchia amici
│   │       ├── hashtag/[key].tsx
│   │       ├── messages/       DM
│   │       └── stories/        Storie
│   ├── src/
│   │   ├── api.ts              Client HTTP → backend
│   │   ├── theme.ts            Palette + tokens
│   │   ├── auth/               Context auth + hook
│   │   ├── components/         Componenti riutilizzabili
│   │   │   └── profile/        Sub-componenti profilo
│   │   ├── hooks/              Custom hooks
│   │   ├── ui/                 Design system (Button, Card, ecc.)
│   │   ├── utils/              deviceId, mentions, socials, ecc.
│   │   ├── ads/                Sponsor rendering
│   │   ├── messaging/          Websocket chat client
│   │   ├── notifications/      Push notifications setup
│   │   └── stories/            Utilities storie
│   ├── app.json                Config Expo (permessi, plugin)
│   ├── package.json            Dipendenze (gestito via `yarn expo install`)
│   └── .env                    Vars ambiente (EXPO_PUBLIC_*)
│
├── docs/                       Documentazione sviluppatore
│   ├── POPULUS_REGOLE_APP.md   Regole funzionali dell'app
│   ├── POPULUS_ALGORITMO_AI.md Regole algoritmi AI
│   └── POPULUS_ARCHITETTURA.md Questo file
│
├── memory/                     Info di sessione (test_credentials, ...)
├── test_reports/               Report iterativi testing_agent
├── test_result.md              Storico test + comunicazioni agent
└── README.md
```

---

## 3. Architettura backend

### 3.1 Entry point
`/app/backend/server.py` monta un `FastAPI()` con:
- **`api_router`** con prefisso `/api` (tutte le route esposte).
- **CORS** wildcard (OK per app mobile).
- **Startup handler**: avvia scheduler generatore faide + bot engine.
- **Shutdown handler**: stoppa scheduler pulitamente.

### 3.2 Autenticazione
- **JWT**: signup/login classici → `Authorization: Bearer <jwt>`.
- **Session token**: Google/Firebase OAuth → tabella `user_sessions`.
- Dep injection: `get_current_user`, `get_current_user_optional`.
- Admin gate: `require_admin` (header `X-Admin-Key`) o
  `_is_founder_admin(user)` (email match).

### 3.3 Task in background
- **APScheduler** (bot engine, tick 30 min).
- **`asyncio.create_task`** (generatore faide, loop continuo 10 min).

### 3.4 Modularizzazione (in corso)
`server.py` è in fase di refactoring: verrà spezzato in router per dominio
(`routes/auth.py`, `routes/feuds.py`, `routes/messages.py`, ...). Vedi
`docs/POPULUS_REGOLE_APP.md` per il quadro funzionale.

---

## 4. Architettura frontend

### 4.1 Routing
**Expo Router file-based**: ogni file in `/app/frontend/app/` corrisponde a una
route. Tab principali dentro `(tabs)/`.

### 4.2 State management
- **Context React** per auth (`src/auth/`).
- **Local state + hook** per il resto (nessun redux/zustand globale).
- **AsyncStorage** per persistenza (token, preferences).
- **SecureStore** per credenziali sensibili.

### 4.3 Networking
- **`src/api.ts`**: fetch wrapper con:
  - Base URL = `EXPO_PUBLIC_BACKEND_URL`
  - Auth header automatico da token in AsyncStorage
  - Gestione 401 (logout auto)
  - Retry su timeout

### 4.4 Realtime
- **WebSocket** per DM (`src/messaging/`).
- Polling per notifiche (feed refresh su focus).

### 4.5 Media
- **Immagini utente**: storage base64 in MongoDB (`user_photos`).
- **Immagini feud**: URL esterni (og:image dalle fonti).
- **YouTube embed**: estrazione ID + player nativo.

---

## 5. Schema database (MongoDB)

### 5.1 Collezioni principali

#### `users`
```
{
  user_id: "uuid",
  email: "...", email_verified: bool,
  nickname: "lowercase.handle",
  display_name: "Nome Visibile",
  auth_provider: "email|google|firebase|anonymous",
  is_anonymous: bool,
  is_bot: bool,             # 100 personas fleet
  is_dev_account: bool,     # esclude da analytics
  bot_active: bool,         # solo bot: online/offline
  city, dob, gender, profession, ...
  total_votes, majority_votes, minority_votes,
  history_public: bool,
  circle_privacy: "public|circle",
  accepted_terms_at: datetime,
  created_at, updated_at
}
```
Indici: `email` (unique), `nickname` (unique), `user_id` (unique),
`is_bot`, `bot_active`.

#### `feuds`
```
{
  feud_id: "uuid",
  title, body,
  category: "politica|tv|...", category_label,
  party_a, party_b,
  image_url, sources: [{url, title}],
  votes_a, votes_b,
  hashtags: ["#..."],
  source: "ai|editorial",
  hidden: bool, hidden_at, hidden_by,
  created_at, updated_at
}
```

#### `votes`
```
{
  vote_id, feud_id, user_id, side: "A|B",
  aligned_final: bool, winning_side_final,
  feud_snapshot: {title, category_label, ...},  # per archivio
  created_at
}
```
Indice unico: `(feud_id, user_id)`.

#### `comments` / `replies`
```
{
  comment_id (o reply_id), feud_id, user_id,
  side: "A|B", text,
  parent_id (solo per replies),
  mentions: ["user_id"],
  flagged: bool,
  created_at
}
```

#### `favorites`
```
{ favorite_id, user_id, feud_id, created_at }
```

#### `notifications`
```
{
  notif_id, user_id (destinatario),
  kind: "reply|mention|hot_feud|badge_unlocked|dm|story_reply",
  payload: {...},          # deep-link data
  read: bool,
  created_at
}
```

#### `messages` / `conversations`
```
messages: { message_id, sender_id, recipient_id, text, image?, reaction?, deleted, created_at }
conversations: { pair_id, participants: [uid1, uid2], last_message, last_activity }
```

#### `stories`
```
{
  story_id, author_id,
  image, text, background_color,
  privacy: "public|circle",
  hidden_viewers: [user_id],
  viewers: [{user_id, viewed_at}],
  expires_at (created_at + 24h),
  created_at
}
```

#### `friendships`
```
{ friendship_id, user_a, user_b, created_at }
```

#### `system_meta`
```
{
  key: "last_scheduler_run" | "bot_config" | ...,
  at, ...payload
}
```

### 5.2 Collezioni ausiliarie
- `user_photos` — foto profilo base64
- `user_sessions` — token session Google/Firebase
- `verification_tokens` — token email verification
- `feud_views` — analytics view feed
- `badge_notifications` — log sblocco badge
- `user_blocks` — blocchi utente-utente
- `user_reports` — segnalazioni
- `support_tickets` — richieste supporto
- `flagged_comments` — commenti in review
- `notification_locks` — anti-duplicati notifiche
- `sponsors` — sponsor rotativi

---

## 6. Comandi utili

### 6.1 Backend
```bash
sudo supervisorctl restart backend      # riavvia FastAPI
sudo supervisorctl status               # stato servizi
tail -f /var/log/supervisor/backend.err.log
```

### 6.2 Frontend
```bash
sudo supervisorctl restart expo         # riavvia Metro bundler
```

### 6.3 MongoDB shell
```bash
mongosh  "$MONGO_URL/$DB_NAME"          # accesso db
```

### 6.4 Reset "day one"
```bash
cd /app/backend && python scripts/reset_to_day_one.py
```
Pulisce tutte le collezioni utente **mantenendo**:
- Account admin (`carlofarinapayme@gmail.com`)
- 100 bot fleet
- Sponsors
- `system_meta` (config bot, ecc.)
- Schema + indici

### 6.5 Test backend
```bash
cd /app/backend && python -m pytest tests/ -v
```

---

## 7. Deploy

Deploy gestito via **Emergent Publish button** (top-right della UI Emergent).
Non usare EAS CLI direttamente. La pipeline gestisce:
- Backend containerizzato (FastAPI + MongoDB).
- Frontend: build web (Expo web) + build nativo iOS/Android (post-approval).

---

## 8. Debugging rapido

### 8.1 Errori auth
- Controlla `/app/memory/test_credentials.md` per credenziali corrette.
- Verifica JWT non expired (log backend).
- Se Google login fallisce → verifica config Emergent Auth in dashboard.

### 8.2 Feed vuoto
- Verifica lo scheduler: `db.system_meta.findOne({key: "last_scheduler_run"})`.
- Log backend: `grep scheduler /var/log/supervisor/backend.err.log`.

### 8.3 Bot non commentano
- Verifica `bot_config.enabled = true` e `active_count > 0`.
- Trigger manuale: `POST /api/admin/bot/burst`.
- Log: `grep bot_engine /var/log/supervisor/backend.err.log`.

---

*Ultimo aggiornamento: Feb 2026.*
