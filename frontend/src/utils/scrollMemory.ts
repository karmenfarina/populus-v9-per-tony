/**
 * Cross-mount scroll memory for tab/detail screens.
 *
 * Expo Router's Tabs navigator does NOT reliably keep sibling routes
 * mounted when you `router.push` between them (especially when the
 * target is a hidden `href: null` route). That means component-local
 * `useRef` values are lost across the round-trip
 *
 *   Profile → history item tap → /feud/X → hardware back → Profile
 *
 * If Profile re-mounts on the way back, its `scrollYRef` and the
 * "should I restore?" flag reset to their defaults and the user
 * silently lands at the top of the page.
 *
 * This module keeps the state at the module scope (survives any
 * component mount/unmount) and is keyed by an arbitrary string so
 * multiple screens (own profile, public user profile) can share the
 * same mechanism without clobbering each other.
 */

type Entry = {
  y: number;
  /** Set to true right before navigating to a detail child screen so
   *  the parent's next focus event knows to restore. Consumed on
   *  restore (or on an explicit tab-bar re-tap that should reset). */
  restore: boolean;
};

const store: Record<string, Entry> = {};

function slot(key: string): Entry {
  if (!store[key]) store[key] = { y: 0, restore: false };
  return store[key];
}

export const scrollMemory = {
  /** Record the current scroll offset — called from onScroll. */
  setY(key: string, y: number): void {
    slot(key).y = y;
  },
  /** Read the last known offset. */
  getY(key: string): number {
    return slot(key).y;
  },
  /** Arm the "restore on next focus" flag — call this in the tap
   *  handler right before router.push to a detail screen. */
  markRestore(key: string): void {
    slot(key).restore = true;
  },
  /** Read and clear the restore flag — call once per focus event. */
  consumeRestore(key: string): boolean {
    const s = slot(key);
    const v = s.restore;
    s.restore = false;
    return v;
  },
  /** Reset both offset and restore flag (used on logout / auth swap). */
  reset(key?: string): void {
    if (key) { delete store[key]; return; }
    for (const k of Object.keys(store)) delete store[k];
  },
};
