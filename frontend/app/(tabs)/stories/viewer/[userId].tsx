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
  Modal,
  Dimensions,
  type GestureResponderEvent,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
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
  // Progress is a plain number 0..1 driven by a setInterval so the
  // rendering stays deterministic across web + native (Animated.Value
  // with useNativeDriver:false was flaky on web at story transitions).
  const [progress, setProgress] = useState(0);
  // Cross-platform confirmation dialog for delete — Alert.alert with
  // multiple buttons does NOT render properly on React Native Web.
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  // Guard that flags "the interval has already scheduled a close" so
  // we don't pile up router.back()/replace calls when the last story
  // ends. It is NOT used by the X button, delete, or manual goNext —
  // those must always work even after an auto-advance close attempt.
  const autoCloseFiredRef = useRef(false);

  const closeViewer = useCallback(() => {
    // On web `router.back()` for tabs with `href:null` prints a
    // GO_BACK-not-handled warning and does nothing, so we use replace.
    // On native `router.back()` is preferred to keep the natural nav
    // history. Falls back to replace when there's no history to pop.
    if (Platform.OS === "web") {
      router.replace("/" as any);
      return;
    }
    if ((router as any).canGoBack?.()) {
      router.back();
    } else {
      router.replace("/" as any);
    }
  }, [router]);

  // Stories ref keeps the latest array reachable from the interval
  // callback without stale-closure captures.
  const storiesRef = useRef<Story[]>([]);
  const idxRef = useRef(0);
  const pausedRef = useRef(false);
  useEffect(() => { storiesRef.current = stories; }, [stories]);
  useEffect(() => { idxRef.current = idx; }, [idx]);
  useEffect(() => { pausedRef.current = paused; }, [paused]);

  const currentStory = stories[idx] || null;
  const isOwnStory = me?.user_id === currentStory?.user_id;

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const r: any = await api.storiesByUser(userId as string);
      const rows: Story[] = r?.stories || [];
      if (!rows.length) {
        setTimeout(() => closeViewer(), 100);
        return;
      }
      setStories(rows);
      const firstUnseen = rows.findIndex((s) => !s.viewed);
      setIdx(firstUnseen >= 0 ? firstUnseen : 0);
      setProgress(0);
    } catch (e: any) {
      Alert.alert("Errore", e?.message || "Impossibile caricare le storie");
      closeViewer();
    } finally {
      setLoading(false);
    }
  }, [userId, closeViewer]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  // Single global interval that ticks 20 times a second, driving the
  // progress bar of the CURRENT story. Uses `useFocusEffect` (not
  // useEffect) so the timer PAUSES when the user leaves this screen
  // (e.g. taps into a feud detail page) and RESUMES cleanly on
  // return — otherwise the progress bar would appear frozen after
  // coming back from a feud because either (a) the interval was
  // torn down and never re-started, or (b) it kept running while
  // the viewer was off-screen and closed the viewer prematurely.
  useFocusEffect(
    useCallback(() => {
      if (loading || stories.length === 0) return;
      // Reset the auto-close guard whenever the screen regains focus
      // so a viewer we came back to can still complete + close.
      autoCloseFiredRef.current = false;
      const TICK_MS = 50;
      const INCREMENT = TICK_MS / STORY_DURATION_MS;
      const timer = setInterval(() => {
        if (autoCloseFiredRef.current) {
          clearInterval(timer);
          return;
        }
        if (pausedRef.current) return;
        setProgress((p) => {
          const next = p + INCREMENT;
          if (next >= 1) {
            const currentIdx = idxRef.current;
            if (currentIdx + 1 >= storiesRef.current.length) {
              autoCloseFiredRef.current = true;
              clearInterval(timer);
              closeViewer();
              return 1;
            }
            setIdx(currentIdx + 1);
            return 0;
          }
          return next;
        });
      }, TICK_MS);
      return () => clearInterval(timer);
    }, [loading, stories.length, closeViewer]),
  );

  // Reset progress every time the user manually navigates to a new
  // story (either via tap or after auto-advance).
  useEffect(() => { setProgress(0); }, [idx]);

  // Fire the view-mark exactly once per story. Fire-and-forget — no
  // error handling because a missed mark just means the author's
  // "seen by" count is one lower, no user-visible impact.
  useEffect(() => {
    if (!currentStory || currentStory.viewed) return;
    api.markStoryViewed(currentStory.story_id).catch(() => { /* noop */ });
    setStories((prev) => prev.map((s, i) => (i === idxRef.current ? { ...s, viewed: true } : s)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStory?.story_id]);

  const goPrev = () => {
    if (idx === 0) { setProgress(0); return; }
    setIdx(idx - 1);
    setProgress(0);
  };

  // Tap on the body decides prev/next based on the horizontal
  // position of the touch. Nested Pressables (the "APRI LA FAIDA"
  // CTA) claim their own taps first via RN's responder hierarchy, so
  // this ONLY fires when the user tapped somewhere OTHER than the
  // CTA button — i.e. the story visual area or the empty side space.
  const onBodyPress = (e: GestureResponderEvent) => {
    const screenW = Dimensions.get("window").width;
    const x = e.nativeEvent.pageX;
    if (x < screenW / 2) goPrev(); else goNext();
  };

  const goNext = () => {
    if (idx + 1 >= stories.length) {
      closeViewer();
      return;
    }
    setIdx(idx + 1);
    setProgress(0);
  };

  const onLongPressStart = () => setPaused(true);
  const onLongPressEnd = () => setPaused(false);

  const openFeud = () => {
    if (!currentStory?.feud?.feud_id) return;
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

  // Trigger the cross-platform delete confirmation modal instead of
  // Alert.alert — the multi-button variant of the latter is not
  // supported on React Native Web (only shows a single OK dialog).
  const confirmDelete = () => {
    if (!currentStory) return;
    setPaused(true);
    setConfirmDeleteOpen(true);
  };

  const doDelete = async () => {
    if (!currentStory) return;
    const toDeleteIdx = idx;
    const remaining = stories.length - 1;
    setConfirmDeleteOpen(false);
    try {
      await api.deleteStory(currentStory.story_id);
      if (remaining <= 0) {
        closeViewer();
        return;
      }
      setStories((prev) => prev.filter((_, i) => i !== toDeleteIdx));
      // Adjust idx if we deleted the last one.
      if (toDeleteIdx >= remaining) {
        setIdx(Math.max(0, remaining - 1));
      }
      setProgress(0);
    } catch (e: any) {
      Alert.alert("Errore", e?.message || "Impossibile eliminare la storia");
    } finally {
      setPaused(false);
    }
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
              <View
                style={[
                  styles.progressFill,
                  {
                    width:
                      i < idx
                        ? "100%"
                        : i === idx
                          ? `${Math.min(100, Math.max(0, progress * 100))}%`
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
          <Pressable onPress={closeViewer} style={styles.headerBtn} testID="story-close">
            <Ionicons name="close" size={26} color="#fff" />
          </Pressable>
        </View>

        {/* Body wraps everything in a single Pressable that decides
            prev/next based on the tap X coordinate. Nested Pressables
            inside (the "APRI LA FAIDA" CTA) intercept their own taps
            first thanks to React Native's native touch responder
            hierarchy — the outer onPress only fires when NO nested
            Pressable claimed the touch. This design guarantees taps
            anywhere on the screen advance the story, EXCEPT on the
            explicit "APRI LA FAIDA" button. */}
        <Pressable
          onPress={onBodyPress}
          onLongPress={onLongPressStart}
          onPressOut={onLongPressEnd}
          style={styles.body}
          testID="story-body"
        >
          {/* Card + CTA wrapped in a container that has the horizontal
              padding — the outer `body` deliberately has NO padding so
              taps at the very edges of the screen still register. */}
          <View style={styles.cardWrap}>
          <View style={[styles.card, { pointerEvents: "none" as any }]}>
            {currentStory.feud ? (
              <>
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
                </View>
              </>
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

          {/* Explicit "open feud" CTA — sibling of the card so it can
              still receive taps despite the card being pointer-events:
              none. Positioned right below the card in the layout flow. */}
          {currentStory.feud ? (
            <Pressable onPress={openFeud} style={styles.openFeudBtn} testID="story-open-feud">
              <Text style={styles.openFeudTxt}>APRI LA FAIDA</Text>
              <Ionicons name="chevron-forward" size={16} color={colors.brandPrimary} />
            </Pressable>
          ) : null}
          </View>
        </Pressable>

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

      {/* Cross-platform delete confirmation dialog. Uses a plain Modal
          because RN Web's `Alert.alert` doesn't render multi-button
          alerts natively — the "Elimina" button was completely
          silent on the web preview otherwise. */}
      <Modal
        visible={confirmDeleteOpen}
        transparent
        animationType="fade"
        onRequestClose={() => { setConfirmDeleteOpen(false); setPaused(false); }}
      >
        <Pressable
          style={styles.confirmBackdrop}
          onPress={() => { setConfirmDeleteOpen(false); setPaused(false); }}
        >
          <Pressable style={styles.confirmSheet} onPress={() => { /* consume */ }}>
            <Text style={styles.confirmTitle}>Elimina storia</Text>
            <Text style={styles.confirmMsg}>
              Sicuro di voler eliminare questa storia? Non sarà più visibile a nessuno.
            </Text>
            <View style={styles.confirmBtnRow}>
              <Pressable
                style={[styles.confirmBtn, styles.confirmBtnCancel]}
                onPress={() => { setConfirmDeleteOpen(false); setPaused(false); }}
                testID="story-delete-cancel"
              >
                <Text style={styles.confirmBtnCancelTxt}>ANNULLA</Text>
              </Pressable>
              <Pressable
                style={[styles.confirmBtn, styles.confirmBtnDelete]}
                onPress={doDelete}
                testID="story-delete-confirm"
              >
                <Text style={styles.confirmBtnDeleteTxt}>ELIMINA</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
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
    // NO horizontal padding here — the tap zones (position:absolute
    // left:0 / right:0) must cover the ENTIRE screen width, edge to
    // edge, so a user resting their thumb near the border still hits
    // prev/next. Card padding is applied via `cardWrap` below.
    justifyContent: "center",
  },
  cardWrap: {
    paddingHorizontal: spacing.md,
    alignItems: "stretch",
  },
  tapZone: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: "50%",
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
    marginTop: 0,
    marginHorizontal: 0,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
  },
  cardCtaTxt: {
    color: colors.brandPrimary,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  openFeudBtn: {
    marginTop: spacing.sm,
    alignSelf: "center",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surface,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: colors.brandPrimary,
    zIndex: 2,
  },
  openFeudTxt: {
    color: colors.brandPrimary,
    fontSize: 12,
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
  // Delete-confirm modal — cross-platform alternative to
  // Alert.alert(multiple buttons) which doesn't work on RN Web.
  confirmBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
  },
  confirmSheet: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: spacing.lg,
    maxWidth: 380,
    width: "100%",
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 1,
    marginBottom: spacing.sm,
  },
  confirmMsg: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 20,
    marginBottom: spacing.lg,
  },
  confirmBtnRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  confirmBtn: {
    flex: 1,
    paddingVertical: spacing.md,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 6,
  },
  confirmBtnCancel: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  confirmBtnDelete: {
    backgroundColor: colors.brandPrimary,
  },
  confirmBtnCancelTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  confirmBtnDeleteTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
});
