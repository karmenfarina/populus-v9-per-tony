// @mention helpers — parsing and rendering of Instagram-style @handles
// inside comments and replies. Kept UI-agnostic (no React imports) so it
// can be unit-tested and reused by non-render code paths.

// Same character class the backend accepts: 2..24 chars of
// [A-Za-z0-9._], case-insensitive. Anchored on the left with a
// look-behind so email-like strings (e.g. `foo@bar.com`) don't
// produce false positives — a mention must start at the beginning
// of the input, after whitespace, or after a non-word/non-dot char.
// Captured group is lowercased downstream for consistent lookup.
export const MENTION_REGEX = /(?:^|(?<=\s)|(?<=[^\w.]))@([A-Za-z0-9._]{2,24})/g;

/**
 * Break a text into typed segments so the renderer can style @mentions
 * differently from regular text. Preserves order and includes empty
 * segments the way `text.split` would.
 *
 *   Input:  "Hey @carlo look at this"
 *   Output: [
 *     { type: 'text',    value: 'Hey ' },
 *     { type: 'mention', value: '@carlo', nickname: 'carlo' },
 *     { type: 'text',    value: ' look at this' },
 *   ]
 */
export type Segment =
  | { type: 'text'; value: string }
  | { type: 'mention'; value: string; nickname: string };

export function splitMentions(text: string): Segment[] {
  if (!text) return [{ type: 'text', value: '' }];
  const out: Segment[] = [];
  let last = 0;
  // The regex has the `g` flag — reset lastIndex to make repeated
  // calls with the same regex instance safe.
  MENTION_REGEX.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = MENTION_REGEX.exec(text)) !== null) {
    const start = m.index;
    if (start > last) out.push({ type: 'text', value: text.slice(last, start) });
    const raw = m[0]; // full match e.g. `@carlo`
    const nick = (m[1] || '').toLowerCase();
    out.push({ type: 'mention', value: raw, nickname: nick });
    last = start + raw.length;
  }
  if (last < text.length) out.push({ type: 'text', value: text.slice(last) });
  if (out.length === 0) out.push({ type: 'text', value: text });
  return out;
}

/**
 * Look at what the user is currently typing and detect if the caret is
 * inside a `@partial` token that should trigger the autocomplete
 * dropdown. Returns the partial nickname (without the `@`) and its
 * substring range so the caller can replace it when a suggestion is
 * picked. Returns null when the caret is not on a live mention.
 *
 * Rules:
 *  - The `@` must be at position 0 OR preceded by a whitespace / newline
 *    (never inside an email or URL fragment).
 *  - After the `@`, only lowercase letters, digits, dots and underscores
 *    are allowed. As soon as a disallowed char (or a space) appears the
 *    mention window closes.
 */
export function detectMentionQuery(
  text: string,
  caret: number,
): { query: string; start: number; end: number } | null {
  const cursor = Math.max(0, Math.min(caret, text.length));
  // Walk backwards from the caret looking for the closest `@` on the
  // current word. Bail as soon as we hit whitespace or an invalid char.
  let i = cursor - 1;
  while (i >= 0) {
    const ch = text[i];
    if (ch === '@') {
      // Ensure the `@` is at start-of-string or preceded by whitespace
      // (or any non-word char that isn't `.` — matches the parser above).
      const prev = i > 0 ? text[i - 1] : '';
      if (i === 0 || /\s/.test(prev) || /[^\w.]/.test(prev)) {
        const query = text.slice(i + 1, cursor).toLowerCase();
        // Ignore malformed / too-long queries so the dropdown doesn't
        // fire for weird cases like `@` alone or `@toolongtobeauser…`.
        if (!/^[a-z0-9._]{0,24}$/.test(query)) return null;
        return { query, start: i, end: cursor };
      }
      return null;
    }
    if (/\s/.test(ch)) return null;
    if (!/[a-zA-Z0-9._@]/.test(ch)) return null;
    i -= 1;
  }
  return null;
}
