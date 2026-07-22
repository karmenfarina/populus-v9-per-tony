// Nickname handling — Instagram-style handles.
// Allowed: LOWERCASE letters (a-z), digits (0-9), underscore, period.
// NO spaces, NO uppercase (Instagram behaviour).
// Kept in sync with the backend regex in `_normalize_and_validate_nickname`.

export const NICKNAME_MAX = 24;
export const NICKNAME_MIN = 2;
export const NICKNAME_HINT =
  "Solo lettere minuscole, numeri, punti e underscore (nessuno spazio).";

/**
 * Compact avatar-fallback initials extracted from whatever human-facing
 * label we have for a user (display_name, nickname, or a generic "utente"
 * placeholder). Always returns 1–2 uppercase alphanumeric characters.
 *
 * Rules:
 *   - "Mario Rossi"  -> "MR"
 *   - "mario_rossi"  -> "MR"  (splits on separators, not just spaces)
 *   - "mario"        -> "M"
 *   - "gli.ispettori"-> "GI"
 *   - unknown/empty  -> "?"
 *
 * Used by StoriesBar, avatar chips, and anywhere else we need a stable
 * placeholder when the user has no profile picture.
 */
export function getInitials(name?: string | null): string {
  const raw = (name || "").trim();
  if (!raw) return "?";
  // Split on any run of non-alphanumeric characters — covers spaces,
  // dots, underscores, dashes.
  const parts = raw
    .split(/[^\p{L}\p{N}]+/u)
    .filter((p) => p.length > 0);
  if (parts.length === 0) return "?";
  if (parts.length === 1) {
    return parts[0].charAt(0).toLocaleUpperCase();
  }
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toLocaleUpperCase();
}

/**
 * Strip anything a user types that isn't an allowed nickname character.
 * Auto-lowercases everything, removes leading '@' if present, and truncates
 * to NICKNAME_MAX to match the backend.
 */
export function sanitizeNicknameInput(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/^@+/, "")
    .replace(/[^a-z0-9._]/g, "")
    .slice(0, NICKNAME_MAX);
}

/**
 * Full validation — returns a specific Italian error string ready to display
 * to the user, or `null` if valid. Errors are worded so the user immediately
 * understands *why* the save was blocked.
 *
 * The function inspects the ORIGINAL raw input so it can report accurately
 * (e.g. "contiene lettere maiuscole" only if the user actually typed some).
 */
export function validateNickname(raw: string): string | null {
  const cleaned = sanitizeNicknameInput(raw).trim();

  if (cleaned.length === 0) {
    return "Inserisci un nickname.";
  }
  if (cleaned.length < NICKNAME_MIN) {
    return `Il nickname è troppo corto (minimo ${NICKNAME_MIN} caratteri).`;
  }
  if (cleaned.length > NICKNAME_MAX) {
    return `Il nickname è troppo lungo (massimo ${NICKNAME_MAX} caratteri).`;
  }

  // Detailed feedback on illegal characters in the original input, so the
  // user knows exactly what to change.
  const rawTrimmed = raw.trim().replace(/^@+/, "");
  if (/\s/.test(rawTrimmed)) {
    return "Il nickname non può contenere spazi.";
  }
  if (/[A-Z]/.test(rawTrimmed)) {
    return "Il nickname non può contenere lettere maiuscole.";
  }
  if (!/^[a-z0-9._]+$/.test(rawTrimmed)) {
    return "Il nickname può contenere solo lettere minuscole, numeri, punti e underscore.";
  }
  return null;
}
