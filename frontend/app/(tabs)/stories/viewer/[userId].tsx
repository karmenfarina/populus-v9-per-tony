import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  Image,
  ActivityIndicator,
  TextInput,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Animated,
  Easing,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font } from "@/src/theme";

/**
 * Fullscreen story viewer — Instagram-style vertical experience.
 *
 * Route: /stories/viewer/[userId]
 *
 * Loads all currently-active stories of the target user, then plays
 * them back-to-back with:
 *  - N progress bars pinned at the top (one per story, filled left-to-
 *    right as the currently-playing story elapses). Fully-played bars
 *    stay solid; upcoming ones stay dimmed.
 *  - Author header with avatar, nickname, time-ago and close button.
 *  - Central feud card — tapping it opens the underlying feud
 *    detail page.
 *  - Author's optional comment rendered below the feud card.
 *  - Reply row (only for OTHER users' stories) that fires off a DM.
 *  - Tap right half → next story. Tap left half → previous story.
 *  - Auto-advance after STORY_DURATION_MS. On the last story, closing
 *    returns to the home screen.
 *
 * Ownership rules:
 *  - Viewing my OWN stories: no reply row, but a "delete" button.
 *  - Viewing OTHER users' stories: reply row visible, no delete.
 *
 * `viewed` state is written back to the backend once per story via
 * POST /api/stories/{id}/view — idempotent + adds this viewer to the
 * story's `viewers` array so the author sees who watched.
 */

const STORY_DURATION_MS = 7000;

type StoryFeud = {
  feud_id: string;
  title?: string;
  category?: string;
  category_label?: string;
  party_a?: string;
  party_b?: string;
  image_url?: string;
  summary?: string;
} | null;

