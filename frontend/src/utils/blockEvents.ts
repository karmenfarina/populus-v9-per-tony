/**
 * Lightweight in-app broadcaster for block/unblock events.
 *
 * When a user blocks or unblocks somebody, EVERY screen currently
 * showing comments/replies/notifications/anything that depends on
 * the block list must refetch its data. React Navigation's
 * `useFocusEffect` handles this when the focus changes, but it does
 * NOT fire when the user stays on the same screen (e.g. blocks from
 * an in-page modal). This broadcaster gives us a screen-agnostic
 * refresh signal.
 *
 * Usage:
 *   // producer
 *   import { blockEvents } from "@/src/utils/blockEvents";
 *   await api.blockUser(uid);
 *   blockEvents.emit();
 *
 *   // consumer (inside a screen)
 *   useEffect(() => blockEvents.subscribe(() => refetch()), []);
 */
type Listener = () => void;

const listeners = new Set<Listener>();

export const blockEvents = {
  subscribe(fn: Listener): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  emit(): void {
    listeners.forEach((fn) => {
      try { fn(); } catch { /* swallow */ }
    });
  },
};
