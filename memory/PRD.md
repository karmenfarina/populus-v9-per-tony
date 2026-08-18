# App di Faide Gossip — PRD

## Overview
Italian mobile app (Expo) where daily controversies from news/gossip are turned into two-sided polls. Users pick a side (Team A red / Team B yellow), read a live-updated summary, comment, and reply to comments. Usernames are color-coded by voted side. After 5+ votes, users unlock a badge: **Utente Bastian Contrario** (mostly with minority) or **Utente di Buon Senso** (mostly with majority).

## Tech Stack
- Backend: FastAPI + MongoDB (motor async), JWT auth, bcrypt, httpx.
- Frontend: Expo Router (React Native), SecureStore for tokens, react-native-safe-area-context, expo-linear-gradient, expo-haptics, @expo/vector-icons.
- AI: Claude Sonnet 4.6 via `emergentintegrations` + `EMERGENT_LLM_KEY` for daily controversy generation.

## Auth (3 flows)
- Email + Password (JWT).
- Anonymous nickname (JWT).
- Emergent-managed Google OAuth (WebBrowser + session_id → session_token).

## Categories
Politica, Programmi TV, Musica, Sport, Cinema, Social, Gossip, Cronaca, Tech.

## Key API endpoints (all under `/api`)
- `POST /auth/signup|login|anonymous|google-session|logout`, `GET /auth/me`
- `GET /categories`, `GET /professions`
- `GET /feuds?category=`, `GET /feuds/{id}`
- `POST /feuds/{id}/vote` (side A|B)
- `GET|POST /feuds/{id}/comments`
- `GET|POST /comments/{id}/replies`
- `POST /admin/generate-daily?count=N` (AI generation)
- `PATCH /auth/me/profile` (age, sex, region, favorite_categories, profession)

## Screens
- `/auth` — three-tab entry (EMAIL / GOOGLE / ANONIMO).
- `/(tabs)` — Home feed with sticky category chip row + full-width feud cards with 50/50 split poll preview.
- `/(tabs)/profile` — Nickname, badge state, achievement badges collection, profession picker, voting stats.
- `/feud/[id]` — Hero image, article summary, big split poll, two-column comments (Pro A / Pro B) with reply threads.
- `/onboarding` — 4 steps: categorie preferite → età+sesso → regione → professione.

## Badge rules
- Alignment badge (buon_senso / bastian_contrario): unlocked at `total_votes >= 10`, mutually exclusive.

## Anon → Registered migration
When a user is anonymous and signs up (fresh email), logs in (existing email), or Google-logs-in:
- Fresh target → in-place upgrade preserves user_id and all engagement.
- Existing target → votes/comments/replies/messages reassigned then anon user deleted.
- For email signup where target is already taken (unverified), migration is deferred until verify-email (via `pending_migration_from` on the verification token).

## Deferred
- Real ad banners targeted per category (AdMob requires native build).
- Push notifications for new daily feuds.

## Founder-admin controls (iter123)
The email `carlofarinapayme@gmail.com` unlocks moderation actions on every
feud detail screen (RBAC hardcoded — no generic `is_admin` flag):
- Edit title/question/category (`PATCH /api/feuds/{id}`).
- Soft-hide (`DELETE /api/feuds/{id}`) — the feud vanishes from all public
  feeds (live/hype/archive/search/hashtag/favorites) but remains visible
  to the admin with a "FAIDA NASCOSTA" banner and a Restore action.
- Restore (`POST /api/feuds/{id}/restore`) — flips visibility back on.
- Admin tab "NASCOSTE" (`GET /api/admin/hidden-feuds`) lists every hidden
  feud with a one-tap restore button.

## Notification deep-link scroll (iter123)
Push notifications for @-mention comments/replies already carry a
`comment_id` inside the deeplink (`?comment=…&side=…`). The feud detail
screen now:
1. Activates the correct side tab based on the param (or auto-detects it).
2. Auto-expands the reply thread of the target comment.
3. Uses `measureLayout(ScrollView)` on the target row's View ref to
   `scrollTo({ y })` precisely, retrying briefly while the layout
   settles after the tab flip.
4. Flashes a bright brand-secondary border on the target row for ~2.5s
   so the user's eye is drawn to it immediately.
