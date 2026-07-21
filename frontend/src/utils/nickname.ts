// Nickname handling — Instagram-style handles.
// Allowed: letters (a-z, A-Z), digits (0-9), underscore, period. NO spaces.
// Kept in sync with the backend regex in `_normalize_and_validate_nickname`.

export const NICKNAME_MAX = 24;
export const NICKNAME_MIN = 2;
export const NICKNAME_HINT =
  "Solo lettere, numeri, punti e underscore (nessuno spazio).";

/**
 * Strip anything a user types that isn't an allowed nickname character.
 * Also removes any leading '@' the user may add out of habit.
 * Truncates to NICKNAME_MAX to match the backend length limit.
 */
export function sanitizeNicknameInput(raw: string): string {
  return raw
    .replace(/^@+/, "")
    .replace(/[^A-Za-z0-9._]/g, "")
    .slice(0, NICKNAME_MAX);
}

/**
 * Full validation — returns an error string (Italian, ready to display) or
 * null if the nickname is valid. Length is checked after sanitizing so the
 * caller can rely on this to be the single source of truth.
 */
export function validateNickname(raw: string): string | null {
  const n = sanitizeNicknameInput(raw).trim();
  if (n.length < NICKNAME_MIN) return `Il nickname deve avere almeno ${NICKNAME_MIN} caratteri`;
  if (n.length > NICKNAME_MAX) return `Il nickname deve avere al massimo ${NICKNAME_MAX} caratteri`;
  if (!/^[A-Za-z0-9._]+$/.test(n)) return NICKNAME_HINT;
  return null;
}
