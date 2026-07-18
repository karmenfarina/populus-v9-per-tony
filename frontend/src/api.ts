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

async function request<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as any),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${BASE}/api${path}`, { ...opts, headers });
  } catch {
    // Fetch itself failed → offline, DNS, CORS block, etc.
    throw new ApiError(0, 'Impossibile contattare il server. Controlla la connessione.');
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const rawDetail = data && (data.detail ?? data.message);
    let userMsg: string;
    if (Array.isArray(rawDetail)) {
      // Pydantic 422 — extract every field message.
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
  return data as T;
}

export const api = {
  signup: (email: string, password: string, nickname: string) =>
    request('/auth/signup', { method: 'POST', body: JSON.stringify({ email, password, nickname }) }),
  login: (email: string, password: string) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  anonymous: (nickname: string) =>
    request('/auth/anonymous', { method: 'POST', body: JSON.stringify({ nickname }) }),
  googleSession: (session_id: string) =>
    request('/auth/google-session', { method: 'POST', body: JSON.stringify({ session_id }) }),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  categories: () => request('/categories'),
  feuds: (category?: string) => request(`/feuds${category && category !== 'all' ? `?category=${category}` : ''}`),
  feud: (id: string) => request(`/feuds/${id}`),
  recordView: (id: string) =>
    request(`/feuds/${id}/view`, { method: 'POST' }).catch(() => null),
  hashtag: (tag: string) => request(`/hashtags/${encodeURIComponent(tag)}`),
  notifications: () => request('/notifications'),
  notificationsUnreadCount: () => request('/notifications/unread-count'),
  notificationsMarkRead: () => request('/notifications/mark-read', { method: 'POST' }),
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
  comments: (id: string) => request(`/feuds/${id}/comments`),
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
  updateProfile: (body: { age: number; sex: 'F'|'M'|'other'|'na'; region: string; favorite_categories: string[]; profession?: string; nickname?: string }) =>
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
  conversations: () => request('/messages/conversations'),
  messagesWith: (userId: string, before?: string, limit = 50) => {
    const p = new URLSearchParams();
    if (before) p.set('before', before);
    p.set('limit', String(limit));
    return request(`/messages/with/${userId}?${p.toString()}`);
  },
  sendMessage: (recipient_id: string, text?: string, image_data?: string) =>
    request('/messages/send', { method: 'POST', body: JSON.stringify({ recipient_id, text, image_data }) }),
  markConversationRead: (userId: string) =>
    request(`/messages/with/${userId}/read`, { method: 'POST' }),
  reactMessage: (messageId: string, emoji: string) =>
    request(`/messages/${messageId}/react`, { method: 'POST', body: JSON.stringify({ emoji }) }),
  deleteMessage: (messageId: string) =>
    request(`/messages/${messageId}`, { method: 'DELETE' }),
  blockUser: (userId: string) => request(`/users/${userId}/block`, { method: 'POST' }),
  unblockUser: (userId: string) => request(`/users/${userId}/block`, { method: 'DELETE' }),
  myBlocks: () => request('/users/me/blocks'),
  reportUser: (userId: string, reason: string, message_id?: string) =>
    request(`/users/${userId}/report`, { method: 'POST', body: JSON.stringify({ reason, message_id }) }),
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
  favorite_categories?: string[];
  push_notifications?: boolean;
  onboarding_completed?: boolean;
  bio?: string | null;
  social_links?: { instagram?: string; tiktok?: string; twitter?: string; youtube?: string; website?: string };
  primary_photo_id?: string | null;
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
};

export type ChatMessage = {
  message_id: string;
  conversation_id: string;
  sender_id: string;
  recipient_id: string;
  text: string | null;
  image_data: string | null;
  kind: 'text' | 'image' | 'mixed';
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

