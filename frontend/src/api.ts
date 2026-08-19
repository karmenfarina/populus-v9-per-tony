import { storage } from '@/src/utils/storage';

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;
export const TOKEN_KEY = 'faide_token';

export async function getToken(): Promise<string | null> {
  return (await storage.secureGet<string>(TOKEN_KEY, '')) || null;
}

export async function setToken(token: string | null) {
  if (token === null) {
    await storage.secureRemove(TOKEN_KEY);
  } else {
    await storage.secureSet(TOKEN_KEY, token);
  }
}

// ── Logout guard ──────────────────────────────────────────────────────
// Background pollers (NotificationsContext, MessagingContext) tick every
// 30s. If a tick fires between `setToken(null)` and the tree tear-down,
// the request goes out without a bearer header and the backend responds
// 401 "Missing bearer token" — surfaced by the promise chain as an
// uncaught error. Auth flows set this flag right before nuking the
// token so `request()` can short-circuit any in-flight request with a
// silent, self-contained ApiError instead of hitting the network.
let _isLoggedOut = false;
export function markLoggedOut() { _isLoggedOut = true; }
export function markLoggedIn() { _isLoggedOut = false; }

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

// Human-friendly Italian messages for Pydantic FastAPI validation errors.
// FastAPI returns `detail: [{loc, msg, type}, ...]` on 422 — the raw payload
// is unreadable, so we translate common cases here.
function friendlyValidation(item: any): string {
  if (!item || typeof item !== 'object') return '';
  const loc = Array.isArray(item.loc) ? item.loc : [];
  const field = String(loc[loc.length - 1] || '').toLowerCase();
  const type = String(item.type || '').toLowerCase();
  const msg = String(item.msg || '').toLowerCase();
  // Field-specific
  if (field === 'email') {
    if (type.includes('value_error') || type.includes('email') || msg.includes('email')) {
      return 'Inserisci un indirizzo email valido.';
    }
    if (type.includes('missing') || msg.includes('required')) return 'Inserisci la tua email.';
  }
  if (field === 'password') {
    if (type.includes('too_short') || msg.includes('at least') || msg.includes('min_length')) {
      return 'La password deve avere almeno 6 caratteri.';
    }
    if (type.includes('missing') || msg.includes('required')) return 'Inserisci la password.';
  }
  if (field === 'nickname') {
    if (type.includes('too_short') || msg.includes('at least 2')) {
      return 'Il nickname deve avere almeno 2 caratteri.';
    }
    if (type.includes('too_long') || msg.includes('at most 24')) {
      return 'Il nickname è troppo lungo (max 24 caratteri).';
    }
    if (type.includes('missing') || msg.includes('required')) return 'Inserisci un nickname.';
  }
  if (field === 'age') return "Inserisci un'età compresa tra 13 e 120.";
  if (field === 'region') return 'Seleziona una regione valida.';
  if (field === 'favorite_categories') return 'Seleziona almeno una categoria preferita.';
  // Fallback: uppercase the field name in Italian style
  const nice = field ? `Campo "${field}" non valido.` : (item.msg || 'Dati non validi.');
  return nice;
}

// Paths that DO NOT require a bearer token. Everything else is treated
// as authenticated — if there's no token in storage when we hit a
// non-listed path, we short-circuit with a client-side 401 instead of
// making a network call that would return the backend's raw
// "Missing bearer token" message (which used to bubble up as a red
// screen during logout).
const PUBLIC_PATH_PREFIXES = [
  '/auth/login',
  '/auth/signup',
  '/auth/register',
  '/auth/anonymous',
  '/auth/google',
  '/auth/emergent/session',
  '/auth/firebase',
  '/auth/session',
  '/auth/verify',
  '/auth/resend-verification',
  '/health',
  '/categories',
  '/professions',
  '/legal',
];
function requiresAuth(path: string): boolean {
  return !PUBLIC_PATH_PREFIXES.some((p) => path.startsWith(p));
}

