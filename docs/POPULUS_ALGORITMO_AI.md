# POPULUS — Algoritmo di Intelligenza Artificiale

> Documento operativo per lo sviluppatore. Descrive **come funziona l'AI** in
> Populus: selezione notizie, generazione faide, moderazione, bot fleet.
> Aggiornare quando cambiano prompt, modelli o soglie.

---

## 1. Panoramica sistemi AI

Populus utilizza **AI** in 4 punti critici:

1. **Generazione automatica delle faide** (scheduler continuo).
2. **Fact-checker editoriale** (revisione IA prima della pubblicazione).
3. **Moderazione commenti** (revisione contenuti al limite).
4. **Bot fleet** (100 personas autonome che simulano ingaggio umano).

Tutti i sistemi usano la **Emergent LLM Key** (`EMERGENT_LLM_KEY` env var) via
libreria `emergentintegrations`. Non serve una chiave OpenAI/Anthropic diretta.

**Modelli utilizzati**:
- **Claude Haiku 4.5** → bot fleet, moderazione (fast, economico).
- **Claude Sonnet / GPT-5.x** (configurabile) → generazione faide + fact-checker
  (richiedono ragionamento più profondo).

---

## 2. Algoritmo di scelta delle notizie

### 2.1 Fonti (RSS)
Il generatore attinge da un pool di feed RSS italiani (definiti in
`server.py`, funzione `_fetch_headlines_for_category`). Ogni categoria ha una
lista dedicata di fonti giornalistiche e magazine.

Categorie e loro focus editoriale:
| Categoria | Focus |
|---|---|
| `politica` | Governo, opposizione, dichiarazioni polarizzanti |
| `tv` | Reality, talent, litigi in diretta |
| `musica` | Sanremo, artisti, controversie discografiche |
| `sport` | Calcio, mercato, VAR, tifoserie |
| `cinema` | Uscite, premi, polemiche registi/attori |
| `social` | Influencer, TikTok drama, virali |
| `gossip` | Coppie famose, rotture, scandali |
| `cronaca` | Fatti gravi, casi noti (attenzione al tatto) |
| `tech` | AI, Big Tech, Musk/Zuckerberg/Altman |

### 2.2 Filtri di deduplicazione
- **`used_links`**: ogni URL già trasformato in faida viene salvato per evitare
  ripetizioni. TTL 30 giorni.
- **`used_titles`**: match fuzzy su titoli per evitare doppioni semantici.

### 2.3 Criteri di priorità (usati dal prompt)
Il prompt di selezione istruisce il modello a dare priorità a notizie che
soddisfano uno o più di questi criteri (rivedibili in `hot_topics.md`):

- **Due fazioni chiare** (persone, gruppi, tifoserie, correnti)
- **Emozione forte** (rabbia, indignazione, ironia, gossip, tifo)
- **Conflitto pubblico** (litigi, gaffe, dichiarazioni divisive)
- **Personaggi riconoscibili** dal pubblico italiano
- **Opinioni contrapposte** (nessun consenso maggioritario)
- **Controversia elevata** (virale sui social o già oggetto di dibattito)

### 2.4 Boost tematico (`hot_topics.md`)
File di configurazione **hot-reloadable** (nessun restart backend richiesto):

```
/app/backend/hot_topics.md
```

Contiene la lista di argomenti prioritari. Al prossimo tick (max 10 min) il
generatore userà la lista aggiornata. Le voci elencate ricevono un boost
significativo di priorità nella selezione a parità di altri criteri.

Argomenti attualmente boostati:
- Sanremo, Grande Fratello, Temptation Island, Amici
- Calcio, cronaca nera, gossip/influencer
- Politica economica, notizie polarizzanti
- Guerra, pandemie, questioni di genere, femminicidi
- AI e Silicon Valley (Musk, Zuckerberg, Altman)

**Formato**: righe che iniziano con `-` o `*` sono voci; `#` sono heading
ignorate; righe vuote ignorate.

### 2.5 AI-skip
Il modello può rispondere `{"skip": true}` se non trova notizie adatte in
una categoria. In quel caso il ciclo passa alla categoria successiva
**senza consumare il cooldown** (fair chance al prossimo tick).

---

## 3. Ciclo di generazione (scheduler)

**Loop**: `_daily_generation_loop` in `server.py`.

```
ogni SCHEDULER_TICK_MIN (10 min):
    per ogni categoria in CATEGORIES:
        se ultima faida della categoria < CATEGORY_COOLDOWN_MIN (20 min) fa:
            → skip (cooldown)
        altrimenti:
            headlines = _fetch_headlines_for_category(cat)
            feud = _generate_feud_for_category(cat, LlmChat, ...)
            se feud:
                feud = _ai_fact_check_feud(feud, ...)  # revisione IA
                se supera fact-check:
                    db.feuds.insert_one(feud)
    _cleanup_expired_feuds()   # purge > 14 giorni
```

