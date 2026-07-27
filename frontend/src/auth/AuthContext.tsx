import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { Platform } from 'react-native';
import { api, ApiError, getToken, setToken, User } from '../api';
import { getDeviceId } from '../utils/deviceId';

type AuthState = {
  user: User | null;
  loading: boolean;
  signup: (email: string, password: string, nickname: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  anonymous: (nickname: string) => Promise<void>;
  loginWithGoogle: () => Promise<void>;
  // Firebase email/password flows — Firebase handles the credentials
  // + verification email; our backend receives the ID token and mints
  // its own session on success.
  firebaseSignup: (email: string, password: string) => Promise<void>;
  firebaseLogin: (email: string, password: string) => Promise<void>;
  firebaseResendVerification: () => Promise<void>;
  firebasePasswordReset: (email: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
};

const AuthContext = createContext<AuthState | undefined>(undefined);

/**
 * Ensure the user object always carries a boolean `is_anonymous` flag derived
 * from `auth_provider`. Backend responses don't always include the flag but
 * the whole app (chat lockout, notifications, profile guards, etc.) treats
 * `user.is_anonymous` as the source of truth.
 */
function normalizeUser(u: User | null | undefined): User | null {
  if (!u) return null;
  const isAnon = u.is_anonymous === true || u.auth_provider === 'anonymous';
  return { ...u, is_anonymous: isAnon };
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  // Guard against processing the SAME `session_id` twice. In dev, React
  // StrictMode double-invokes useEffect. In prod, a fast remount (e.g.
  // Fast Refresh, route change during mount) can also cause the hash
  // parser to run twice before `history.replaceState` clears it. Both
  // paths would call `/auth/google-session` twice → duplicate work on
  // the backend, occasional consent-screen re-prompts on Emergent's
  // side, and noisy logs. The ref survives re-renders.
  const processedSessionIds = useRef<Set<string>>(new Set());
  const processingLock = useRef<Promise<boolean> | null>(null);

  const refreshMe = useCallback(async () => {
    // Read the persisted session token first.
    const t = await getToken();
    if (!t) { setUser(null); return; }
    // Retry `/auth/me` up to 3 times with a short backoff. Post-fork or
    // right after a deploy, the backend can respond with a 5xx/network
    // hiccup on the first call — silently trying again avoids kicking a
    // logged-in user back to the Google login flow (which is what forces
    // them through the Emergent consent screen again).
    const attempts = [0, 500, 1500];
    let lastStatus = -1;
    let lastErr: any = null;
    for (let i = 0; i < attempts.length; i++) {
      if (attempts[i] > 0) {
        await new Promise((r) => setTimeout(r, attempts[i]));
      }
      try {
        const res = await api.me();
        setUser(normalizeUser(res.user));
        return;
      } catch (e: any) {
        lastErr = e;
        lastStatus = e instanceof ApiError ? e.status : -1;
        // Terminal cases: don't retry.
        // - 401 → session is truly dead, drop the token
        // - 403 → app-level auth denial, treat as invalid too
        // - 404 → shouldn't happen but stop the loop
        if (lastStatus === 401 || lastStatus === 403 || lastStatus === 404) break;
      }
    }
    // We exhausted retries. Only clear the stored token when the server
    // explicitly told us the session is dead. On network/5xx/transient
    // failures we KEEP the token so the next cold start can recover the
    // session without forcing the user through Google + Emergent consent
    // all over again.
    if (lastStatus === 401 || lastStatus === 403) {
      await setToken(null);
      setUser(null);
    } else {
      console.warn('[Auth] refreshMe transient failure, keeping token', {
        status: lastStatus,
        message: lastErr?.message,
      });
      setUser(null);
    }
  }, []);

  // Extract `session_id` from a redirect URL and exchange it for a session token.
  // Supports both hash fragment (#session_id=...) and query param (?session_id=...).
  const processSessionUrl = useCallback(async (url: string): Promise<boolean> => {
    if (!url) return false;
    const m = url.match(/[#?&]session_id=([^&]+)/);
    if (!m || !m[1]) return false;
    const sid = decodeURIComponent(m[1]);
    // De-dupe: never process the same session_id twice, even across
    // React StrictMode double-invocations or Fast Refresh remounts.
    if (processedSessionIds.current.has(sid)) return true;
    // Serialize concurrent callers so a race between (mount, hot deep-link
    // listener, cold-start deep link) doesn't fire two backend requests.
    if (processingLock.current) {
      return processingLock.current;
    }
    const task = (async () => {
      try {
        const res = await api.googleSession(sid);
        processedSessionIds.current.add(sid);
        await setToken(res.token);
        setUser(normalizeUser(res.user));
        return true;
      } catch {
        return false;
      } finally {
        processingLock.current = null;
      }
    })();
    processingLock.current = task;
    return task;
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
    setUser(normalizeUser(res.user));
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
    // Attach a stable device_id so the backend can resurrect the same
    // anonymous user instead of minting a new user_id on every tap.
    // This is what prevents a single device from vote-stuffing a feud.
    let deviceId: string | undefined;
    try { deviceId = await getDeviceId(); } catch { deviceId = undefined; }
    const res = await api.anonymous(nickname, deviceId);
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
    // Route through `processSessionUrl` so the shared dedupe lock catches
    // the race between this call and the global deep-link listener (which
    // also fires when Android delivers the callback URL to the running
    // app). Without the lock we would call `/api/auth/google-session`
    // twice for the same `session_id` — that in turn nudges Emergent
    // into re-showing the consent screen on subsequent logins.
    const ok = await processSessionUrl(url);
    if (!ok) throw new Error('Session ID mancante nella risposta di Google');
  };

  const logout = async () => {
    try { await api.logout(); } catch {}
    try {
      // Fire-and-forget Firebase sign-out so a stale Firebase session
      // can't override our next login attempt. Import inline to keep
      // the auth module tree-shakeable on cold start.
      const fb = await import('./firebase');
      await fb.fbSignOut(fb.auth);
    } catch {}
    await setToken(null);
    setUser(null);
  };

  // ── Firebase email/password bridge ──
  const firebaseSignup = async (email: string, password: string) => {
    const fb = await import('./firebase');
    const cred = await fb.createUserWithEmailAndPassword(fb.auth, email.trim(), password);
    // Fire verification email immediately. The user cannot log in
    // until they click the link.
    try { await fb.sendEmailVerification(cred.user); } catch {}
    // We do NOT create the backend session yet — that happens on
    // login AFTER the user has verified their email.
  };

  const firebaseLogin = async (email: string, password: string) => {
    const fb = await import('./firebase');
    const cred = await fb.signInWithEmailAndPassword(fb.auth, email.trim(), password);
    // Refresh so the latest `emailVerified` propagates from Firebase.
    try { await fb.fbReload(cred.user); } catch {}
    if (!cred.user.emailVerified) {
      const err: any = new Error(
        "Email non verificata. Controlla la casella e clicca il link di conferma."
      );
      err.code = 'auth/email-not-verified';
      throw err;
    }
    const idToken = await cred.user.getIdToken(true);
    const res: any = await api.firebaseSession(idToken);
    await applyAuthResult(res);
  };

  const firebaseResendVerification = async () => {
    const fb = await import('./firebase');
    if (!fb.auth.currentUser) {
      throw new Error("Fai prima il login per reinviare l'email.");
    }
    await fb.sendEmailVerification(fb.auth.currentUser);
  };

  const firebasePasswordReset = async (email: string) => {
    const fb = await import('./firebase');
    await fb.sendPasswordResetEmail(fb.auth, email.trim());
  };

  return (
    <AuthContext.Provider value={{
      user, loading,
      signup, login, anonymous, loginWithGoogle,
      firebaseSignup, firebaseLogin, firebaseResendVerification, firebasePasswordReset,
      logout, refreshMe,
    }}>
      {children}
    </AuthContext.Provider>
  );
}