async function request<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  // Retry SecureStore fino a 3 volte con backoff (0/100/300ms). Su
  // Android SecureStore ha race di write-behind + null transienti dopo
  // ~1min di uso continuativo: getToken() puo' ritornare null
  // MOMENTANEAMENTE anche se il token e' persistito correttamente.
  // Se short-circuitassimo un 401 su questo null fasullo, i consumer
  // (feed home, archivio, hashtag, categorie) catcherebbero e
  // farebbero setFeuds([]) — svuotando tutte le liste in cascata dopo
  // ~1 min di sessione. Bug riproducibile SOLO su APK Android e su
  // mobile browser deployed, non su preview web desktop.
  let token = await getToken();
  if (!token && !_isLoggedOut) {
    for (const delay of [100, 300]) {
      await _sleep(delay);
      token = await getToken();
      if (token) break;
    }
  }
  // Short-circuit SOLO su logout esplicito. NON piu' su "token null":
  // se dopo i retry il token e' ancora null MA l'utente non ha fatto
  // logout esplicito, si procede COMUNQUE senza header Authorization.
  // Il backend decidera' (401 reale se serve token, dati anonimi se
  // l'endpoint e' optional-auth). Questo elimina il 401 fasullo che
  // svuotava le liste sull'APK.
  //
  // NB: il gate `requiresAuth(path)` resta OBBLIGATORIO anche qui:
  // senza di esso, un utente che ha appena fatto logout esplicito
  // non potrebbe piu' fare login (perche' /auth/login e' pubblico ma
  // `_isLoggedOut` sarebbe true — se togliessimo il gate, lancerebbe
  // "Sessione terminata" anche sul login stesso, rompendo l'accesso
  // in APK come nel fix precedente).
  if (_isLoggedOut && requiresAuth(path)) {
    throw new ApiError(401, 'Sessione terminata');
  }
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as any),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  // ── Retry & timeout policy ─────────────────────────────────────────
  // Rete instabile (3G, tunnel, wifi che fa flip) è la causa nº1 di UX
  // percepita come "app rotta". Politica:
  //  • Timeout hard: 12s per GET, 20s per write (LLM/aggregations lente).
  //  • Retry SOLO su errori di rete / 5xx / 429 e SOLO per idempotenti
  //    (GET / HEAD / metodo assente = GET).
  //  • Backoff esponenziale con jitter: 300ms, 800ms, 1800ms.
  //  • Max 3 tentativi totali (2 retry).
  //  • Ogni retry emette un evento a `NetworkStatus` che alza il banner
  //    "connessione lenta" solo dopo il primo retry andato a segno.
  const method = (opts.method || 'GET').toUpperCase();
  const isIdempotent = method === 'GET' || method === 'HEAD';
  const timeoutMs = method === 'GET' ? 12_000 : 20_000;
  const maxAttempts = isIdempotent ? 3 : 1;

  let lastError: unknown = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetch(`${BASE}/api${path}`, {
        ...opts,
        headers,
        signal: controller.signal,
      });
    } catch (e: any) {
      clearTimeout(timer);
      // AbortError = timeout; TypeError = fetch-level failure (DNS,
      // TLS, offline, CORS, proxy). Both retryable.
      lastError = e;
      _networkStatus.notify({ kind: 'error', attempt, path });
      if (attempt < maxAttempts) {
        await _sleep(_backoffDelay(attempt));
        continue;
      }
      const isTimeout = e?.name === 'AbortError';
      throw new ApiError(
        0,
        isTimeout
          ? 'Connessione lenta o assente. Ricontrolla la rete e riprova.'
          : 'Impossibile contattare il server. Controlla la connessione.'
      );
    }
    clearTimeout(timer);

    // Retry-able server errors (5xx and 429). We do NOT retry 4xx
    // client errors — those are actual bugs / auth issues.
    if (isIdempotent && (res.status >= 500 || res.status === 429) && attempt < maxAttempts) {
      _networkStatus.notify({ kind: 'error', attempt, path });
      await _sleep(_backoffDelay(attempt));
      continue;
    }

    const text = await res.text();
    let data: any = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const rawDetail = data && (data.detail ?? data.message);
      let userMsg: string;
      if (Array.isArray(rawDetail)) {
        const items = rawDetail.map(friendlyValidation).filter(Boolean);
        userMsg = items.length > 0 ? items.join('\n') : 'Dati non validi.';
      } else if (typeof rawDetail === 'string' && rawDetail.trim()) {
        userMsg = rawDetail;
      } else if (res.status >= 500) {
        userMsg = 'Errore del server. Riprova tra poco.';
      } else if (res.status === 401) {
        userMsg = 'Credenziali non valide.';
      } else if (res.status === 403) {
        userMsg = "Non hai i permessi per questa operazione.";
      } else if (res.status === 404) {
        userMsg = 'Contenuto non trovato.';
      } else if (res.status === 409) {
        userMsg = 'Conflitto: la risorsa esiste già.';
      } else if (res.status === 429) {
        userMsg = 'Troppe richieste. Aspetta qualche istante e riprova.';
      } else {
        userMsg = `Errore imprevisto (HTTP ${res.status}).`;
      }
      throw new ApiError(res.status, userMsg);
    }
    // Success — if we had recovered from a network hiccup on a prior
    // attempt, notify success so the banner can hide itself.
    if (attempt > 1) _networkStatus.notify({ kind: 'recovered', path });
    return data as T;
  }
  // Non dovremmo mai arrivare qui; il loop torna o rilancia.
  throw (lastError as any) ?? new ApiError(0, 'Errore di rete.');
}

