/**
 * In-app store review request — Google Play + Apple App Store native prompt.
 *
 * Strategy (Apple + Google guidelines compliant):
 *  - NEVER show on first launch or immediately after install.
 *  - Require the user to have accumulated MULTIPLE "positive moments":
 *      • at least N (default 3) distinct app sessions,
 *      • at least M (default 5) meaningful actions (vote, comment, reply),
 *      • ≥ D days since first install (default 3),
 *      • ≥ D2 days since the LAST prompt was shown (default 120).
 *  - Apple limits the native `SKStoreReviewController` to 3 prompts per year
 *    per user; we respect that by remembering the last-shown timestamp AND
 *    by requiring meaningful new activity between prompts.
 *  - Web / Expo Go: no-op. Only real iOS/Android builds show the popup.
 *  - Prompt is fired via `expo-store-review.requestReview()` which delegates
 *    to the platform-native UI — we never draw our own "rate us" alert.
 *  - Failure to display (rate limited by OS, not installed via a store,
 *    etc.) is silently swallowed — no user-visible error, no retry storm.
 *
 * Public API:
 *   • `reviewManager.recordAction(kind)`  — call after any meaningful UX event.
 *   • `reviewManager.maybePrompt()`       — call at a "positive moment" — will
 *      only actually prompt when every gate above is satisfied.
 */
import { Platform } from "react-native";
import * as StoreReview from "expo-store-review";
import { storage } from "@/src/utils/storage";

type ActionKind = "vote" | "comment" | "reply" | "share";

const KEY_INSTALL_AT = "review_install_at";
const KEY_LAST_PROMPT = "review_last_prompt";
const KEY_ACTION_COUNT = "review_action_count";
const KEY_SESSION_COUNT = "review_session_count";
const KEY_SESSION_MARKED = "review_session_marked_at";

// Tunables — conservative defaults that match community best practices.
const MIN_SESSIONS = 3;
const MIN_ACTIONS = 5;
const MIN_DAYS_SINCE_INSTALL = 3;
const MIN_DAYS_BETWEEN_PROMPTS = 120;
const SESSION_DEBOUNCE_MS = 30 * 60 * 1000; // 30 min

async function readNum(key: string): Promise<number> {
  const v = await storage.getItem<number>(key, 0);
  return typeof v === "number" && !isNaN(v) ? v : 0;
}

async function writeNum(key: string, v: number): Promise<void> {
  try { await storage.setItem(key, v); } catch { /* noop */ }
}

async function ensureInstallStamp(): Promise<number> {
  const cur = await readNum(KEY_INSTALL_AT);
  if (cur > 0) return cur;
  const now = Date.now();
  await writeNum(KEY_INSTALL_AT, now);
  return now;
}

/** Debounced session-open counter — increments at most once per 30 minutes. */
async function markSessionOpen(): Promise<void> {
  const last = await readNum(KEY_SESSION_MARKED);
  const now = Date.now();
  if (last > 0 && now - last < SESSION_DEBOUNCE_MS) return;
  await writeNum(KEY_SESSION_MARKED, now);
  const count = await readNum(KEY_SESSION_COUNT);
  await writeNum(KEY_SESSION_COUNT, count + 1);
}

async function recordAction(_kind: ActionKind): Promise<void> {
  try {
    const c = await readNum(KEY_ACTION_COUNT);
    await writeNum(KEY_ACTION_COUNT, c + 1);
  } catch { /* noop */ }
}

async function maybePrompt(): Promise<void> {
  // Web and Expo Go don't have the native store popup — no-op.
  if (Platform.OS === "web") return;
  try {
    const [hasAction, isAvailable] = await Promise.all([
      StoreReview.hasAction(),
      StoreReview.isAvailableAsync(),
    ]);
    if (!hasAction || !isAvailable) return;
  } catch {
    return;
  }
  const installAt = await ensureInstallStamp();
  const now = Date.now();
  const daysSinceInstall = (now - installAt) / (24 * 60 * 60 * 1000);
  if (daysSinceInstall < MIN_DAYS_SINCE_INSTALL) return;

  const [sessions, actions, lastPrompt] = await Promise.all([
    readNum(KEY_SESSION_COUNT),
    readNum(KEY_ACTION_COUNT),
    readNum(KEY_LAST_PROMPT),
  ]);
  if (sessions < MIN_SESSIONS) return;
  if (actions < MIN_ACTIONS) return;
  if (lastPrompt > 0) {
    const daysSince = (now - lastPrompt) / (24 * 60 * 60 * 1000);
    if (daysSince < MIN_DAYS_BETWEEN_PROMPTS) return;
  }
  // All gates passed — fire the native prompt. Any platform-level rate
  // limit failure is swallowed inside requestReview itself.
  try {
    await StoreReview.requestReview();
    await writeNum(KEY_LAST_PROMPT, now);
    // Reset action counter so a repeated prompt requires FRESH activity —
    // Apple/Google both frown upon repeat prompts triggered by the same
    // engagement.
    await writeNum(KEY_ACTION_COUNT, 0);
  } catch { /* noop */ }
}

export const reviewManager = {
  markSessionOpen,
  recordAction,
  maybePrompt,
};
