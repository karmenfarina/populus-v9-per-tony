import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Modal,
} from "react-native";
import { Image } from "expo-image";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, radius } from "@/src/theme";
import AnimatedStoryRing from "@/src/components/AnimatedStoryRing";
import { useStoryUpload } from "@/src/stories/StoryUploadContext";
import { getInitials } from "@/src/utils/nickname";

/**
 * Instagram-style stories strip.
 *
 * Rendered directly under the brand header on the home screen. Shows
 * horizontally-scrollable circles for every author in the user's
 * circle who currently has an active (non-expired) story, sorted by
 * the backend using this priority chain:
 *   1. My own group (always leftmost)
 *   2. Groups with unseen stories, newest first
 *   3. Fully-seen groups, newest first
 *
 * When the user taps a circle we push `/stories/viewer/{userId}`, the
 * fullscreen viewer that plays that author's stories back-to-back.
 *
 * The "add story" affordance is a small "+" badge overlayed on the
 * first (self) circle — tapping it directly opens the composer if the
 * user has no active story yet, otherwise it opens their own viewer.
 *
 * Anonymous users: the strip renders empty (no publish, no follows).
 */

type StoryAuthor = {
  user_id: string;
  nickname?: string | null;
  display_name?: string | null;
  avatar?: string | null;
};

// Local shape helper — kept lightweight in sync with the `StoryGroup`
// exported from `src/api.ts` (we intentionally do not re-import it
// here to avoid a circular type dependency while StoriesBar owns its
// fetch call).
type LocalStoryGroup = {
  user_id: string;
  author: StoryAuthor | null;
  has_unseen: boolean;
  is_mine: boolean;
  stories: { story_id: string }[];
  latest_ts: string;
};

// Bar dims kept in constants so the parent screen can compute paddings
// without duplicating magic numbers.
export const STORIES_BAR_HEIGHT = 108;
const CIRCLE_SIZE = 66;
const RING_WIDTH = 3;

// NOTE: we intentionally do NOT persist the collapsed/expanded choice
// across app launches. Product decision: every fresh app session
// starts with the strip OPEN so returning users see who published
// stories at a glance. Within the session the user can collapse it —
// that state lives only in component memory and resets on the next
// cold start.