// ── Helpers per il retry + broadcast dello stato rete ───────────────
function _backoffDelay(attempt: number): number {
  // 300ms, 800ms, 1800ms + jitter fino a 200ms.
  const base = attempt === 1 ? 300 : attempt === 2 ? 800 : 1800;
  return base + Math.floor(Math.random() * 200);
}
function _sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Bus event-driven per stato rete. Le view (banner, toast) si
 * sottoscrivono; il wrapper `request()` emette `error`/`recovered`.
 * Nessuna dipendenza da NetInfo: distingue "rete assente" da "server
 * lento" osservando i retry effettivi (più affidabile del segnale OS
 * che a volte è ottimista).
 */
type NetEvent = { kind: 'error' | 'recovered'; attempt?: number; path?: string };
type NetListener = (e: NetEvent) => void;
export const _networkStatus = (() => {
  const listeners = new Set<NetListener>();
  return {
    subscribe(fn: NetListener) { listeners.add(fn); return () => listeners.delete(fn); },
    notify(e: NetEvent) { listeners.forEach((fn) => { try { fn(e); } catch {} }); },
  };
})();
export const networkStatus = _networkStatus;

export const api = {
  signup: (email: string, password: string, nickname: string) =>
    request('/auth/signup', { method: 'POST', body: JSON.stringify({ email, password, nickname }) }),
  login: (email: string, password: string) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  anonymous: (nickname: string, device_id?: string) =>
    request('/auth/anonymous', {
      method: 'POST',
      body: JSON.stringify(device_id ? { nickname, device_id } : { nickname }),
    }),
  googleSession: (session_id: string) =>
    request('/auth/google-session', { method: 'POST', body: JSON.stringify({ session_id }) }),
  firebaseSession: (id_token: string) =>
    request('/auth/firebase-session', { method: 'POST', body: JSON.stringify({ id_token }) }),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  categories: () => request('/categories'),
  feuds: (category?: string) => request(`/feuds${category && category !== 'all' ? `?category=${category}` : ''}`),
  feudsHype: () => request('/feuds/hype'),
  feud: (id: string) => request(`/feuds/${id}`),
  recordView: (id: string) =>
    request(`/feuds/${id}/view`, { method: 'POST' }).catch(() => null),
  hashtag: (tag: string) => request(`/hashtags/${encodeURIComponent(tag)}`),
  notifications: () => request('/notifications'),
  notificationsUnreadCount: () => request('/notifications/unread-count'),
  notificationsMarkRead: () => request('/notifications/mark-read', { method: 'POST' }),
  notificationMarkOneRead: (id: string) =>
    request(`/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }),
  registerPush: (platform: string, device_token: string) =>
    request('/register-push', {
      method: 'POST',
      body: JSON.stringify({ platform, device_token }),
    }),
  togglePush: (enabled: boolean) =>
    request('/settings/push', { method: 'POST', body: JSON.stringify({ enabled }) }),
  submitSupport: (data: {
    category: string; description: string; frequency: string;
    section: string; contact_email?: string;
  }) => request('/support/submit', { method: 'POST', body: JSON.stringify(data) }),
  vote: (id: string, side: 'A' | 'B') =>
    request(`/feuds/${id}/vote`, { method: 'POST', body: JSON.stringify({ side }) }),
  comments: (id: string, ownerUserId?: string) =>
    request(`/feuds/${id}/comments${ownerUserId ? `?owner_user_id=${encodeURIComponent(ownerUserId)}` : ''}`),
  // Analytics — record an app-open event (fired once per session on
  // launch). Fire-and-forget from the caller's perspective.
  analyticsAppOpen: () => request('/analytics/app-open', { method: 'POST' }).catch(() => null),
  addComment: (id: string, text: string) =>
    request(`/feuds/${id}/comments`, { method: 'POST', body: JSON.stringify({ text }) }),
  replies: (commentId: string) => request(`/comments/${commentId}/replies`),
  addReply: (commentId: string, text: string) =>
    request(`/comments/${commentId}/replies`, { method: 'POST', body: JSON.stringify({ text }) }),
  sponsors: (category?: string) =>
    request(`/sponsors${category && category !== 'all' ? `?category=${category}` : ''}`),
  history: (filter: 'all' | 'majority' | 'minority' = 'all') =>
    request(`/users/me/history?filter=${filter}`),
  search: (q: string) => request(`/search?q=${encodeURIComponent(q)}`),
  share: (id: string) => request(`/share/${id}`),
  updateProfile: (body: { age: number; sex: 'F'|'M'|'other'|'na'; region: string; favorite_categories: string[]; profession?: string; nickname?: string; display_name?: string }) =>
    request('/auth/me/profile', { method: 'PATCH', body: JSON.stringify(body) }),
  professions: () => request('/professions'),
  updateDetails: (body: { bio?: string; social_links?: Record<string, string> }) =>
    request('/auth/me/details', { method: 'PATCH', body: JSON.stringify(body) }),
  myPhotos: () => request('/auth/me/photos'),
  uploadPhoto: (data: string, original_data?: string) =>
    request('/auth/me/photos', {
      method: 'POST',
      body: JSON.stringify(original_data ? { data, original_data } : { data }),
    }),
  replacePhoto: (id: string, data: string, original_data?: string) =>
    request(`/auth/me/photos/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(original_data ? { data, original_data } : { data }),
    }),
  getPhotoOriginal: (id: string) => request(`/auth/me/photos/${id}/original`),
  setPrimaryPhoto: (id: string) => request(`/auth/me/photos/${id}/primary`, { method: 'PATCH' }),
  deletePhoto: (id: string) => request(`/auth/me/photos/${id}`, { method: 'DELETE' }),
  publicUser: (id: string) => request(`/users/${id}`),
  publicUserHistory: (id: string, filter: 'all' | 'majority' | 'minority' = 'all') =>
    request(`/users/${id}/history?filter=${filter}`),
  categoryBadges: (id: string) => request(`/users/${id}/category_badges`),
  // ─── Stories ───────────────────────────────────────────────
  storiesFeed: () => request('/stories/feed'),
  storiesByUser: (userId: string) => request(`/stories/user/${userId}`),
  createStory: (feud_id: string, comment?: string) =>
    request('/stories', { method: 'POST', body: JSON.stringify({ feud_id, comment: comment || null }) }),
  // Publish a "badge showcase" story — no feud reference, just the
  // unlocked category badge the user wants to flex. The backend
  // rejects tiers the user hasn't earned yet.
  createBadgeStory: (badge_category: string, badge_tier: number, comment?: string) =>
    request('/stories', {
      method: 'POST',
      body: JSON.stringify({
        kind: 'badge',
        badge_category,
        badge_tier,
        comment: comment || null,
      }),
    }),
  markStoryViewed: (story_id: string) =>
    request(`/stories/${story_id}/view`, { method: 'POST' }),
  deleteStory: (story_id: string) =>
    request(`/stories/${story_id}`, { method: 'DELETE' }),
  storiesHiddenViewers: () => request('/stories/hidden_viewers'),
  toggleHiddenViewer: (viewer_id: string, hidden: boolean) =>
    request(`/stories/hidden_viewers/${viewer_id}`, {
      method: 'PUT',
      body: JSON.stringify({ hidden }),
    }),
  replyToStory: (story_id: string, text: string) =>
    request(`/stories/${story_id}/reply`, { method: 'POST', body: JSON.stringify({ text }) }),
  archiveDates: (category?: string) =>
    request(`/feuds/archive/dates${category && category !== 'all' ? `?category=${category}` : ''}`),
  archiveFeuds: (date: string, category?: string) => {
    const params = new URLSearchParams({ date });
    if (category && category !== 'all') params.set('category', category);
    return request(`/feuds/archive?${params.toString()}`);
  },
  favorites: () => request('/favorites'),
  addFavorite: (id: string) => request(`/feuds/${id}/favorite`, { method: 'POST' }),
  removeFavorite: (id: string) => request(`/feuds/${id}/favorite`, { method: 'DELETE' }),
  feudStats: (id: string) => request(`/feuds/${id}/stats`),
  verifyEmail: (token: string) => request('/auth/verify-email', { method: 'POST', body: JSON.stringify({ token }) }),
  resendVerification: (email: string) => request('/auth/resend-verification', { method: 'POST', body: JSON.stringify({ email }) }),

  // --- Messaging ---
  messagesUnreadCount: () => request('/messages/unread-count'),
  messagesMarkAllRead: () => request('/messages/mark-all-read', { method: 'POST' }),
  conversations: () => request('/messages/conversations'),
  clearConversation: (otherUserId: string) =>
    request(`/messages/with/${otherUserId}`, { method: 'DELETE' }),
  messagesWith: (userId: string, before?: string, limit = 50) => {
    const p = new URLSearchParams();
    if (before) p.set('before', before);
    p.set('limit', String(limit));
    return request(`/messages/with/${userId}?${p.toString()}`);
  },
  sendMessage: (recipient_id: string, text?: string, image_data?: string, shared_feud_id?: string) =>
    request('/messages/send', { method: 'POST', body: JSON.stringify({ recipient_id, text, image_data, shared_feud_id }) }),
  shareFeudToUsers: (feud_id: string, recipient_ids: string[], text?: string) =>
    request(`/feuds/${feud_id}/share`, { method: 'POST', body: JSON.stringify({ recipient_ids, text }) }),
  shareSuggestions: (limit = 21) => request(`/messages/share-suggestions?limit=${limit}`),
  searchUsers: (q: string, limit = 20) =>
    request(`/search/users?q=${encodeURIComponent(q)}&limit=${limit}`),
  // Proximity-ranked @mention autocomplete. When `q` is empty (user just
  // typed `@`) returns the viewer's closest contacts. When `q` is a few
  // chars, filters by substring while still boosting people they interact
  // with — Cerchia > DMs > reply exchanges > co-commenters > everyone.
  mentionSuggest: (q: string, feudId?: string, limit = 8) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (feudId) params.set('feud_id', feudId);
    params.set('limit', String(limit));
    return request(`/mentions/suggest?${params.toString()}`);
  },
  // Suggested friends for the Cerchia — union of DM contacts, friends-of-
  // friends and co-commenters, ranked by a weighted score. Returns
  // hydrated user rows with a `reasons` string list ("chat",
  // "amici_di_amici", "commenti_in_comune") the UI can render as chips.
  circleSuggestions: (limit = 20) =>
    request(`/circle/suggestions?limit=${limit}`),
  markConversationRead: (userId: string) =>
    request(`/messages/with/${userId}/read`, { method: 'POST' }),
  reactMessage: (messageId: string, emoji: string) =>
    request(`/messages/${messageId}/react`, { method: 'POST', body: JSON.stringify({ emoji }) }),
  deleteMessage: (messageId: string) =>
    request(`/messages/${messageId}`, { method: 'DELETE' }),
  blockUser: (userId: string) => request(`/users/${userId}/block`, { method: 'POST' }),
  unblockUser: (userId: string) => request(`/users/${userId}/block`, { method: 'DELETE' }),
  deleteComment: (commentId: string) => request(`/comments/${commentId}`, { method: 'DELETE' }),
  deleteReply: (replyId: string) => request(`/replies/${replyId}`, { method: 'DELETE' }),
  // Cerchia del Gossip (friend circle)
  circleAdd: (friendId: string) => request(`/circle/${friendId}`, { method: 'POST' }),
  circleRemove: (friendId: string) => request(`/circle/${friendId}`, { method: 'DELETE' }),
  circleStatus: (otherUserId: string) => request(`/circle/me/status/${otherUserId}`),
  circleSetPrivacy: (isPrivate: boolean) =>
    request('/circle/me/privacy', { method: 'PATCH', body: JSON.stringify({ private: isPrivate }) }),
  circleGet: (ownerId: string, q?: string) =>
    request(`/users/${ownerId}/circle${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  myBlocks: () => request('/users/me/blocks'),
  // Voting-history privacy: two independent toggles. `generic` covers all
  // random users; `mutual` covers members of the "cerchia bilaterale"
  // (users I have in my circle who also have me in theirs).
  updateHistoryPrivacy: (patch: { generic?: boolean; mutual?: boolean }) =>
    request('/users/me/history-privacy', { method: 'PATCH', body: JSON.stringify(patch) }),
  // Terms & Privacy Policy — displayed on first login and any time the
  // stored `terms_accepted_version` diverges from the server-side one.
  getLegalTerms: () => request('/legal/terms'),
  // NDA (Accordo di Riservatezza) — separate document sub-signed on the
  // same onboarding screen as the ToS. `terms_accepted` at /auth/me is
  // TRUE only when BOTH documents are accepted at their current versions.
  getLegalNda: () => request('/legal/nda'),
  acceptLegalTerms: (version: string) =>
    request('/users/me/accept-terms', { method: 'POST', body: JSON.stringify({ version }) }),
  // Combined acceptance — a single round-trip that stamps both docs.
  acceptLegalBoth: (termsVersion: string, ndaVersion: string) =>
    request('/users/me/accept-terms', {
      method: 'POST',
      body: JSON.stringify({ version: termsVersion, nda_version: ndaVersion }),
    }),
  acceptLegalNda: (ndaVersion: string) =>
    request('/users/me/accept-terms', { method: 'POST', body: JSON.stringify({ nda_version: ndaVersion }) }),
  // AI faction summary — regenerated on every call from the latest
  // visible comments so the synthesis sharpens as more people chime in.
  feudAiSummary: (feudId: string) =>
    request(`/feuds/${feudId}/ai-summary`, { method: 'POST' }),
  reportUser: (userId: string, reason: string, message_id?: string) =>
    request(`/users/${userId}/report`, { method: 'POST', body: JSON.stringify({ reason, message_id }) }),
  // ─── Founder-admin: edit / hide / restore feuds ─────────────────
  // Only usable by the account matching FOUNDER_ADMIN_EMAIL on the
  // backend. Any other caller receives 403.
  adminEditFeud: (feudId: string, patch: { title?: string; question?: string; category?: string; summary?: string; party_a?: string; party_b?: string }) =>
    request(`/feuds/${feudId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  adminHideFeud: (feudId: string) =>
    request(`/feuds/${feudId}`, { method: 'DELETE' }),
  adminRestoreFeud: (feudId: string) =>
    request(`/feuds/${feudId}/restore`, { method: 'POST' }),
  adminHiddenFeuds: () => request('/admin/hidden-feuds'),
  // ─── Founder-admin: bot fleet controls ─────────────────────────
  // Master switch + numeric slider (0..100) + immediate burst.
  // Uses X-Admin-Key on the fetch layer (see admin.tsx callers).
  adminBotState: (adminKey: string) =>
    fetch(`${process.env.EXPO_PUBLIC_BACKEND_URL}/api/admin/bots/state`, {
      headers: { 'X-Admin-Key': adminKey },
    }).then(async (r) => {
      const t = await r.text();
      const d = t ? JSON.parse(t) : null;
      if (!r.ok) throw new ApiError(r.status, d?.detail || `HTTP ${r.status}`);
      return d;
    }),
  adminBotToggle: (adminKey: string, enabled: boolean) =>
    fetch(`${process.env.EXPO_PUBLIC_BACKEND_URL}/api/admin/bots/toggle`, {
      method: 'POST',
      headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }).then(async (r) => {
      const t = await r.text();
      const d = t ? JSON.parse(t) : null;
      if (!r.ok) throw new ApiError(r.status, d?.detail || `HTTP ${r.status}`);
      return d;
    }),
  adminBotSetCount: (adminKey: string, count: number) =>
    fetch(`${process.env.EXPO_PUBLIC_BACKEND_URL}/api/admin/bots/count`, {
      method: 'POST',
      headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ count }),
    }).then(async (r) => {
      const t = await r.text();
      const d = t ? JSON.parse(t) : null;
      if (!r.ok) throw new ApiError(r.status, d?.detail || `HTTP ${r.status}`);
      return d;
    }),
  adminBotBurst: (adminKey: string) =>
    fetch(`${process.env.EXPO_PUBLIC_BACKEND_URL}/api/admin/bots/burst`, {
      method: 'POST',
      headers: { 'X-Admin-Key': adminKey },
    }).then(async (r) => {
      const t = await r.text();
      const d = t ? JSON.parse(t) : null;
      if (!r.ok) throw new ApiError(r.status, d?.detail || `HTTP ${r.status}`);
      return d;
    }),
  // Wipe existing bot-authored artefacts. Payload:
  //   kinds: string[] — subset of ["comments","stories","votes"].
  // Default (empty payload) wipes comments+stories only, which is what
  // the founder needs after a persona rename.
  adminBotReset: (adminKey: string, kinds: string[] = ['comments', 'stories']) =>
    fetch(`${process.env.EXPO_PUBLIC_BACKEND_URL}/api/admin/bots/reset`, {
      method: 'POST',
      headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ kinds }),
    }).then(async (r) => {
      const t = await r.text();
      const d = t ? JSON.parse(t) : null;
      if (!r.ok) throw new ApiError(r.status, d?.detail || `HTTP ${r.status}`);
      return d;
    }),
};

