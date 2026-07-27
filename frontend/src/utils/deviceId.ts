// Device-scoped identifier used by anonymous-auth to keep the same
// anonymous user across app restarts on the same device.
//
// Strategy (in priority order):
//   1) Native (Android/iOS): read a persisted UUID from expo-secure-store.
//      Keychain / EncryptedSharedPreferences survives app updates.
//   2) Web: read a persisted UUID from AsyncStorage (localStorage-backed).
//   3) If none exists → mint a new one and persist it.
//
// The value is a random UUID (v4-ish, cryptographically strong on native
// and web where crypto.getRandomValues is available; falls back to
// Math.random otherwise). Not tied to any hardware ID → GDPR-neutral and
// works uniformly across the three platforms.
//
// Limitations (acceptable per product):
//   - Uninstalling the app on iOS clears the keychain entry → new ID.
//   - Clearing browser storage on the web resets the ID.
//   - Rooted devices / jailbroken devices can forge the ID.
// It's a strong deterrent against casual vote-stuffing, not a hard wall.
import { Platform } from "react-native";
import { storage } from "@/src/utils/storage";

const KEY = "populus.deviceId.v1";

let _cached: string | null = null;
let _pending: Promise<string> | null = null;

function _randomUUID(): string {
  // Prefer the standard runtime UUID generator when available.
  try {
    // @ts-ignore — crypto.randomUUID exists on modern web + Hermes ≥0.71.
    if (typeof globalThis !== "undefined" && (globalThis as any).crypto?.randomUUID) {
      // @ts-ignore
      return (globalThis as any).crypto.randomUUID();
    }
  } catch { /* fall through */ }
  // Fallback: RFC-4122 v4 built from getRandomValues() or Math.random().
  const bytes = new Uint8Array(16);
  try {
    // @ts-ignore
    if ((globalThis as any).crypto?.getRandomValues) {
      // @ts-ignore
      (globalThis as any).crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
  } catch {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  // Set version + variant.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return (
    hex.slice(0, 8) + "-" +
    hex.slice(8, 12) + "-" +
    hex.slice(12, 16) + "-" +
    hex.slice(16, 20) + "-" +
    hex.slice(20)
  );
}

async function _load(): Promise<string | null> {
  if (Platform.OS === "web") {
    const v = await storage.getItem<string>(KEY, "");
    return typeof v === "string" && v.length > 0 ? v : null;
  }
  const v = await storage.secureGet<string>(KEY, "");
  return typeof v === "string" && v.length > 0 ? v : null;
}

async function _save(value: string): Promise<void> {
  if (Platform.OS === "web") {
    await storage.setItem(KEY, value);
  } else {
    await storage.secureSet(KEY, value);
  }
}

/**
 * Returns the persistent, device-scoped identifier for this install.
 * Idempotent: subsequent calls yield the same string.
 */
export async function getDeviceId(): Promise<string> {
  if (_cached) return _cached;
  if (_pending) return _pending;
  _pending = (async () => {
    try {
      const existing = await _load();
      if (existing) {
        _cached = existing;
        return existing;
      }
      const fresh = _randomUUID();
      try { await _save(fresh); } catch { /* best-effort */ }
      _cached = fresh;
      return fresh;
    } finally {
      _pending = null;
    }
  })();
  return _pending;
}
