/**
 * Cross-platform navigation stack.
 *
 * Expo Router's built-in `router.back()` is unreliable inside a Tabs
 * layout on web — it collapses back to the root instead of walking
 * the pushed history one screen at a time. This module keeps a
 * manual stack of visited pathnames so `useSmartBack` can pop the
 * exact previous screen regardless of platform.
 *
 * The stack is a module-level singleton (state is preserved across
 * screen mounts). Each detail screen using `useSmartBack` pushes
 * its own `pathname` on focus and pops it on back navigation.
 *
 * Consecutive duplicates are automatically deduplicated so a
 * `router.replace(x)` triggered by our own back handler doesn't
 * grow the stack again.
 */

const MAX_DEPTH = 50;
let stack: string[] = [];

export const navStack = {
  peek(): string | undefined {
    return stack[stack.length - 1];
  },
  /** Push a pathname unless it's the same as the current top. */
  push(path: string): void {
    if (!path) return;
    if (stack[stack.length - 1] === path) return;
    stack.push(path);
    if (stack.length > MAX_DEPTH) stack.shift();
  },
  /** Pop the top entry and return the NEW top (i.e. the previous screen). */
  popAndPeek(): string | undefined {
    stack.pop();
    return stack[stack.length - 1];
  },
  /** Full reset — call on logout / auth change so a fresh session starts clean. */
  clear(): void {
    stack = [];
  },
  size(): number {
    return stack.length;
  },
};