export type User = {
  user_id: string;
  email: string | null;
  nickname: string;
  auth_provider: string;
  majority_votes: number;
  minority_votes: number;
  total_votes: number;
  age?: number | null;
  sex?: 'F' | 'M' | 'other' | 'na' | null;
  region?: string | null;
  profession?: string | null;
  display_name?: string | null;
  favorite_categories?: string[];
  push_notifications?: boolean;
  history_public_generic?: boolean;
  history_public_mutual?: boolean;
  terms_accepted?: boolean;
  terms_accepted_version?: string | null;
  terms_accepted_at?: string | null;
  nda_accepted_version?: string | null;
  nda_accepted_at?: string | null;
  onboarding_completed?: boolean;
  bio?: string | null;
  social_links?: { instagram?: string; tiktok?: string; twitter?: string; youtube?: string; website?: string };
  primary_photo_id?: string | null;
  // Hydrated by `/auth/me` so the app can render the avatar before
  // `/stories/feed` finishes — eliminates the initials-then-photo
  // flash on cold start. Present only when the user has an avatar.
  primary_photo?: {
    photo_id: string;
    data: string;
    mime?: string;
  } | null;
  photos_count?: number;
  badge: {
    unlocked: boolean;
    type?: 'buon_senso' | 'bastian_contrario';
    label: string;
    majority?: number;
    minority?: number;
    progress?: number;
    target?: number;
  } | null;
};

