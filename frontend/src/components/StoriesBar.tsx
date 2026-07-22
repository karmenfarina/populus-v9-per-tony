import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  Pressable,
  ScrollView,
  Image,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing } from "@/src/theme";

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
  const isAnon = user?.is_anonymous === true || (user as any)?.auth_provider === "anonymous";

  const load = useCallback(async () => {
    if (!user?.user_id || isAnon) {
      setGroups([]);
      setLoading(false);
      return;
    }
    try {
      const r: any = await api.storiesFeed();
      setGroups((r?.groups || []) as StoryGroup[]);
    } catch {
      // Silent failure — the strip is a secondary UI element and we
      // don't want to blow up the whole home screen if it flakes.
      setGroups([]);
    } finally {
      setLoading(false);
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
    // so I can rewatch/delete. Otherwise show a short explainer alert
    // — stories are ALWAYS created from a specific feud via the share
    // sheet, so there's nothing meaningful to do from this button
    // beyond pointing the user to that flow.
    const myGroup = groups.find((g) => g.is_mine);
    if (myGroup && myGroup.stories.length > 0) {
      openViewer(myGroup.user_id);
    } else {
      // Explain the flow rather than dumping the user into /archive
      // (which was misleading — the user had to also find the share
      // button on a feud). One informative alert is friendlier.
      Alert.alert(
        "Come pubblicare una storia",
        "Apri una faida che ti interessa, tocca il pulsante Condividi e scegli \"Aggiungi alla tua storia\".",
        [{ text: "Ho capito" }],
      );
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
          <View style={[styles.ring, myGroup?.has_unseen ? styles.ringUnseen : styles.ringMine]}>
            <View style={styles.avatarWrap}>
              {user?.photos && user.photos[0]?.data ? (
                <Image source={{ uri: user.photos[0].data }} style={styles.avatar} />
              ) : (
                <View style={[styles.avatar, styles.avatarFallback]}>
                  <Ionicons name="person" size={26} color={colors.muted} />
                </View>
              )}
              {(!myGroup || myGroup.stories.length === 0) && (
                <View style={styles.plusBadge}>
                  <Ionicons name="add" size={14} color="#fff" />
                </View>
              )}
            </View>
          </View>
          <Text style={styles.label} numberOfLines={1}>
            {myGroup && myGroup.stories.length > 0 ? "Tua storia" : "Le tue storie"}
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
            <View style={[styles.ring, g.has_unseen ? styles.ringUnseen : styles.ringSeen]}>
              <View style={styles.avatarWrap}>
                {g.author?.avatar ? (
                  <Image source={{ uri: g.author.avatar }} style={styles.avatar} />
                ) : (
                  <View style={[styles.avatar, styles.avatarFallback]}>
                    <Ionicons name="person" size={26} color={colors.muted} />
                  </View>
                )}
              </View>
            </View>
            <Text style={styles.label} numberOfLines={1}>
              {g.author?.nickname || g.author?.display_name || "utente"}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
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
});