**Motivazione delle soglie**:
- **Tick 10 min**: permette di riempire buchi orari (es. `social` spento la
  notte) rapidamente.
- **Cooldown 20 min per categoria**: evita bursts (troppe faide dello stesso
  tema in poco tempo).
- Combinati, garantiscono che nell'app ci sia sempre almeno una faida
  fresca (<20 min) in qualche categoria.

### 3.1 Fact-checker IA (`_ai_fact_check_feud`)

Gate obbligatorio prima della pubblicazione. Il fact-checker verifica:
- La notizia è **plausibile e attribuita** correttamente alle fonti citate?
- I `party_a` e `party_b` rappresentano posizioni **realmente contrapposte**?
- Il titolo è **neutrale** (non tendenzioso pro/contro una parte)?
- Il body descrive **fatti**, non opinioni non attribuite?
- Non ci sono **hallucinations** (nomi/date/eventi inventati)?

Se il fact-checker rigetta → feud scartata, nessun insert.

---

## 4. Generazione contenuti (schema output)

Il prompt di generazione forza un JSON strutturato:

```json
{
  "title": "Titolo breve, neutrale",
  "body": "Descrizione del conflitto in 2-4 frasi",
  "category": "politica|tv|musica|...",
  "party_a": "Nome/etichetta parte A",
  "party_b": "Nome/etichetta parte B",
  "sources": [{"url": "...", "title": "..."}],
  "image_url": "url_immagine_originale",
  "hashtags": ["#tag1", "#tag2"]
}
```

**Regole editoriali applicate dal prompt**:
- No linguaggio dispregiativo.
- No parti umane monolitiche ("gli italiani vs il governo"): sempre entità
  specifiche.
- Formato `party_a` / `party_b` breve (max 3-4 parole).
- Hashtag estratti dal soggetto principale.

---

## 5. Bot Fleet (100 personas autonomi)

**File**: `/app/backend/bot_engine.py`, `bot_personas.py`.

### 5.1 Seeding personas
- 100 utenti creati una tantum al primo avvio (`_ensure_bots_seeded`).
- Ogni bot ha: nickname unico, età (16-70), città italiana, allineamento
  politico/culturale (bilanciato per costruzione), stile linguistico.
- Marcati `is_bot=true` + `is_dev_account=true` → **invisibili all'analytics**
  per costruzione (tutte le query filtrano `is_dev_account: {"$ne": True}`).

### 5.2 Scheduler bot
- **APScheduler** (`AsyncIOScheduler`).
- Tick ogni **30 minuti** (`bot_tick`).
- Master switch `bot_config.enabled` (default OFF).
- `bot_config.active_count`: quanti bot sono "online" (0-100).

### 5.3 Comportamento del tick
Ad ogni tick, un subset random dei bot attivi decide se:
- Votare una faida esistente (probabilità alta).
- Commentare una faida (probabilità media).
- Rispondere a un commento umano (probabilità bassa).
- Creare una storia (probabilità molto bassa).

### 5.4 Distribuzione voti/commenti realistica
Funzione `_pick_comment_side` (in `bot_engine.py`):
- **Non** segue rigidamente il bias politico della persona.
- Legge la **distribuzione voti reale** della faida (`votes_a`/`votes_b`).
- Applica smoothing di Laplace per evitare distorsioni con poche osservazioni.
- Sceglie il lato ponderato → i commenti bot si distribuiscono in modo
  simile a quello che sta facendo il pubblico umano.

**Motivazione**: senza questa logica, i bot rossi commentavano tutti su A e
i gialli tutti su B → distribuzione irrealistica. Ora bilanciano.

### 5.5 Generazione testo commenti (Claude Haiku 4.5)
Prompt costruito ad hoc per ogni bot:
- Include il **profilo persona** (età, città, allineamento, stile).
- Include il **contesto della faida** (titolo, body, side scelto).
- **`BANNED_OPENERS`**: lista di frasi/pattern IA riconoscibili (es. "Assolutamente!",
  "Concordo pienamente,", "In conclusione,") che vengono bannate.
- **`random_style_hint`**: micro-variazioni stilistiche (uso emoji, refuso
  volontario, dialettismo, lunghezza) per rompere il pattern generico.
- Filtro di rifiuto: se l'output contiene un banned opener o suona "IA
  standard", viene scartato e rigenerato.

### 5.6 Isolamento analytics
Ogni query analytics filtra:
```python
{'is_dev_account': {'$ne': True}}
```
E per demografia si aggiunge:
```python
{'is_bot': {'$ne': True}}
```
→ le metriche del founder vedono solo attività umana reale.

### 5.7 Hot news da bot: DISABILITATO
Le notifiche "faida calda" **non scattano mai** per attività bot. Il fanout
`_fanout_hot_news` è nel path voto/commento reale, non nel path bot.