export type UserPhoto = { photo_id: string; data: string; position: number; is_primary?: boolean; created_at?: string };
export type PublicUser = {
  user_id: string;
  nickname: string;
  auth_provider?: string;
  is_anonymous?: boolean;
  push_notifications?: boolean;
  bio?: string | null;
  social_links?: User['social_links'];
  primary_photo_id?: string | null;
  photos: UserPhoto[];
  total_votes: number;
  majority_votes: number;
  minority_votes: number;
  badge: User['badge'];
  profession?: string | null;
  display_name?: string | null;
  region?: string | null;
};

export type Sponsor = {
  sponsor_id: string;
  category: string;
  sponsor: string;
  headline: string;
  cta: string;
  image_url: string;
};

export type HistoryItem = {
  feud_id: string;
  title: string;
  category_label: string;
  party_a: string;
  party_b: string;
  side_voted: 'A' | 'B';
  winning_side: 'A' | 'B' | null;
  aligned: boolean;
  feud_deleted?: boolean;
  voted_at: string;
};

export type FeudMedia = {
  type: 'youtube' | 'video' | 'image';
  video_id?: string;
  embed_url?: string;
  watch_url?: string;
  video_url?: string;
  video_type?: string;
  thumbnail?: string;
  image_url?: string;
  channel?: string;
  video_title?: string;
  source_domain?: string;
  provenance?: string;
};