export default function StoriesBar() {
  const router = useRouter();
  const { user } = useAuth();
  const [groups, setGroups] = useState<LocalStoryGroup[]>([]);
  const [loading, setLoading] = useState(true);
  // Set to true the moment the FIRST load resolves (regardless of
  // success). Prevents the visible flash where the "my" circle shows
  // the "Le tue storie" + "+" configuration for a split second before
  // flipping to "Tua storia" when the API returns data. Until we know
  // for sure whether the user has active stories we render a stable
  // neutral state (avatar only, no label change, no add badge).
  const [firstLoadDone, setFirstLoadDone] = useState(false);
  // Independent "loading" gate for the animated ring. Kept separate
  // from firstLoadDone so we can enforce a MIN visible duration for
  // the rotating gradient — on a fast connection the feed comes back
  // in ~150ms, way too quick for the user to perceive the loading
  // animation. We hold this true for at least 1500ms after mount so
  // the "loading → loaded" transition is actually noticeable.
  const [ringLoading, setRingLoading] = useState(true);
  // Global "am I currently publishing a story?" flag driven by the
  // composer via context. While true my ring keeps spinning even
  // after the feed itself finished loading — that's the whole point
  // of the loading state (Instagram-style).
  const { isUploading, viewedTick } = useStoryUpload();
  // Cross-platform info sheet — Alert.alert was causing subtle state
  // corruption on React Native Web (subsequent tab taps briefly showed
  // their content, then bounced back to home). A plain <Modal> is
  // deterministic on both web and native.
  const [helpOpen, setHelpOpen] = useState(false);
  const isAnon = user?.is_anonymous === true || (user as any)?.auth_provider === "anonymous";

  // Collapsed / expanded state for the whole stories strip.
  //   collapsed = only the thin pill "Nuove storie" / "Storie" is shown
  //   expanded  = the full horizontal ring strip (default)
  //
  // Product decision: every fresh app session starts EXPANDED so
  // returning users immediately see who has fresh stories. The user
  // can collapse the strip in-session, but the choice does NOT
  // persist across cold starts — we want the strip to reintroduce
  // itself every time the app opens.
  const [collapsed, setCollapsed] = useState<boolean>(false);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => !prev);
  }, []);

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!user?.user_id || isAnon) {
      setGroups([]);
      setLoading(false);
      setFirstLoadDone(true);
      setRingLoading(false);
      return;
    }
    // Reset the ring-loading gate at the start of every FIRST fetch
    // (or explicit user-initiated refresh) so the spinner animation
    // plays for its minimum visible duration. Silent refreshes
    // (e.g. after a story-view) do NOT reset it — otherwise the ring
    // would re-animate every time and never reach the "loaded" state.
    if (!opts?.silent) setRingLoading(true);
    try {
      const r: any = await api.storiesFeed();
      setGroups((r?.groups || []) as LocalStoryGroup[]);
    } catch {
      // Silent failure — the strip is a secondary UI element and we
      // don't want to blow up the whole home screen if it flakes.
      setGroups([]);
    } finally {
      setLoading(false);
      setFirstLoadDone(true);
      if (!opts?.silent) {
        setTimeout(() => setRingLoading(false), 900);
      }
    }
  }, [user?.user_id, isAnon]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  // Refetch the feed EVERY TIME a story is viewed anywhere in the app
  // (viewer marks it via context). This is what actually flips the
  // ring from "unseen" → "seen" without waiting for a focus event
  // that might never fire in nested-nav situations. Silent so the
  // loading spinner animation doesn't kick in again.
  useEffect(() => {
    if (viewedTick === 0) return; // Skip the initial value.
    load({ silent: true });
  }, [viewedTick, load]);

  // Anonymous users don't see the bar at all — no publishing, no
  // reading. Keeps the home screen cleaner for that account type.
  if (isAnon) return null;

  const openViewer = (authorId: string) => {
    if (!authorId) return;
    // Direct URL push is more reliable than the templated
    // `pathname + params` form on RN-Web — the latter has been
    // observed to occasionally resolve to a STALE viewer instance
    // (opening the wrong user's stories) when the previous viewer
    // was still mid-unmount, right after a story upload flow.
    //
    // ALSO: pass a `nav` token (unique per tap). The viewer's route
    // sync effect keys off it, so even if Expo Router REUSES the same
    // mounted viewer instance (e.g. the last time it was open it
    // auto-advanced internally to a different user via jumpToUser
    // and the URL param never changed), the fresh token forces a
    // full resync back to the user the strip actually tapped. This
    // was the root cause of "tapping any circle after publishing a
    // story opens the wrong user's stories".
    router.push(
      `/stories/viewer/${encodeURIComponent(authorId)}?nav=${Date.now()}` as any,
    );
  };

  const openComposerOrMine = () => {
    // If I have any active story, open the viewer over my own strip
    // so I can rewatch/delete. Otherwise show a small, custom help
    // sheet — stories are ALWAYS created from a specific feud via
    // the share sheet, so there's nothing meaningful to do from this
    // button beyond pointing the user to that flow.
    const myGroup = groups.find((g) => g.is_mine);
    if (myGroup && myGroup.stories.length > 0) {
      openViewer(myGroup.user_id);
    } else {
      setHelpOpen(true);
    }
  };

  const myGroup = groups.find((g) => g.is_mine) || null;
  // Show ALL friends who have stories in the last 24h — whether the
  // user has viewed them or not. Viewed rings stay in the strip but
  // switch to their `seen` (faded outline) variant. Filtering them
  // out would drop chats-with-context the user still wants to see.
  const otherGroups = groups.filter((g) => !g.is_mine);

  // How many OTHER users have at least one story I haven't watched
  // yet. Drives the "Nuove storie" highlight on the collapsed pill.
  // My own group is intentionally excluded — a story I just published
  // shouldn't shout "new" back at me.
  const unseenCount = otherGroups.filter((g) => g.has_unseen).length;
  const hasUnseen = unseenCount > 0;

  // ------------------------------------------------------------------
  // COLLAPSED VIEW — thin pill, tap to expand.
  // Only rendered when the user explicitly collapsed the strip in
  // this session (default is expanded).
  // ------------------------------------------------------------------
  if (collapsed) {
    return (
      <View style={styles.collapsedContainer} testID="stories-bar-collapsed">
        <Pressable
          onPress={toggleCollapsed}
          style={styles.collapsedCard}
          testID="stories-bar-pill"
          accessibilityRole="button"
          accessibilityLabel={
            hasUnseen
              ? `Nuove storie disponibili (${unseenCount}). Tocca per espandere.`
              : "Storie. Tocca per espandere."
          }
        >
          <View style={styles.collapsedIconWrap}>
            <Ionicons name="albums-outline" size={18} color={colors.onSurface} />
          </View>
          <Text style={styles.collapsedLabel} numberOfLines={1}>
            {hasUnseen ? "Nuove storie" : "Storie"}
          </Text>
          {hasUnseen ? (
            <View style={styles.collapsedCountBadge}>
              <Text style={styles.collapsedCountText} allowFontScaling={false}>
                {unseenCount}
              </Text>
            </View>
          ) : null}
          <View style={{ flex: 1 }} />
          <Ionicons name="chevron-down" size={20} color={colors.muted} />
        </Pressable>
      </View>
    );
  }

  // ------------------------------------------------------------------
  // EXPANDED VIEW — original horizontal ring strip.
  // Adds a small "collapse" header on top so the user can hide the
  // strip again without going into settings.
  // ------------------------------------------------------------------
  return (
    <View style={styles.container} testID="stories-bar">
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.scrollBody}
      >
        {/* My-circle slot — always leftmost. Doubles as the "add story"
            entry point when I don't yet have an active story. */}
        <Pressable
          onPress={openComposerOrMine}
          style={styles.item}
          testID="stories-bar-mine"
        >
          <AnimatedStoryRing
            size={CIRCLE_SIZE + RING_WIDTH * 2}
            ringWidth={RING_WIDTH}
            variant={firstLoadDone && myGroup?.has_unseen ? "unseen" : "mine"}
            // Two conditions animate MY ring:
            //   1. Very first fetch of the feed on app open — brief
            //      but honest signal that the strip is still loading.
            //   2. A publish-story request is in flight (user just
            //      hit Pubblica in the composer). The ring keeps
            //      spinning until the server confirms — matches
            //      Instagram's "story being uploaded" state.
            loading={ringLoading || isUploading}
          >
            <View style={styles.avatarWrap}>
              {(() => {
                // Prefer the group's `author.avatar` from /stories/feed
                // once available. On cold start `myGroup` is null until
                // the feed lands — use `user.primary_photo` (hydrated
                // by /auth/me) as the immediate fallback so we don't
                // flash the initials placeholder before the real photo
                // paints. `user.photos[0]?.data` remains a legacy
                // safety net if `primary_photo` isn't populated.
                const primary = user?.primary_photo?.data || null;
                const legacy = (user as any)?.photos && (user as any).photos[0]?.data
                  ? (user as any).photos[0].data
                  : null;
                const raw = myGroup?.author?.avatar ?? primary ?? legacy;
                if (!raw) {
                  // No profile picture and no active story avatar.
                  // A plain empty gray circle read as a "broken image
                  // placeholder" to users. Render initials from the
                  // user's display name / nickname instead — same
                  // pattern used by Instagram, WhatsApp and Gmail
                  // when there's no avatar. Never crashes:
                  // `getInitials` degrades to "?" for unknown labels.
                  const initials = getInitials(
                    (user as any)?.display_name || user?.nickname || "?"
                  );
                  return (
                    <View style={[styles.avatar, styles.avatarFallback]}>
                      <Text style={styles.avatarInitials} allowFontScaling={false}>
                        {initials}
                      </Text>
                    </View>
                  );
                }
                const uri = raw.startsWith("data:") ? raw : `data:image/jpeg;base64,${raw}`;
                return <Image source={{ uri }} style={styles.avatar} cachePolicy="memory-disk" contentFit="cover" />;
              })()}
              {/* Removed the red "+" badge overlay — user feedback:
                  it read as visual noise, especially over the initials
                  fallback. The affordance is now carried by the label
                  underneath ("Aggiungi storia" when the user has none,
                  "Tua storia" when they do), matching the modern
                  Instagram pattern. */}
            </View>
          </AnimatedStoryRing>
          <Text style={styles.label} numberOfLines={1}>
            {/* Hold a neutral fallback until first load so the label
                doesn't visibly flip from "Le tue storie" → "Tua storia". */}
            {!firstLoadDone
              ? "Le tue storie"
              : myGroup && myGroup.stories.length > 0
              ? "Tua storia"
              : "Aggiungi storia"}
          </Text>
        </Pressable>

        {loading && otherGroups.length === 0 ? (
          <View style={[styles.item, { justifyContent: "center" }]}>
            <ActivityIndicator color={colors.brandPrimary} />
          </View>
        ) : null}

        {otherGroups.map((g) => (
          <Pressable
            key={g.user_id}
            onPress={() => openViewer(g.user_id)}
            style={styles.item}
            testID={`stories-bar-${g.user_id}`}
          >
            <AnimatedStoryRing
              size={CIRCLE_SIZE + RING_WIDTH * 2}
              ringWidth={RING_WIDTH}
              variant={g.has_unseen ? "unseen" : "seen"}
              // Friends' stories arrive from the server already
              // uploaded, so they never need the "loading" spinner —
              // only the initial-fetch phase animates them.
              loading={ringLoading}
            >
              <View style={styles.avatarWrap}>
                {g.author?.avatar ? (
                  <Image source={{ uri: g.author.avatar }} style={styles.avatar} cachePolicy="memory-disk" contentFit="cover" recyclingKey={g.user_id} />
                ) : (
                  // Initials fallback — same pattern as the "my ring"
                  // case above. Empty gray circles read as broken
                  // image placeholders.
                  <View style={[styles.avatar, styles.avatarFallback]}>
                    <Text style={styles.avatarInitials} allowFontScaling={false}>
                      {getInitials(
                        g.author?.display_name || g.author?.nickname || "?"
                      )}
                    </Text>
                  </View>
                )}
              </View>
            </AnimatedStoryRing>
            <Text style={styles.label} numberOfLines={1}>
              {g.author?.nickname || g.author?.display_name || "utente"}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
      {/* Chevron overlay — floats over the top-right corner so the
          collapse tap-target adds ZERO vertical height to the strip.
          The container already has enough top padding to avoid clipping
          against the story circles. */}
      <Pressable
        onPress={toggleCollapsed}
        style={styles.collapseChip}
        testID="stories-bar-collapse-btn"
        accessibilityRole="button"
        accessibilityLabel="Nascondi storie"
        hitSlop={10}
      >
        <Ionicons name="chevron-up" size={14} color={colors.muted} />
      </Pressable>

      {/* Explainer sheet for the "no stories yet" case. Uses a normal
          Modal instead of Alert.alert to avoid the RN-Web edge case
          where subsequent tab taps flicker back to home. */}
      <Modal
        visible={helpOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setHelpOpen(false)}
      >
        <Pressable style={styles.helpBackdrop} onPress={() => setHelpOpen(false)}>
          <Pressable style={styles.helpSheet} onPress={() => { /* consume */ }}>
            <Text style={styles.helpTitle}>Come pubblicare una storia</Text>
            <Text style={styles.helpBody}>
              Apri una faida che ti interessa, tocca il pulsante Condividi e scegli &quot;Aggiungi alla tua storia&quot;.
            </Text>
            <Pressable
              onPress={() => setHelpOpen(false)}
              style={styles.helpOkBtn}
              testID="stories-help-ok"
            >
              <Text style={styles.helpOkTxt}>HO CAPITO</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    // Expanded strip. Height derived purely from the circle + label +
    // minimal paddings. The chevron collapse button is absolute-positioned
    // so it does NOT add any vertical space.
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingTop: 0,
    position: "relative",
  },
  collapseChip: {
    position: "absolute",
    top: 2,
    right: 6,
    width: 20,
    height: 20,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "transparent",
  },
  // -------- Collapsed card --------
  // Prominent full-width tap target — reads as a "collapsible section
  // header" rather than a discrete pill. Matches the phase 1b mockup:
  // subtle elevated card, small albums icon on the left, "Storie"
  // label, chevron-down anchored right.
  collapsedContainer: {
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  collapsedCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    minHeight: 48,
  },
  collapsedIconWrap: {
    width: 30,
    height: 30,
    borderRadius: 8,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  collapsedLabel: {
    color: colors.onSurface,
    fontSize: 14,
    fontWeight: "600",
    letterSpacing: 0.2,
  },
  collapsedCountBadge: {
    marginLeft: 2,
    minWidth: 20,
    height: 20,
    paddingHorizontal: 6,
    borderRadius: 10,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  collapsedCountText: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "800",
    lineHeight: 13,
  },
  scrollBody: {
    // Padding asimmetrico: paddingLeft standard (spacing.md), ma a
    // destra riserviamo spazio in piu' per NON far mai finire l'ultimo
    // cerchio sotto la freccetta di collasso (`collapseChip`), che e'
    // absolute-positioned a top:2/right:6 con hitSlop=10. Senza questo
    // padding, scrollando all'estrema destra l'ultima storia rimane
    // graficamente sovrapposta al chevron. La freccetta NON viene
    // spostata — la posizione (top:2, right:6) rimane invariata, solo
    // lo scroll si ferma piu' a sinistra.
    paddingLeft: spacing.md,
    paddingRight: 36,
    alignItems: "center",
    gap: spacing.md,
    paddingBottom: 2,
    paddingTop: 4,
  },
  item: {
    width: CIRCLE_SIZE + 12,
    alignItems: "center",
  },
  // Ring is the outer accent that signals unseen/seen state. Padding
  // creates the visual gap between ring and avatar that Instagram
  // popularized.
  ring: {
    width: CIRCLE_SIZE + RING_WIDTH * 2,
    height: CIRCLE_SIZE + RING_WIDTH * 2,
    borderRadius: (CIRCLE_SIZE + RING_WIDTH * 2) / 2,
    padding: RING_WIDTH,
    alignItems: "center",
    justifyContent: "center",
  },
  ringUnseen: {
    // Solid brand red for maximum visibility. Gradients require an
    // extra dep on RN — sticking to solid keeps bundle size and
    // rendering predictable across web+native.
    backgroundColor: colors.brandPrimary,
  },
  ringSeen: {
    backgroundColor: colors.muted + "55",
    opacity: 0.7,
  },
  ringMine: {
    backgroundColor: colors.border,
  },
  avatarWrap: {
    width: CIRCLE_SIZE,
    height: CIRCLE_SIZE,
    borderRadius: CIRCLE_SIZE / 2,
    backgroundColor: colors.surface,
    overflow: "visible",
    padding: 2,
  },
  avatar: {
    width: CIRCLE_SIZE - 4,
    height: CIRCLE_SIZE - 4,
    borderRadius: (CIRCLE_SIZE - 4) / 2,
    backgroundColor: colors.surfaceTertiary,
  },
  avatarFallback: {
    alignItems: "center",
    justifyContent: "center",
    // Slightly warmer tone than `surfaceTertiary` so the initials
    // pop and the circle reads as an intentional avatar placeholder,
    // not a broken image.
    backgroundColor: colors.surfaceSecondary || colors.surfaceTertiary,
  },
  avatarInitials: {
    color: colors.onSurface,
    fontSize: 22,
    fontWeight: "700",
    letterSpacing: 0.5,
    // Keep the letters visually centered even when the descender is
    // taller than the ascender (works around the RN-Web baseline
    // being higher than native).
    lineHeight: 24,
    textAlign: "center",
    includeFontPadding: false,
  },
  label: {
    marginTop: 4,
    fontSize: 11,
    color: colors.onSurface,
    maxWidth: CIRCLE_SIZE + 12,
    textAlign: "center",
  },
  helpBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
  },
  helpSheet: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: spacing.lg,
    maxWidth: 360,
    width: "100%",
  },
  helpTitle: {
    color: colors.onSurface,
    fontSize: 16,
    fontWeight: "700",
    marginBottom: spacing.sm,
  },
  helpBody: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: spacing.md,
  },
  helpOkBtn: {
    alignSelf: "flex-end",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.brandPrimary,
    borderRadius: 6,
  },
  helpOkTxt: {
    color: colors.onBrandPrimary,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
});
