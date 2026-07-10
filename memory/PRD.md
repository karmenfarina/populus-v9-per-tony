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
Politica, Programmi TV, Musica, Sport, Cinema, Social, Gossip.

## Key API endpoints (all under `/api`)
- `POST /auth/signup|login|anonymous|google-session|logout`, `GET /auth/me`
- `GET /categories`
- `GET /feuds?category=`, `GET /feuds/{id}`
- `POST /feuds/{id}/vote` (side A|B)
- `GET|POST /feuds/{id}/comments`
- `GET|POST /comments/{id}/replies`
- `POST /admin/generate-daily?count=N` (AI generation)

## Screens
- `/auth` — three-tab entry (EMAIL / GOOGLE / ANONIMO).
- `/(tabs)` — Home feed with sticky category chip row + full-width feud cards with 50/50 split poll preview.
- `/(tabs)/profile` — Nickname, badge state (locked / bastian contrario / buon senso), voting stats.
- `/feud/[id]` — Hero image, article summary, big split poll, two-column comments (Pro A / Pro B) with reply threads.

## Badge rules
- Locked until `total_votes >= 5`.
- Compare cumulative majority vs minority votes on each vote → recompute alignment.

## Deferred
- Real ad banners targeted per category.
- Push notifications for new daily feuds.
