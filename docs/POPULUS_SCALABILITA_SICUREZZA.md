# POPULUS — Scalabilità & Sicurezza

> Riepilogo delle ottimizzazioni per gestire alto traffico e degli
> hardening applicati. Aggiornare a ogni introduzione di nuovi indici,
> rate-limit o header di sicurezza.

---

## 1. Scalabilità

### 1.1 Indici MongoDB (`/app/backend/db_indexes.py`)
Modulo idempotente `ensure_indexes(db)` chiamato al boot. Copre **20 collezioni** con **39+ indici** custom. Include:
- Composti per feed (`feuds.category+created_at`, `feuds.is_hidden+created_at`).
- Filter+sort per notifiche, DM, storie (`user_id+created_at DESC`).
- Unique constraints (`friendships`, `user_blocks`, `system_meta`, `notification_locks.key`).
- TTL naturali (`notification_locks` 48h, `verification_tokens` on-expire, `stories` via `expires_at`, `user_sessions`).

Ogni volta che aggiungi una nuova query "hot", aggiungi l'indice qui.

### 1.2 Connection pool Mongo (`server.py`)
```python
AsyncIOMotorClient(
    mongo_url,
    maxPoolSize=200,            # up from default 100
    minPoolSize=10,             # 10 socket sempre caldi
    waitQueueTimeoutMS=5000,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    retryWrites=True,
)
```

### 1.3 Compressione (GZip middleware)
Response >1KB compresse automaticamente. Riduce banda ~55% su JSON feed.

### 1.4 Cache HTTP (edge/browser)
Middleware setta `Cache-Control` su GET pubblici stabili:
- `/api/categories`, `/api/professions`, `/api/legal/*`, `/api/docs/*` → `public, max-age=300, s-maxage=600`
- `/api/sponsors` → `public, max-age=120`
- `/api/share/{id}/html` → `public, max-age=300`

Al crescere degli utenti, un CDN davanti al backend (Cloudflare/Fastly) può servire questi endpoint quasi a costo zero.

### 1.5 Paginazione difensiva
- Feed live: hard limit 50 doc.
- Cronologia voti: hard limit 500 doc.
- Notifiche: hard limit 50 doc.
- Storie feed: hard limit 500 doc.

### 1.6 Task background
- **APScheduler** (bot fleet tick 30min).
- **Loop asyncio** in-process per generazione faide (10min tick).

⚠️ **Non scala oltre singolo processo**. Se il pod scala orizzontalmente:
- Sposta scheduler + generator su un worker dedicato (leader election via Mongo lock su `system_meta.key='scheduler_leader'`).
- Alternative: Celery + Redis, o RQ.

### 1.7 Rate limit in-memory
⚠️ Rate limiter attuale è **in-memory per processo**. Se scala orizzontalmente perde efficacia. Alla scala di 100k+ utenti:
- Migrare a Redis + `slowapi` (`Limiter(storage_uri="redis://…")`).

---

## 2. Sicurezza

### 2.1 Autenticazione
- **JWT HS256** (secret in `JWT_SECRET` env). TTL 7gg.
- **Bcrypt** per password (default gensalt → rounds=12, ~250ms).
- **Session token** per OAuth Google/Firebase (tabella `user_sessions`).
- Warning al boot se `JWT_SECRET` debole (<32 char o contiene "change/secret/test/…").

### 2.2 Rate limit anti brute-force
- `POST /auth/login`: max **10 tentativi** per (IP+email) all'ora → 429.
- `POST /auth/signup`: max **5 signup** per IP all'ora.
- `POST /auth/verify-email`: max **20 tentativi** per IP all'ora.
- `POST /auth/resend-verification`: max **3 tentativi** per (IP+email) all'ora.

Rate limit rispetta `X-Forwarded-For` (header injection dal Kubernetes ingress).

### 2.3 Header di sicurezza (globali via middleware)
Applicati su **ogni risposta HTTP**:
- `X-Content-Type-Options: nosniff` — previene MIME sniffing.
- `X-Frame-Options: DENY` — previene clickjacking (embed in iframe).
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — forza HTTPS.
- `Referrer-Policy: strict-origin-when-cross-origin` — limita referer leak.
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()` — disabilita API browser sensibili di default.

### 2.4 Content-Security-Policy (share HTML)
Applicata solo su `/api/share/{id}/html`:
```
default-src 'self';
img-src 'self' https: data:;
style-src 'self' 'unsafe-inline';
script-src 'none';         ← nessun JS eseguibile
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```
User-content è comunque escapato con `html.escape()` prima del rendering.

### 2.5 Admin endpoints
- Tutti gli endpoint `/api/admin/*` richiedono header `X-Admin-Key`.
- Warning al boot se `ADMIN_TOKEN` vuoto o placeholder.
- UI admin (`/admin`) accessibile solo all'email founder (`FOUNDER_ADMIN_EMAIL`).

### 2.6 Moderazione contenuti
- `BLOCKED_WORDS` (regex) su titoli/commenti/DM (`moderation.py`).
- `ai_moderate_comment` (LLM) per contenuti borderline.
- Commenti sospetti → `flagged_comments` per admin review.
- Blocchi utente-utente propagano su feed/comment/DM (cascade su Cerchia).

### 2.7 Enumerazione utenti
- `/auth/resend-verification` ritorna sempre 200 (non conferma se l'email esiste).
- `/auth/login` ritorna sempre 401 senza distinguere "utente inesistente" da "password sbagliata".

### 2.8 Injection
- MongoDB Motor: query parametrizzate (`.find({field: value})`) → no NoSQL injection.
- HTML share: `html.escape()` su tutti i campi user-controlled.
- Nickname: whitelist regex `^[a-z0-9._]+$`.

### 2.9 Secrets management
- Tutte le chiavi/token in `.env` (mai in repo).
- `EMERGENT_LLM_KEY`, `EMERGENT_PUSH_KEY`, `RESEND_API_KEY`, `YOUTUBE_API_KEY` fuori dal codice.
- `firebase-service-account.json` letto da file, path in `.env`.

### 2.10 CORS
Attualmente `allow_origins=['*']` — accettabile per app mobile (native client, no cookie session). In produzione web, se si aggiunge un frontend web con auth-cookie, restringere agli host approvati.

---

## 3. Monitoraggio a scala

Non incluso in questa release ma **fortemente consigliato** al superamento di ~10k utenti attivi:
- **Sentry** o **Rollbar** per error tracking.
- **Prometheus + Grafana** o **Datadog APM** per latenze/throughput.
- **MongoDB Atlas Performance Advisor** per suggerimenti indici automatici.

---

## 4. Checklist deploy produzione

- [ ] `JWT_SECRET` = string casuale ≥32 byte (es. `openssl rand -hex 32`).
- [ ] `ADMIN_TOKEN` = string casuale, ≠ default.
- [ ] CORS `allow_origins` = lista domini esatti (se web).
- [ ] Backup automatici MongoDB (Atlas o cron `mongodump`).
- [ ] TLS attivo dal load balancer/ingress (Emergent lo fa).
- [ ] Log rotazione (supervisor già configurato).
- [ ] Rate limit su Redis (se >1 processo).