export type Feud = {
  feud_id: string;
  category: string;
  category_label: string;
  title: string;
  party_a: string;
  party_b: string;
  summary: string;
  // Optional context/backstory generated by AI. Only present on feuds
  // created after the context feature landed. When null/missing the UI
  // hides the "i" info toggle button on the feud detail screen.
  context_text?: string | null;
  question: string;
  image_url: string;
  media?: FeudMedia | null;
  votes_a: number | null;
  votes_b: number | null;
  total_votes: number;
  pct_a: number | null;
  pct_b: number | null;
  revealed: boolean;
  my_vote?: 'A' | 'B' | null;
  my_vote_changes?: number;
  my_vote_changes_left?: number;
  sources?: { title: string; link: string; source: string }[];
  hashtag?: string;
  hashtag_display?: string;
  is_favorite?: boolean;
  /** Only present in HYPE payloads — visible-comment + reply counts for
   *  the card badge (so the user sees real engagement before voting). */
  hype_comments?: number;
  hype_engagement?: number;
  /** Founder-admin only. Flags whether the caller is the admin viewer
   *  (server-computed) and whether this feud is currently soft-deleted. */
  is_admin_viewer?: boolean;
  is_hidden?: boolean;
  edited_at?: string | null;
  hidden_at?: string | null;
};