### 5.8 Controlli admin
- `POST /api/admin/bot/burst` → esegue un tick immediato.
- `POST /api/admin/bot/reset` → cancella tutti i commenti/risposte bot
  (utile per test).
- `PATCH /api/admin/bot/config` → cambia `enabled` / `active_count`.
- `GET /api/admin/bot/state` → snapshot corrente (per il pannello).

---

## 6. Moderazione AI (commenti utente)

**File**: `/app/backend/moderation.py`.

### 6.1 Livello 1: `moderate_text` (regex + BLOCKED_WORDS)
- Match fuzzy contro lista offensive/vietate.
- Block secco → 400 al frontend.

### 6.2 Livello 2: `ai_moderate_comment` (LLM review)
- Se passa il livello 1 ma contiene segnali borderline (parolacce non in lista,
  toni aggressivi, riferimenti sensibili), viene passato a un LLM di review.
- Il modello risponde con `{"allow": true|false, "reason": "..."}`.
- Se `allow=false` → commento marchiato `flagged` e messo in `flagged_comments`
  per review admin.

### 6.3 Filtri applicati sempre
- No dati personali (email/telefoni) — regex.
- No URL esterni sospetti — dominio whitelist.

---

## 7. Riepilogo variabili di controllo

| Variabile | Dove | Default | Descrizione |
|---|---|---|---|
| `SCHEDULER_TICK_MIN` | `server.py` | 10 min | Cadenza tick generatore faide |
| `CATEGORY_COOLDOWN_MIN` | `server.py` | 20 min | Cooldown per categoria |
| `FEUD_RETENTION_DAYS` | `server.py` | 14 giorni | Durata faida prima della purge |
| `STORY_TTL_HOURS` | `server.py` | 24 h | Durata storia |
| `HOT_MIN_VOTES` | `server.py` | 10 | Soglia voti per faida hot |
| `HOT_MIN_COMMENTS` | `server.py` | 3 | Soglia commenti per hot |
| `HOT_MIN_COMBINED_SCORE` | `server.py` | 15 | Score alternativo (voti + 2×commenti) |
| Bot tick | `bot_engine.py` | 30 min | Cadenza azioni bot |
| `bot_config.enabled` | DB | `false` | Master switch bot fleet |
| `bot_config.active_count` | DB | 0 | Quanti bot online (0-100) |

---

## 8. Come modificare l'AI

### 8.1 Aggiungere/rimuovere temi caldi
Modifica `/app/backend/hot_topics.md`. **No restart**.

### 8.2 Cambiare cadenza scheduler
Modifica `SCHEDULER_TICK_MIN` e/o `CATEGORY_COOLDOWN_MIN` in `server.py`,
funzione `_daily_generation_loop`. Restart backend richiesto.

### 8.3 Cambiare il modello LLM
La libreria `emergentintegrations` supporta il switch di modello nel costruttore
di `LlmChat`. Cercare `LlmChat(` in `server.py` e `bot_engine.py`.

### 8.4 Aggiungere una nuova categoria
1. Aggiungi entry in `CATEGORIES` (`server.py`).
2. Aggiungi feed RSS in `_fetch_headlines_for_category`.
3. (Opzionale) aggiungi voci in `hot_topics.md`.

### 8.5 Aggiungere pattern IA da bannare nei commenti bot
Modifica `BANNED_OPENERS` in `/app/backend/bot_personas.py`.

### 8.6 Aggiungere parole vietate
Aggiungi a `BLOCKED_WORDS` in `/app/backend/moderation.py`.

---

## 9. Costi e limiti

- **EMERGENT_LLM_KEY**: budget gestito lato Emergent (Profile → Manage plan →
  Universal Key). Auto top-up abilitabile.
- Budget stimato tipico: bot fleet a 30 attivi → ~1000-2000 chiamate/giorno →
  gestibile con Claude Haiku 4.5.
- Se il budget si esaurisce: le chiamate LLM restituiscono errore, i bot
  saltano il turno silenziosamente (nessuna crash), il generatore di faide
  logga warning e riprende al prossimo tick.

---

## 10. Debugging AI

### 10.1 Vedere l'ultima esecuzione scheduler
Query: `db.system_meta.findOne({key: "last_scheduler_run"})`

### 10.2 Log del bot engine
```
grep "bot_engine" /var/log/supervisor/backend.err.log
```

### 10.3 Test manuale di generazione faida
```
POST /api/admin/generate-daily
Headers: X-Admin-Key: <ADMIN_TOKEN>
Body: {"count": 1}
```

### 10.4 Test burst bot
```
POST /api/admin/bot/burst
Auth: admin logged-in
```

---

*Ultimo aggiornamento: Feb 2026. Aggiornare a ogni cambio di prompt/soglie/modello.*
