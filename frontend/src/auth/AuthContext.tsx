import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { Platform } from 'react-native';
import { api, getToken, setToken, User } from '../api';

type AuthState = {
  user: User | null;
  loading: boolean;
  signup: (email: string, password: string, nickname: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  anonymous: (nickname: string) => Promise<void>;
  loginWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
};

const AuthContext = createContext<AuthState | undefined>(undefined);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      const t = await getToken();
      if (!t) { setUser(null); return; }
      const res = await api.me();
      setUser(res.user);
    } catch {
      await setToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      // Web-only: Emergent Google Auth redirects back with `#session_id=...`
      // in the URL fragment. Since fragments never reach the backend, we must
      // parse them client-side, exchange for a first-party session, then wipe
      // the fragment via `history.replaceState` so the token doesn't linger.
      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        try {
          const hash = window.location.hash || '';
          const m = hash.match(/[#&]session_id=([^&]+)/);
          if (m && m[1]) {
            const sid = decodeURIComponent(m[1]);
            const res = await api.googleSession(sid);
            await setToken(res.token);
            setUser(res.user);
            // Strip the fragment from the address bar (keeps path + query).
            try {
              const url = window.location.pathname + window.location.search;
              window.history.replaceState(null, '', url);
            } catch { /* ignore */ }
            setLoading(false);
            return;
          }
        } catch {
          // Fall through to normal refreshMe if the callback exchange failed.
        }
      }
      await refreshMe();
      setLoading(false);
    })();
  }, [refreshMe]);

  const applyAuthResult = async (res: { token: string; user: User }) => {
    await setToken(res.token);
    setUser(res.user);
  };

  const signup = async (email: string, password: string, nickname: string) => {
    const res = await api.signup(email, password, nickname);
    await applyAuthResult(res);
  };
  const login = async (email: string, password: string) => {
    const res = await api.login(email, password);
    await applyAuthResult(res);
  };
  const anonymous = async (nickname: string) => {
    const res = await api.anonymous(nickname);
    await applyAuthResult(res);
  };

  const loginWithGoogle = async () => {
    const redirectUrl =
      Platform.OS === 'web'
        ? (typeof window !== 'undefined' ? window.location.origin + '/' : 'https://localhost/')
        : Linking.createURL('');
    const authUrl = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;

    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined') window.location.href = authUrl;
      return;
    }

    const result = await WebBrowser.openAuthSessionAsync(authUrl, redirectUrl);
    if (result.type !== 'success' || !result.url) throw new Error('Login Google annullato');
    const url = result.url;
    const hashMatch = url.match(/[#?]session_id=([^&]+)/);
    if (!hashMatch) throw new Error('Session ID mancante');
    const session_id = decodeURIComponent(hashMatch[1]);
    const res = await api.googleSession(session_id);
    await applyAuthResult(res);
  };

  const logout = async () => {
    try { await api.logout(); } catch {}
    await setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, signup, login, anonymous, loginWithGoogle, logout, refreshMe }}>
      {children}
    </AuthContext.Provider>
  );
}