type Story = {
  story_id: string;
  user_id: string;
  comment: string;
  created_at: string;
  expires_at: string;
  feud: StoryFeud;
  author: {
    user_id: string;
    nickname?: string | null;
    display_name?: string | null;
    avatar?: string | null;
  } | null;
  viewed: boolean;
};

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "adesso";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}g`;
}

export default function StoriesViewer() {
  const router = useRouter();
  const { userId } = useLocalSearchParams<{ userId: string }>();
  const { user: me } = useAuth();
  const [stories, setStories] = useState<Story[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [paused, setPaused] = useState(false);

  const progress = useRef(new Animated.Value(0)).current;
  const timerRef = useRef<any>(null);
  const startTsRef = useRef<number>(Date.now());
  // Fraction of the current story already played — used to resume with
  // the correct remaining duration after a pause (long-press / focus).
  const elapsedFracRef = useRef<number>(0);

  const currentStory = stories[idx] || null;
  const isOwnStory = me?.user_id === currentStory?.user_id;

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const r: any = await api.storiesByUser(userId as string);
      const rows: Story[] = r?.stories || [];
      if (!rows.length) {
        // Nothing to show — bail back to the previous screen. Wrapped
        // in setTimeout so React unmount doesn't fight our animation.
        setTimeout(() => router.back(), 100);
        return;
      }
      setStories(rows);
      // Start at the first unseen — matches Instagram's "resume where
      // you left off" behavior. If everything is seen, start at 0.
      const firstUnseen = rows.findIndex((s) => !s.viewed);
      setIdx(firstUnseen >= 0 ? firstUnseen : 0);
    } catch (e: any) {
      Alert.alert("Errore", e?.message || "Impossibile caricare le storie");
      router.back();
    } finally {
      setLoading(false);
    }
  }, [userId, router]);

  useEffect(() => {
    load();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  // Progress-bar animation + auto-advance timer. Kicks off whenever the
  // current story index changes AND we're not paused. The `paused`
  // check lets long-press-to-hold behave like Instagram: hold anywhere
  // on the screen to freeze both the timer and the progress fill.
  useEffect(() => {
    if (loading || !currentStory) return;
    if (paused) return;
    progress.setValue(elapsedFracRef.current);
    startTsRef.current = Date.now();
    const remainingMs = STORY_DURATION_MS * (1 - elapsedFracRef.current);
    Animated.timing(progress, {
      toValue: 1,
      duration: remainingMs,
      easing: Easing.linear,
      useNativeDriver: false,
    }).start();
    timerRef.current = setTimeout(() => {
      elapsedFracRef.current = 0;
      advance(1);
    }, remainingMs);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      progress.stopAnimation();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, loading, paused, currentStory?.story_id]);

  // Fire the view-mark exactly once per story. Fire-and-forget — no
  // error handling because a missed mark just means the author's
  // "seen by" count is one lower, no user-visible impact.
  useEffect(() => {
    if (!currentStory || currentStory.viewed) return;
    api.markStoryViewed(currentStory.story_id).catch(() => { /* noop */ });
    // Locally flip the flag so we don't POST twice for the same story
    // if the user paginates back and forth.
    setStories((prev) => prev.map((s, i) => (i === idx ? { ...s, viewed: true } : s)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStory?.story_id]);

  const advance = (delta: number) => {
    setIdx((cur) => {
      const next = cur + delta;
      if (next < 0) return 0;
      if (next >= stories.length) {
        // End of this author's queue → close the viewer.
        setTimeout(() => router.back(), 50);
        return cur;
      }
      elapsedFracRef.current = 0;
      return next;
    });
  };

  const onPressLeft = () => {
    elapsedFracRef.current = 0;
    if (idx === 0) {
      // Restart current story instead of leaving the viewer.
      progress.setValue(0);
      return;
    }
    advance(-1);
  };

  const onPressRight = () => {
    elapsedFracRef.current = 0;
    advance(1);
  };

  const onLongPressStart = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    progress.stopAnimation((current) => {
      elapsedFracRef.current = current as number;
    });
    setPaused(true);
  };

  const onLongPressEnd = () => {
    setPaused(false);
  };

  const openFeud = () => {
    if (!currentStory?.feud?.feud_id) return;
    // Pause the ticker so the user comes back to the same story after
    // returning from the feud detail page.
    if (timerRef.current) clearTimeout(timerRef.current);
    router.push({
      pathname: "/feud/[id]",
      params: {
        id: currentStory.feud.feud_id,
        from: `/stories/viewer/${userId}`,
      },
    } as any);
  };

  const sendReply = async () => {
    if (!currentStory) return;
    const txt = replyText.trim();
    if (!txt || sending) return;
    setSending(true);
    try {
      await api.replyToStory(currentStory.story_id, txt);
      setReplyText("");
      Alert.alert("Inviato", "Il tuo messaggio è stato recapitato.");
    } catch (e: any) {
      Alert.alert("Errore", e?.message || "Impossibile inviare la risposta");
    } finally {
      setSending(false);
    }
  };

  const confirmDelete = () => {
    if (!currentStory) return;
    Alert.alert(
      "Elimina storia",
      "Sicuro di voler eliminare questa storia? Non sarà più visibile a nessuno.",
      [
        { text: "Annulla", style: "cancel" },
        {
          text: "Elimina",
          style: "destructive",
          onPress: async () => {
            try {
              await api.deleteStory(currentStory.story_id);
              setStories((prev) => prev.filter((_, i) => i !== idx));
              if (stories.length <= 1) {
                router.back();
              } else if (idx >= stories.length - 1) {
                setIdx(Math.max(0, idx - 1));
              }
            } catch (e: any) {
              Alert.alert("Errore", e?.message || "Impossibile eliminare la storia");
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.center}>
          <ActivityIndicator color="#fff" size="large" />
        </View>
      </SafeAreaView>
    );
  }

  if (!currentStory) return null;

  const author = currentStory.author;

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]} testID="stories-viewer">
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        {/* Progress-bar strip. Only the currently active bar animates;
            past bars are solid, future bars are dimmed. */}
        <View style={styles.progressStrip}>
          {stories.map((_, i) => (
            <View key={i} style={styles.progressTrack}>
              <Animated.View
                style={[
                  styles.progressFill,
                  {
                    width:
                      i < idx
                        ? "100%"
                        : i === idx
                          ? progress.interpolate({
                              inputRange: [0, 1],
                              outputRange: ["0%", "100%"],
                            })
                          : "0%",
                  },
                ]}
              />
            </View>
          ))}
        </View>

        {/* Author header */}
        <View style={styles.headerRow}>
          <View style={styles.headerAvatarWrap}>
            {author?.avatar ? (
              <Image source={{ uri: author.avatar }} style={styles.headerAvatar} />
            ) : (
              <View style={[styles.headerAvatar, styles.headerAvatarFallback]}>
                <Ionicons name="person" size={16} color={colors.muted} />
              </View>
            )}
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.headerNick} numberOfLines={1}>
              {author?.nickname || author?.display_name || "utente"}
            </Text>
            <Text style={styles.headerTime}>{timeAgo(currentStory.created_at)}</Text>
          </View>
          {isOwnStory ? (
            <Pressable onPress={confirmDelete} style={styles.headerBtn} testID="story-delete">
              <Ionicons name="trash-outline" size={20} color="#fff" />
            </Pressable>
          ) : null}
          <Pressable onPress={() => router.back()} style={styles.headerBtn} testID="story-close">
            <Ionicons name="close" size={26} color="#fff" />
          </Pressable>
        </View>

        {/* Body — tap zones layered underneath the interactive card */}
        <View style={styles.body}>
          {/* Invisible tap zones on left/right thirds for prev/next */}
          <Pressable
            onPress={onPressLeft}
            onLongPress={onLongPressStart}
            onPressOut={onLongPressEnd}
            style={[styles.tapZone, { left: 0 }]}
            testID="story-tap-prev"
          />
          <Pressable
            onPress={onPressRight}
            onLongPress={onLongPressStart}
            onPressOut={onLongPressEnd}
            style={[styles.tapZone, { right: 0 }]}
            testID="story-tap-next"
          />

          {/* Feud card — the "content" of the story */}
          <View style={styles.card}>
            {currentStory.feud ? (
              <Pressable onPress={openFeud} testID="story-open-feud">
                {currentStory.feud.image_url ? (
                  <Image
                    source={{ uri: currentStory.feud.image_url }}
                    style={styles.cardImage}
                    resizeMode="cover"
                  />
                ) : null}
                <View style={styles.cardBody}>
                  <Text style={styles.cardCat} numberOfLines={1}>
                    {(currentStory.feud.category_label || currentStory.feud.category || "").toUpperCase()}
                  </Text>
                  <Text style={styles.cardTitle} numberOfLines={4}>
                    {currentStory.feud.title}
                  </Text>
                  <View style={styles.cardVsRow}>
                    <View style={[styles.cardParty, { backgroundColor: colors.brandPrimary }]}>
                      <Text style={styles.cardPartyTxt} numberOfLines={2}>
                        {currentStory.feud.party_a}
                      </Text>
                    </View>
                    <Text style={styles.cardVs}>VS</Text>
                    <View style={[styles.cardParty, { backgroundColor: colors.brandSecondary }]}>
                      <Text style={[styles.cardPartyTxt, { color: colors.onBrandSecondary }]} numberOfLines={2}>
                        {currentStory.feud.party_b}
                      </Text>
                    </View>
                  </View>
                  <View style={styles.cardCta}>
                    <Text style={styles.cardCtaTxt}>APRI LA FAIDA</Text>
                    <Ionicons name="chevron-forward" size={16} color={colors.brandPrimary} />
                  </View>
                </View>
              </Pressable>
            ) : (
              <View style={styles.cardMissing}>
                <Ionicons name="alert-circle-outline" size={40} color={colors.muted} />
                <Text style={styles.cardMissingTxt}>Faida non più disponibile</Text>
              </View>
            )}

            {currentStory.comment ? (
              <View style={styles.commentBox}>
                <Text style={styles.commentTxt}>{currentStory.comment}</Text>
              </View>
            ) : null}
          </View>
        </View>

        {/* Reply row — hidden on my own stories */}
        {!isOwnStory ? (
          <View style={styles.replyRow}>
            <TextInput
              value={replyText}
              onChangeText={setReplyText}
              placeholder={`Rispondi a ${author?.nickname || "utente"}…`}
              placeholderTextColor="rgba(255,255,255,0.6)"
              style={styles.replyInput}
              editable={!sending}
              onFocus={() => setPaused(true)}
              onBlur={() => setPaused(false)}
              testID="story-reply-input"
            />
            <Pressable
              onPress={sendReply}
              disabled={sending || !replyText.trim()}
              style={[styles.replySend, (sending || !replyText.trim()) && { opacity: 0.4 }]}
              testID="story-reply-send"
            >
              {sending ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Ionicons name="paper-plane" size={18} color="#fff" />
              )}
            </Pressable>
          </View>
        ) : null}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#000" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  progressStrip: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    gap: 4,
  },
  progressTrack: {
    flex: 1,
    height: 3,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.28)",
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: "#fff",
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  headerAvatarWrap: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 2,
    borderColor: "#fff",
    padding: 1,
  },
  headerAvatar: {
    width: 30,
    height: 30,
    borderRadius: 15,
  },
  headerAvatarFallback: {
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  headerNick: {
    color: "#fff",
    fontSize: font.sizes.sm,
    fontWeight: "700",
  },
  headerTime: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 11,
    marginTop: 1,
  },
  headerBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    paddingHorizontal: spacing.md,
    justifyContent: "center",
  },
  tapZone: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: "35%",
    zIndex: 0,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    overflow: "hidden",
    zIndex: 1,
  },
  cardImage: {
    width: "100%",
    height: 180,
    backgroundColor: colors.surfaceTertiary,
  },
  cardBody: {
    padding: spacing.md,
  },
  cardCat: {
    color: colors.brandPrimary,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.5,
    marginBottom: 4,
  },
  cardTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    lineHeight: 24,
  },
  cardVsRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: spacing.md,
    gap: spacing.sm,
  },
  cardParty: {
    flex: 1,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    borderRadius: 6,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  cardPartyTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    textAlign: "center",
    letterSpacing: 0.5,
  },
  cardVs: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    fontWeight: "700",
  },
  cardCta: {
    marginTop: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
  },
  cardCtaTxt: {
    color: colors.brandPrimary,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  cardMissing: {
    padding: spacing.xl,
    alignItems: "center",
    gap: spacing.sm,
  },
  cardMissingTxt: {
    color: colors.muted,
    fontSize: font.sizes.sm,
  },
  commentBox: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  commentTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    lineHeight: 20,
  },
  replyRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
    paddingTop: spacing.sm,
    gap: spacing.sm,
  },
  replyInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.5)",
    borderRadius: 999,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    color: "#fff",
    fontSize: font.sizes.sm,
  },
  replySend: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
});
