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

  // Extract `session_id` from a redirect URL and exchange it for a session token.
  // Supports both hash fragment (#session_id=...) and query param (?session_id=...).
  const processSessionUrl = useCallback(async (url: string): Promise<boolean> => {
    if (!url) return false;
    const m = url.match(/[#?&]session_id=([^&]+)/);
    if (!m || !m[1]) return false;
    try {
      const sid = decodeURIComponent(m[1]);
      const res = await api.googleSession(sid);
      await setToken(res.token);
      setUser(res.user);
      return true;
    } catch {
      return false;
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
          const hash = window.location.hash || window.location.search || '';
          if (await processSessionUrl(hash)) {
            try {
              const url = window.location.pathname;
              window.history.replaceState(null, '', url);
            } catch { /* ignore */ }
            setLoading(false);
            return;
          }
        } catch { /* fall through to refreshMe */ }
      }
      // Mobile cold-start: if the app was launched by a deep link containing
      // a session_id (Expo Go/native standalone build), pick it up now.
      if (Platform.OS !== 'web') {
        try {
          const initial = await Linking.getInitialURL();
          if (initial && await processSessionUrl(initial)) {
            setLoading(false);
            return;
          }
        } catch { /* ignore */ }
      }
      await refreshMe();
      setLoading(false);
    })();
  }, [refreshMe, processSessionUrl]);

  // Hot deep-link listener: if the app is already running and the OS delivers
  // a deep link (e.g. user completed OAuth in an in-app browser), process the
  // session_id immediately. Only for mobile — web uses URL hash on mount.
  useEffect(() => {
    if (Platform.OS === 'web') return;
    const sub = Linking.addEventListener('url', async ({ url }) => {
      if (url) await processSessionUrl(url);
    });
    return () => { try { sub.remove(); } catch { /* ignore */ } };
  }, [processSessionUrl]);

  const applyAuthResult = async (res: { token: string; user: User }) => {
    await setToken(res.token);
    setUser(res.user);
  };

  const signup = async (email: string, password: string, nickname: string) => {
    const res: any = await api.signup(email, password, nickname);
    // New flow: signup does NOT return a session token. The backend just
    // sends the verification email; the caller must handle the
    // `requires_verification` response and show a "check your inbox" state.
    if (res?.requires_verification) {
      const err: any = new Error(res.message || 'Verifica la tua email per completare la registrazione.');
      err.requires_verification = true;
      err.email = res.email || email;
      throw err;
    }
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

    // On mobile the session_id can arrive via two paths:
    // 1) `result.url` returned by openAuthSessionAsync (happy path)
    // 2) A deep link delivered to the running app while the browser is still open
    //    (some Android/Expo Go configurations bypass the WebBrowser return channel)
    // We watch both simultaneously and race whichever wins.
    let handled = false;
    const linkPromise = new Promise<string | null>((resolve) => {
      const sub = Linking.addEventListener('url', ({ url }) => {
        if (!handled && url && /session_id=/.test(url)) {
          handled = true;
          try { sub.remove(); } catch { /* ignore */ }
          resolve(url);
        }
      });
      // Timeout fallback so this listener doesn't hang forever if the user aborts.
      setTimeout(() => { try { sub.remove(); } catch { /* ignore */ } resolve(null); }, 300000);
    });

    const browserPromise = WebBrowser.openAuthSessionAsync(authUrl, redirectUrl)
      .then((res) => (res.type === 'success' && res.url ? res.url : null));

    const url = (await Promise.race([browserPromise, linkPromise])) as string | null;
    handled = true;
    if (!url) throw new Error('Login Google annullato');
    const hashMatch = url.match(/[#?&]session_id=([^&]+)/);
    if (!hashMatch) throw new Error('Session ID mancante nella risposta di Google');
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