export type Mention = {
  user_id: string;
  nickname: string;
};

export type Comment = {
  comment_id: string;
  feud_id: string;
  user_id: string;
  nickname: string;
  side: 'A' | 'B';
  nickname_side?: 'A' | 'B';
  text: string;
  reply_count?: number;
  mentions?: Mention[];
  created_at: string;
};

export type Reply = Comment & { reply_id: string; comment_id: string };

export type FeudStatsSide = {
  total: number;
  age: Record<string, number>;
  region: { Nord: number; Centro: number; Sud: number; unknown: number };
  sex: { F: number; M: number; other: number; unknown: number };
};
export type FeudStats = {
  feud_id: string;
  total_votes: number;
  sides: { A: FeudStatsSide; B: FeudStatsSide };
};

// --- Messaging types ---
export type MiniUser = {
  user_id: string;
  nickname: string;
  primary_photo_id?: string | null;
  photo_data?: string | null;
  display_name?: string | null;
};

export type SharedFeud = {
  feud_id: string;
  title?: string | null;
  image_url?: string | null;
  category?: string | null;
  category_label?: string | null;
};

// Snapshot of a story the DM refers to. Attached by the backend when
// a user replies to a story so the chat can render a tappable preview
// that reopens the original story viewer as long as expires_at > now.
export type StoryRef = {
  story_id: string;
  author_id: string;
  feud_id?: string | null;
  feud_title?: string | null;
  feud_image_url?: string | null;
  category_label?: string | null;
  comment?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
};

export type ChatMessage = {
  message_id: string;
  conversation_id: string;
  sender_id: string;
  recipient_id: string;
  text: string | null;
  image_data: string | null;
  shared_feud?: SharedFeud | null;
  story_ref?: StoryRef | null;
  kind: 'text' | 'image' | 'mixed' | 'shared_feud' | 'story_reply';
  reactions: Record<string, string>;
  created_at: string;
  read_at: string | null;
  deleted?: boolean;
};

export type Conversation = {
  conversation_id: string;
  other_user: MiniUser;
  last_message_at: string | null;
  last_message_preview: string;
  last_sender_id: string | null;
  unread: number;
};

