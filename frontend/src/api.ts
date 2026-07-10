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

async function request<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as any),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${BASE}/api${path}`, { ...opts, headers });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
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
};

export type User = {
  user_id: string;
  email: string | null;
  nickname: string;
  auth_provider: string;
  majority_votes: number;
  minority_votes: number;
  total_votes: number;
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
  voted_at: string;
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
  votes_a: number | null;
  votes_b: number | null;
  total_votes: number;
  pct_a: number | null;
  pct_b: number | null;
  revealed: boolean;
  my_vote?: 'A' | 'B' | null;
};

export type Comment = {
  comment_id: string;
  feud_id: string;
  user_id: string;
  nickname: string;
  side: 'A' | 'B';
  text: string;
  reply_count?: number;
  created_at: string;
};

export type Reply = Comment & { reply_id: string; comment_id: string };
