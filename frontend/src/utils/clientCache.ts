/**
 * Cache in-memory client-side con TTL e supporto stale-while-error.
 *
 * Rationale:
 *  - `cachedGet(key, ttl, factory)`: se in TTL, torna il valore memorizzato.
 *  - Se scaduto o assente, esegue `factory()`. Se `factory()` va a buon fine,
 *    aggiorna la cache. Se fallisce (rete offline, timeout, 5xx dopo i retry
 *    del wrapper API), e la cache contiene ancora una vecchia risposta,
 *    restituisce quella **stale** invece di propagare l'errore.
 *    L'utente vede l'ultimo contenuto valido invece di uno schermo vuoto.
 *  - `invalidateCache(prefix)` rimuove le entry con la chiave che comincia
 *    per `prefix`.
 *
 * Nota: la cache è per-sessione (RAM). Per persistenza cross-launch si
 * potrebbe fare un `saveToStorage()` async ma non è ancora necessario.
 */
type Entry = { value: unknown; expires: number; storedAt: number };
const store: Map<string, Entry> = new Map();

// Entry stale sono considerate utilizzabili solo se scadute da meno di
// STALE_MAX_MS. Oltre, sono troppo vecchie per essere mostrate anche in
// caso di errore (il contenuto sarebbe fuorviante).
const STALE_MAX_MS = 5 * 60_000; // 5 minuti

export async function cachedGet<T>(
  key: string,
  ttlMs: number,
  factory: () => Promise<T>
): Promise<T> {
  const now = Date.now();
  const hit = store.get(key);
  if (hit && hit.expires > now) return hit.value as T;

  try {
    const value = await factory();
    store.set(key, { value, expires: now + Math.max(0, ttlMs), storedAt: now });
    return value;
  } catch (err) {
    // Stale-while-error: se abbiamo un valore vecchio ma non troppo,
    // meglio mostrarlo che rompere la UI.
    if (hit && now - hit.storedAt < STALE_MAX_MS) {
      // Emette warning in dev per capire quando questa branch scatta
      if (__DEV__) {
        console.warn(`[clientCache] serving STALE for "${key}" — ${err instanceof Error ? err.message : err}`);
      }
      return hit.value as T;
    }
    throw err;
  }
}

export function invalidateCache(prefix: string) {
  const keys = Array.from(store.keys()).filter((k) => k.startsWith(prefix));
  for (const k of keys) store.delete(k);
}

export function clearCache() {
  store.clear();
}

/** Ritorna una snapshot dello stato cache (solo per debug/admin panel). */
export function cacheDebug() {
  const now = Date.now();
  return Array.from(store.entries()).map(([k, v]) => ({
    key: k,
    ageMs: now - v.storedAt,
    ttlLeftMs: Math.max(0, v.expires - now),
    stale: v.expires <= now,
  }));
}
