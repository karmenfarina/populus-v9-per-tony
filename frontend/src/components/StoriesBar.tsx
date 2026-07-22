import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  Image,
  StyleSheet,
  ActivityIndicator,
  Modal,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing } from "@/src/theme";
import AnimatedStoryRing from "@/src/components/AnimatedStoryRing";

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

type StoryGroup = {
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

export default function StoriesBar() {
  const router = useRouter();
  const { user } = useAuth();
  const [groups, setGroups] = useState<StoryGroup[]>([]);
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
  // Cross-platform info sheet — Alert.alert was causing subtle state
  // corruption on React Native Web (subsequent tab taps briefly showed
  // their content, then bounced back to home). A plain <Modal> is
  // deterministic on both web and native.
  const [helpOpen, setHelpOpen] = useState(false);
  const isAnon = user?.is_anonymous === true || (user as any)?.auth_provider === "anonymous";

  const load = useCallback(async () => {
    if (!user?.user_id || isAnon) {
      setGroups([]);
      setLoading(false);
      setFirstLoadDone(true);
      setRingLoading(false);
      return;
    }
    // Reset the ring-loading gate at the start of every fetch so a
    // manual refresh (or focus-triggered re-fetch) also plays the
    // spinner animation for its minimum visible duration.
    setRingLoading(true);
    try {
      const r: any = await api.storiesFeed();
      setGroups((r?.groups || []) as StoryGroup[]);
    } catch {
      // Silent failure — the strip is a secondary UI element and we
      // don't want to blow up the whole home screen if it flakes.
      setGroups([]);
    } finally {
      setLoading(false);
      setFirstLoadDone(true);
      // Hold the loading ring animation for a minimum visible time
      // AFTER the fetch resolves — this is what makes the "loading
      // then loaded" transition perceivable on fast connections.
      setTimeout(() => setRingLoading(false), 900);
    }
  }, [user?.user_id, isAnon]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  // Anonymous users don't see the bar at all — no publishing, no
  // reading. Keeps the home screen cleaner for that account type.
  if (isAnon) return null;

  const openViewer = (authorId: string) => {
    router.push({ pathname: "/stories/viewer/[userId]", params: { userId: authorId } } as any);
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
  const otherGroups = groups.filter((g) => !g.is_mine);

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
            // Spin while the feed is still being fetched, freeze it
            // afterwards. Gives the "story loading → story loaded"
            // visual cue the user asked for (Instagram behaviour).
            loading={ringLoading}
          >
            <View style={styles.avatarWrap}>
              {(() => {
                // Prefer the group's `author.avatar` from /stories/feed
                // (already normalised to a full data URL on the server)
                // so my own ring uses the SAME photo the other users
                // see — the "primary" one, not just position 0. Fall
                // back to `user.photos[0]?.data` when the feed hasn't
                // arrived yet, prefixing the raw base64 with the data
                // URL scheme so <Image source> can actually render it.
                const raw = myGroup?.author?.avatar
                  ?? (user?.photos && user.photos[0]?.data ? user.photos[0].data : null);
                if (!raw) {
                  return (
                    <View style={[styles.avatar, styles.avatarFallback]}>
                      <Ionicons name="person" size={26} color={colors.muted} />
                    </View>
                  );
                }
                const uri = raw.startsWith("data:") ? raw : `data:image/jpeg;base64,${raw}`;
                return <Image source={{ uri }} style={styles.avatar} />;
              })()}
              {/* Only reveal the "+" affordance AFTER first load resolved
                  and we know for sure the user has no active story yet.
                  Otherwise it flashes on then off during initial render. */}
              {firstLoadDone && (!myGroup || myGroup.stories.length === 0) && (
                <View style={styles.plusBadge}>
                  <Ionicons name="add" size={14} color="#fff" />
                </View>
              )}
            </View>
          </AnimatedStoryRing>
          <Text style={styles.label} numberOfLines={1}>
            {/* Hold a neutral fallback until first load so the label
                doesn't visibly flip from "Le tue storie" → "Tua storia". */}
            {!firstLoadDone
              ? "Le tue storie"
              : myGroup && myGroup.stories.length > 0
              ? "Tua storia"
              : "Le tue storie"}
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
              // Only spin while the feed itself is loading. When the
              // strip has settled the ring becomes a static gradient
              // so the user can tell "loading" from "ready to tap".
              loading={ringLoading}
            >
              <View style={styles.avatarWrap}>
                {g.author?.avatar ? (
                  <Image source={{ uri: g.author.avatar }} style={styles.avatar} />
                ) : (
                  <View style={[styles.avatar, styles.avatarFallback]}>
                    <Ionicons name="person" size={26} color={colors.muted} />
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
    height: STORIES_BAR_HEIGHT,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    justifyContent: "center",
  },
  scrollBody: {
    paddingHorizontal: spacing.md,
    alignItems: "center",
    gap: spacing.md,
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
  },
  plusBadge: {
    position: "absolute",
    right: 0,
    bottom: 0,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 2,
    borderColor: colors.surface,
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
