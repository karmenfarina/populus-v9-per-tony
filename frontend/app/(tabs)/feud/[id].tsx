import { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform, ImageBackground, Linking, Alert, BackHandler,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as Clipboard from "expo-clipboard";
import { api, ApiError, Comment, Feud, Reply, Sponsor } from "@/src/api";
import { colors, spacing, font, sideColor, onSideColor, radius } from "@/src/theme";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import MentionInput from "@/src/components/MentionInput";
import MentionText from "@/src/components/MentionText";
import FeudMediaBlock from "@/src/components/FeudMediaBlock";
import FeudStatsModal from "@/src/components/FeudStatsModal";
import AiFactionSummaryModal from "@/src/components/AiFactionSummaryModal";
import ShareSheet from "@/src/components/ShareSheet";
import InAppShareSheet from "@/src/components/InAppShareSheet";
import ConfirmModal from "@/src/components/ConfirmModal";
import { useNotifications } from "@/src/notifications/NotificationsContext";
import AdBanner from "@/src/ads/AdBanner";
import { useUIPrefs } from "@/src/ui/UIPrefs";
import { useAuth } from "@/src/auth/AuthContext";
import { blockEvents } from "@/src/utils/blockEvents";
import { scrollMemory } from "@/src/utils/scrollMemory";

export default function FeudDetail() {
  const { id, comment: commentParam, side: sideParam, from, archiveCat, archiveDate, messagesUserId } =
    useLocalSearchParams<{
      id: string; comment?: string; side?: string; from?: string;
      archiveCat?: string; archiveDate?: string; messagesUserId?: string;
    }>();
  const router = useRouter();
  const { user } = useAuth();
  // Notifications badge polling context — we call refresh() after any
  // action that MAY have earned the user a new badge server-side
  // (voting for alignment badges, commenting for category badges), so
  // the tab-bar counter updates within a couple of seconds instead of
  // waiting up to 30s for the next scheduled poll. The 500ms delay
  // covers the fire-and-forget `_evaluate_and_notify_*_badge_change`
  // running in a background task on the backend.
  const { refresh: refreshUnreadNotifs } = useNotifications();
  const isAnonymous = !!user && user.auth_provider === "anonymous";

  // Explicit back destination: when we know which tab launched us we return
  // there directly (via router.replace) so expo-router's tab-stack doesn't
  // dump us on the wrong tab. Falls back to router.back() when unknown.
  const goBack = () => {
    if (from === "top") { router.replace("/top"); return; }
    if (from === "notifications") { router.replace("/notifications"); return; }
    // From a story viewer — return to the SAME author's viewer so
    // the user resumes the queue where they left off. The `from`
    // param is expected to be `/stories/viewer/{userId}` (the exact
    // path the caller constructed).
    if (typeof from === "string" && from.startsWith("/stories/viewer/")) {
      router.replace(from as any);
      return;
    }
    // When opened from a chat message (shared_feud card), go back to that
    // exact conversation — not the tab root or the feed. If for some reason
    // we don't have the counterparty user_id, fall back to the messages
    // conversation list.
    if (from === "messages") {
      const uid = (messagesUserId as string) || "";
      if (uid) {
        router.replace(`/messages/${encodeURIComponent(uid)}`);
      } else {
        router.replace("/messages");
      }
      return;
    }
    // When launched from the archive, return to the SAME archive day and
    // category the user was viewing — not the default "all / newest" state.
    if (from === "archive") {
      const cat = (archiveCat as string) || "all";
      const date = (archiveDate as string) || "";
      const qs = date
        ? `?category=${encodeURIComponent(cat)}&date=${encodeURIComponent(date)}`
        : `?category=${encodeURIComponent(cat)}`;
      router.replace(`/archive${qs}`);
      return;
    }
    // Explicit path override: the caller can pass any absolute path
    // (starting with "/") via `?from=`. We use `router.replace` here
    // rather than `router.back()` because the tabs navigator does NOT
    // grow a real back-stack when jumping between hidden `href: null`
    // routes — `back()` would land on "/" instead of the intended
    // parent. Used by profile → /feud/X and /user/[id] → /feud/X so
    // the user returns to the exact history list they were browsing.
    if (typeof from === "string" && from.startsWith("/")) {
      router.replace(from as any);
      return;
    }
    if (router.canGoBack && router.canGoBack()) router.back();
    else router.replace("/");
  };

  // Android hardware back button — must land on the same archive day/category
  // we came from, not on the previous tab route or the feed. iOS swipe-back
  // still uses the native stack, which our archive component already restores
  // via the `category` + `date` params (see /archive?category=X&date=Y).
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      goBack();
      return true; // intercept
    });
    return () => { try { sub.remove(); } catch { /* noop */ } };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, archiveCat, archiveDate, messagesUserId]);
  const [feud, setFeud] = useState<Feud | null>(null);
  const [sponsor, setSponsor] = useState<Sponsor | null>(null);
  const [sideA, setSideA] = useState<Comment[]>([]);
  const [sideB, setSideB] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, Reply[]>>({});
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");
  const [activeSide, setActiveSide] = useState<"A" | "B" | null>(null);
  const [statsOpen, setStatsOpen] = useState(false);
  const [aiSummaryOpen, setAiSummaryOpen] = useState(false);
  // Toggle between "LA FAIDA" (summary) and "CONTESTO" (context_text).
  // Reset back to `false` every time the feud id changes so opening a new
  // post never inherits the previous one's toggled state.
  const [showContext, setShowContext] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [inAppShareOpen, setInAppShareOpen] = useState(false);
  const { sourcesExpanded, setSourcesExpanded } = useUIPrefs();
  const scrollRef = useRef<ScrollView>(null);
  // Floating "back to top" pill: appears once the user scrolls past the hero
  // + poll and is deep into comments. Threshold raised so the button doesn't
  // pop up while the user is still reading the article.
  const [showTopBtn, setShowTopBtn] = useState(false);
  // Y position of the comments section header (captured via onLayout).
  // Used as the scroll target so the pill returns to the start of comments,
  // not to the top of the page. Fallback 0 until first layout.
  const commentsYRef = useRef(0);

  // Clear all per-post state the instant the route param changes so the
  // screen never shows the *previous* post's contents while the next one
  // is being fetched. expo-router keeps the mounted screen around when we
  // navigate /feud/A → /feud/B, so without an explicit reset the stale
  // `feud`, `sideA`, `sideB`, `sponsor` would flash for a frame.
  useEffect(() => {
    setFeud(null);
    setSideA([]);
    setSideB([]);
    setSponsor(null);
    setExpanded({});
    setActiveSide(null);
    setLoading(true);
    setError(null);
    setGone(false);
    setShowContext(false);
    // Fresh visit to this feud → wipe any leftover scroll offset from a
    // previous session so the restore-on-focus effect doesn't yank the
    // user mid-hero. We only want restoration when they COME BACK from
    // a child screen inside the SAME session.
    if (id) scrollMemory.setY(`feud:${id}`, 0);
    scrollRef.current?.scrollTo({ y: 0, animated: false });
  }, [id]);

  // Precompute the profile-owner uid derived from `from=/user/<uid>` so
  // every reload of comments floats their bubbles to the top consistently.
  const ownerUid: string | undefined = (() => {
    if (typeof from !== "string") return undefined;
    const m = from.match(/^\/user\/([^\/?#]+)/);
    return m ? m[1] : undefined;
  })();

  const loadAll = useCallback(async () => {
    const f = await api.feud(id!);
    setFeud(f.feud);
    // When the user opened this feud from another user's public vote history
    // (`from=/user/<uid>`), lift that owner's comments to the very top so the
    // viewer immediately sees what the profile they were browsing had to say
    // about this story.
    const [c, s] = await Promise.all([
      api.comments(id!, ownerUid),
      api.sponsors(f.feud.category).catch(() => ({ sponsors: [] })),
    ]);
    setSideA(c.side_a); setSideB(c.side_b);
    if (s.sponsors && s.sponsors.length > 0) setSponsor(s.sponsors[0]);
  }, [id, ownerUid]);

  useEffect(() => {
    (async () => {
      try { await loadAll(); }
      catch (e: any) {
        if (e instanceof ApiError && (e.status === 410 || e.status === 404)) {
          setGone(true);
        } else {
          setError(e?.message || "Errore");
        }
      }
      finally { setLoading(false); }
    })();
    // Fire-and-forget engagement signal for the personalized feed.
    if (id) { api.recordView(id); }
  }, [loadAll, id]);

  // Restore scroll position on focus — every time the screen re-focuses
  // (child screen popped, share sheet dismissed, etc.), if we've stashed
  // a non-zero offset for this feud in scrollMemory, jump the ScrollView
  // back to that spot AFTER the content has laid out. Without this, a
  // trip to /user/[id] and back would silently reset the view to the
  // top of the article, forcing the user to scroll all the way down to
  // find the comment they were reading. Handles the exact spec:
  // "tornando indietro devo essere riportato alla stessa faida,
  // esattamente nel punto dei commenti che avevo lasciato".
  const restoredOnceRef = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (!id) return;
      const y = scrollMemory.getY(`feud:${id}`);
      if (y <= 0) return;
      // Defer the scroll until after the first paint so the ScrollView
      // has its content measured. Two rAFs cover both native (single
      // pass) and web (React scheduler + browser paint).
      const t1 = setTimeout(() => {
        try { scrollRef.current?.scrollTo({ y, animated: false }); } catch { /* noop */ }
      }, 0);
      const t2 = setTimeout(() => {
        try { scrollRef.current?.scrollTo({ y, animated: false }); } catch { /* noop */ }
        restoredOnceRef.current = true;
      }, 120);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }, [id]),
  );

  // Refresh comments whenever the screen re-focuses (e.g. user tapped a
  // commenter's avatar → went to their profile → blocked them → came
  // back). Without this, the comment list would still show the blocked
  // user's messages because they were fetched BEFORE the block.
  // We only refetch comments (not the feud + sponsor) to keep the
  // return trip snappy — the feud itself doesn't need to reload.
  useFocusEffect(
    useCallback(() => {
      if (!id) return;
      const t = setTimeout(async () => {
        try {
          const c = await api.comments(id, ownerUid);
          setSideA(c.side_a);
          setSideB(c.side_b);
          // Refetch every currently-expanded reply thread so that any
          // reply from a user who was blocked WHILE we were away from
          // the screen (e.g. user navigated to that user's profile
          // and tapped "Blocca") disappears from the thread too.
          // Without this the top-level comments update on focus but
          // the expanded reply lists remain frozen at the pre-block
          // snapshot — the exact bug reported after iteration 113.
          setExpanded((prev) => {
            const openIds = Object.keys(prev);
            if (openIds.length === 0) return prev;
            // Kick off refetches in parallel; we don't await here
            // because the effect must return synchronously. The
            // setter callback below patches state as each promise
            // resolves.
            openIds.forEach((cid) => {
              api.replies(cid)
                .then((r) => {
                  setExpanded((cur) => (cid in cur ? { ...cur, [cid]: r.replies } : cur));
                })
                .catch(() => { /* silent */ });
            });
            return prev;
          });
        } catch { /* silent — the mount fetch handles hard errors */ }
      }, 50);
      return () => clearTimeout(t);
    }, [id, ownerUid]),
  );

  // Global block/unblock listener — refetch comments + expanded replies
  // the instant the block list changes anywhere in the app (e.g. user
  // blocked someone from a chat message screen, or unblocked from the
  // Profile settings). Without this, staying on the feud detail while
  // the block happened would leave the stale comments visible.
  useEffect(() => {
    return blockEvents.subscribe(() => {
      if (!id) return;
      (async () => {
        try {
          const c = await api.comments(id, ownerUid);
          setSideA(c.side_a);
          setSideB(c.side_b);
        } catch { /* silent */ }
      })();
      setExpanded((prev) => {
        const openIds = Object.keys(prev);
        openIds.forEach((cid) => {
          api.replies(cid)
            .then((r) => {
              setExpanded((cur) => (cid in cur ? { ...cur, [cid]: r.replies } : cur));
            })
            .catch(() => { /* silent */ });
        });
        return prev;
      });
    });
  }, [id, ownerUid]);

  // Deep-link from a notification: if a `comment` param is present, activate
  // the correct side tab and auto-expand that comment's reply thread so the
  // user sees the reply without any extra taps.
  const deepLinkHandledRef = useRef(false);
  useEffect(() => {
    if (deepLinkHandledRef.current) return;
    if (!feud || !commentParam) return;
    // Wait until comments are loaded before deciding which side hosts it.
    const cA = sideA.find((c) => c.comment_id === commentParam);
    const cB = sideB.find((c) => c.comment_id === commentParam);
    const target: "A" | "B" | null =
      (sideParam === "A" || sideParam === "B")
        ? (sideParam as "A" | "B")
        : (cA ? "A" : cB ? "B" : null);
    if (!target) return;
    deepLinkHandledRef.current = true;
    setActiveSide(target);
    // Auto-expand replies to reveal the notification's context.
    toggleReplies(commentParam).catch(() => { /* silent */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feud, sideA, sideB, commentParam, sideParam]);

  const vote = async (side: "A" | "B") => {
    if (!feud) return;
    // Same side & already voted → no-op (button is greyed out anyway).
    if (feud.my_vote === side) return;
    // If switching sides, respect the change limit.
    if (feud.my_vote && (feud.my_vote_changes_left ?? 0) <= 0) {
      setError("Hai raggiunto il limite di cambi voto");
      return;
    }
    setVoting(true); setError(null);
    try {
      try { await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium); } catch {}
      const res = await api.vote(feud.feud_id, side);
      setFeud(res.feud);
      // Reload comments so nickname_side reflects the new faction everywhere.
      try {
        const c = await api.comments(feud.feud_id, ownerUid);
        setSideA(c.side_a); setSideB(c.side_b);
      } catch {}
      // Voting can unlock an alignment badge (buon_senso / bastian_contrario).
      // Give the backend a moment to write the notification, then refresh.
      // Fire two refreshes (fast + safety-net) so the badge counter lights
      // up even if the first refresh races the async task on slow DB.
      setTimeout(() => { refreshUnreadNotifs().catch(() => {}); }, 800);
      setTimeout(() => { refreshUnreadNotifs().catch(() => {}); }, 2500);
    } catch (e: any) { setError(e?.message || "Errore"); }
    finally { setVoting(false); }
  };

  const submitComment = async () => {
    if (!feud || !commentText.trim()) return;
    setPosting(true);
    try {
      await api.addComment(feud.feud_id, commentText.trim());
      setCommentText("");
      const c = await api.comments(feud.feud_id, ownerUid);
      setSideA(c.side_a); setSideB(c.side_b);
      // Commenting can cross a category-badge threshold (100/250/500).
      // Two-shot refresh so the tab badge lights up even under DB load.
      setTimeout(() => { refreshUnreadNotifs().catch(() => {}); }, 800);
      setTimeout(() => { refreshUnreadNotifs().catch(() => {}); }, 2500);
    } catch (e: any) { setError(e?.message || "Errore"); }
    finally { setPosting(false); }
  };

  const toggleFavorite = async () => {
    if (!feud || isAnonymous) return;
    const nextVal = !feud.is_favorite;
    // Optimistic UI: flip the icon instantly, revert on error
    setFeud({ ...feud, is_favorite: nextVal });
    try { await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
    try {
      if (nextVal) {
        await api.addFavorite(feud.feud_id);
      } else {
        await api.removeFavorite(feud.feud_id);
      }
    } catch (e: any) {
      // Revert on failure
      setFeud({ ...feud, is_favorite: !nextVal });
      setError(e?.detail || e?.message || "Errore nel salvataggio del preferito");
    }
  };

  const toggleReplies = async (commentId: string) => {
    if (expanded[commentId]) {
      const copy = { ...expanded }; delete copy[commentId]; setExpanded(copy);
      return;
    }
    const r = await api.replies(commentId);
    setExpanded((prev) => ({ ...prev, [commentId]: r.replies }));
  };

  /**
   * Idempotent "expand" — used by the "rispondi" tap so the user can
   * see existing replies while composing their own. If the thread is
   * already expanded this is a silent no-op (no extra network call).
   */
  const expandReplies = async (commentId: string) => {
    if (expanded[commentId]) return;
    try {
      const r = await api.replies(commentId);
      setExpanded((prev) => ({ ...prev, [commentId]: r.replies }));
    } catch { /* silent — the toggle button still works as a fallback */ }
  };

  const submitReply = async (commentId: string) => {
    if (!replyText.trim()) return;
    try {
      await api.addReply(commentId, replyText.trim());
      setReplyText(""); setReplyingTo(null);
      const r = await api.replies(commentId);
      setExpanded((prev) => ({ ...prev, [commentId]: r.replies }));
      // Keep the parent's "N risposte" label in sync with the new reply count.
      const bump = (list: Comment[]) => list.map((c) => c.comment_id === commentId
        ? { ...c, reply_count: r.replies.length }
        : c);
      setSideA(bump);
      setSideB(bump);
    } catch (e: any) { setError(e?.message || "Errore"); }
  };

  /**
   * Delete a comment the current user authored. Wired to the trash icon that
   * appears only when `c.user_id === me.user_id` (see CommentItem below).
   * On success we optimistically drop the comment from local state and
   * collapse any expanded reply thread.
   */
  const deleteOwnComment = async (commentId: string) => {
    try {
      await api.deleteComment(commentId);
    } catch (e: any) {
      setError(e?.detail || e?.message || "Impossibile eliminare");
      return;
    }
    setSideA((prev) => prev.filter((x) => x.comment_id !== commentId));
    setSideB((prev) => prev.filter((x) => x.comment_id !== commentId));
    setExpanded((prev) => {
      const copy = { ...prev };
      delete copy[commentId];
      return copy;
    });
  };

  /**
   * Delete a reply the current user authored. Refreshes the parent's reply
   * count so the "N risposte" label stays accurate.
   */
  const deleteOwnReply = async (commentId: string, replyId: string) => {
    try {
      await api.deleteReply(replyId);
    } catch (e: any) {
      setError(e?.detail || e?.message || "Impossibile eliminare");
      return;
    }
    setExpanded((prev) => {
      const list = (prev[commentId] || []).filter((r) => r.reply_id !== replyId);
      return { ...prev, [commentId]: list };
    });
    const dec = (list: Comment[]) => list.map((c) => c.comment_id === commentId
      ? { ...c, reply_count: Math.max(0, (c.reply_count ?? 1) - 1) }
      : c);
    setSideA(dec);
    setSideB(dec);
  };

  // Build the absolute URL used by all share targets. Falls back to
  // window.location.origin so the URL is always valid, even on embedded web
  // previews where EXPO_PUBLIC_BACKEND_URL might be empty.
  const buildShareUrl = () => {
    let base = process.env.EXPO_PUBLIC_BACKEND_URL || "";
    if ((!base || !/^https?:\/\//i.test(base)) && Platform.OS === "web" && typeof window !== "undefined") {
      base = window.location.origin;
    }
    return `${base}/api/share/${feud?.feud_id}/html`;
  };

  const copyShareLink = async () => {
    if (!feud) return;
    const url = buildShareUrl();
    if (Platform.OS === "web" && typeof navigator !== "undefined") {
      try {
        await navigator.clipboard.writeText(url);
        try { window.alert("Link copiato negli appunti"); } catch { /* ignore */ }
        return;
      } catch { /* try next */ }
      try { window.prompt("Copia il link:", url); } catch { /* ignore */ }
      return;
    }
    try {
      await Clipboard.setStringAsync(url);
      Alert.alert("Link copiato", "Il link della faida è stato copiato negli appunti.");
    } catch { /* ignore */ }
  };

  const onShare = () => {
    if (!feud) return;
    // Registered users get the in-app share sheet first (Instagram-style
    // "share to a friend" flow). Anonymous users can't send DMs, so we
    // fall back directly to the external social-share sheet for them.
    if (isAnonymous) {
      setShareOpen(true);
    } else {
      setInAppShareOpen(true);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.centerFill}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }
  if (gone) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="feud-gone-screen">
        <View style={styles.topbar}>
          <Pressable onPress={goBack} style={styles.backBtn} testID="gone-back-button">
            <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
            <Text style={styles.backTxt}>INDIETRO</Text>
          </Pressable>
        </View>
        <View style={styles.goneBox}>
          <Ionicons name="hourglass-outline" size={64} color={colors.brandSecondary} />
          <Text style={styles.goneTitle}>FAIDA SCADUTA</Text>
          <Text style={styles.goneMsg}>
            Errore: stai provando a visualizzare una faida che ha più di due settimane.
          </Text>
          <Text style={styles.goneHint}>
            Le faide vengono conservate per 14 giorni. Dopo questo periodo la discussione e i commenti vengono rimossi definitivamente.
          </Text>
          <Pressable onPress={() => router.replace("/")} style={styles.goneBtn} testID="gone-home-btn">
            <Text style={styles.goneBtnTxt}>TORNA ALLE FAIDE ATTIVE</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }
  if (!feud) return null;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="feud-detail-screen">
      <View style={styles.topbar}>
        <Pressable onPress={goBack} style={styles.backBtn} testID="back-button">
          <Ionicons name="chevron-back" size={22} color={colors.brandSecondary} />
          <Text style={styles.backTxt}>INDIETRO</Text>
        </Pressable>
        <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
          <Text style={styles.topCat}>{feud.category_label.toUpperCase()}</Text>
          <Pressable onPress={onShare} testID="share-button" style={styles.shareBtn}>
            <Ionicons name="share-outline" size={18} color={colors.brandSecondary} />
          </Pressable>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={80}>
        <ScrollView
          ref={scrollRef}
          contentContainerStyle={{ paddingBottom: spacing.xxxl }}
          onScroll={(e) => {
            // Show the pill only once the user has scrolled past the
            // comments header itself + a full viewport — otherwise the
            // pill pops up while still reading the article. If we don't
            // yet have a measured Y, fall back to a large hardcoded
            // threshold so the button doesn't appear at all in the article.
            const y = e.nativeEvent.contentOffset.y;
            const target = commentsYRef.current || 0;
            setShowTopBtn(target > 0 ? y > target + 400 : y > 1400);
            // Persist the current scroll offset so a subsequent
            // navigation to a child route (/user/X, /messages/X, share
            // sheet, etc.) can restore the exact position on return.
            // Keyed per-feud so distinct posts don't collide.
            if (id) scrollMemory.setY(`feud:${id}`, y);
          }}
          scrollEventThrottle={120}
        >
          <ImageBackground source={{ uri: feud.image_url }} style={styles.hero}>
            <LinearGradient colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.9)"]} style={StyleSheet.absoluteFill} />
            {!isAnonymous && (
              <Pressable
                onPress={toggleFavorite}
                testID="favorite-button"
                hitSlop={8}
                style={[styles.favBtn, feud.is_favorite && styles.favBtnActive]}
              >
                <Ionicons
                  name={feud.is_favorite ? "bookmark" : "bookmark-outline"}
                  size={22}
                  color={feud.is_favorite ? colors.onBrandPrimary : "#FFFFFF"}
                />
              </Pressable>
            )}
            <View style={styles.heroContent}>
              <Text style={styles.title}>{feud.title}</Text>
            </View>
          </ImageBackground>

          <View style={styles.article}>
            <View style={styles.articleHeader}>
              <Text style={styles.sectionKicker}>LA FAIDA</Text>
              {feud.context_text ? (
                <Pressable
                  onPress={() => setShowContext((v) => !v)}
                  hitSlop={12}
                  testID="feud-context-toggle"
                  accessibilityLabel={
                    showContext ? "Chiudi il contesto della notizia" : "Mostra il contesto della notizia"
                  }
                  style={styles.contextInfoBtn}
                >
                  <Ionicons
                    name={showContext ? "information-circle" : "information-circle-outline"}
                    size={20}
                    color={showContext ? colors.brandSecondary : colors.brandSecondary}
                  />
                </Pressable>
              ) : null}
            </View>
            {showContext && feud.context_text ? (
              <View style={styles.contextBox} testID="feud-context-panel">
                <Text style={styles.contextKicker}>CONTESTO</Text>
                {feud.context_text
                  .split(/\n{2,}/)
                  .filter((p) => p.trim())
                  .map((para, idx) => (
                    <Text key={idx} style={styles.contextText}>{para.trim()}</Text>
                  ))}
              </View>
            ) : null}
            {(feud.summary || "")
              .split(/\n{2,}/)
              .filter((p) => p.trim())
              .map((para, idx) => (
                <Text key={idx} style={styles.summary}>{para.trim()}</Text>
              ))}
            {feud.hashtag && (
              <Pressable
                onPress={() => router.push(`/hashtag/${feud.hashtag}`)}
                testID="feud-hashtag"
                hitSlop={6}
                style={styles.hashtagPill}
              >
                <Text style={styles.hashtagText} numberOfLines={1}>
                  {feud.hashtag_display || `#${feud.hashtag}`}
                </Text>
              </Pressable>
            )}
          </View>

          {feud.media && (
            <View style={styles.mediaSection} testID="feud-media-section">
              <Text style={styles.sectionKicker}>
                {feud.media.type === "youtube" || feud.media.type === "video" ? "VIDEO" : "IMMAGINE"}
              </Text>
              <FeudMediaBlock
                media={feud.media}
                fallbackImage={feud.image_url}
                title={feud.title}
              />
            </View>
          )}

          {feud.sources && feud.sources.length > 0 && (
            <View style={styles.sourcesBox} testID="sources-box">
              <Pressable
                onPress={() => setSourcesExpanded(!sourcesExpanded)}
                testID="sources-toggle"
                style={styles.sourcesHead}
                hitSlop={6}
              >
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <Ionicons name="newspaper-outline" size={16} color={colors.brandPrimary} />
                  <Text style={styles.sectionKicker}>
                    {feud.sources.length === 1 ? "FONTE" : "FONTI"}
                  </Text>
                  <Text style={styles.sourcesCount}>{feud.sources.length}</Text>
                </View>
                <Ionicons
                  name={sourcesExpanded ? "chevron-up" : "chevron-down"}
                  size={20}
                  color={colors.onSurface}
                />
              </Pressable>
              {sourcesExpanded && (
                <View style={styles.sourcesList} testID="sources-list">
                  {feud.sources.map((s, i) => (
                    <Pressable
                      key={i}
                      style={styles.sourceItem}
                      onPress={() => Linking.openURL(s.link)}
                      testID={`source-${i}`}
                    >
                      <Text style={styles.sourceName}>{s.source.toUpperCase()}</Text>
                      <Text style={styles.sourceTitle} numberOfLines={2}>{s.title}</Text>
                      <Text style={styles.sourceLink}>{s.link.replace(/^https?:\/\//, '').slice(0, 45)}...  ›</Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          )}

          {/* In-feud ad slot — mid-way through the article, right
              before the poll. Same slot the old mock "sponsor" box
              occupied. On web the <AdBanner /> component renders a
              visual placeholder (AdMob has no web SDK); on native
              it renders the real banner (test or prod based on IDs). */}
          <View style={styles.inFeudAdSlot} testID="ad-slot-feud-detail">
            <Text style={styles.inFeudAdLabel}>PUBBLICITÀ</Text>
            <AdBanner placement="feud-detail" />
          </View>

          <View style={styles.pollWrap}>
            <Text style={styles.question}>{feud.question}</Text>
            <View style={styles.pollSplit}>
              <Pressable
                testID="vote-a-button"
                onPress={() => vote("A")}
                disabled={voting || feud.my_vote === "A" || (!!feud.my_vote && (feud.my_vote_changes_left ?? 0) <= 0)}
                style={[styles.pollHalf, { backgroundColor: colors.brandPrimary }, feud.my_vote === "B" && { opacity: 0.35 }]}
              >
                {feud.revealed && <Text style={styles.pollPct}>{feud.pct_a}%</Text>}
                <Text style={styles.pollName}>{feud.party_a}</Text>
                <Text style={styles.pollVotes}>{feud.revealed ? `${feud.votes_a} voti` : "voti nascosti"}</Text>
                {feud.my_vote === "A" && <View style={styles.checkPill}><Ionicons name="checkmark" size={14} color={colors.onBrandPrimary} /></View>}
              </Pressable>
              <Pressable
                testID="vote-b-button"
                onPress={() => vote("B")}
                disabled={voting || feud.my_vote === "B" || (!!feud.my_vote && (feud.my_vote_changes_left ?? 0) <= 0)}
                style={[styles.pollHalf, { backgroundColor: colors.brandSecondary }, feud.my_vote === "A" && { opacity: 0.35 }]}
              >
                {feud.revealed && <Text style={[styles.pollPct, { color: colors.onBrandSecondary }]}>{feud.pct_b}%</Text>}
                <Text style={[styles.pollName, { color: colors.onBrandSecondary }]}>{feud.party_b}</Text>
                <Text style={[styles.pollVotes, { color: colors.onBrandSecondary }]}>{feud.revealed ? `${feud.votes_b} voti` : "voti nascosti"}</Text>
                {feud.my_vote === "B" && <View style={[styles.checkPill, { borderColor: colors.onBrandSecondary }]}><Ionicons name="checkmark" size={14} color={colors.onBrandSecondary} /></View>}
              </Pressable>
            </View>
            {!feud.my_vote && <Text style={styles.pollHint}>Vota per svelare i risultati e sbloccare i commenti.</Text>}
            {feud.my_vote && (
              <Text style={styles.pollHint} testID="vote-change-hint">
                {(feud.my_vote_changes_left ?? 0) > 0
                  ? `Puoi cambiare voto ancora ${feud.my_vote_changes_left} ${feud.my_vote_changes_left === 1 ? "volta" : "volte"} · tocca l'altra fazione`
                  : "Hai esaurito i cambi voto disponibili"}
              </Text>
            )}
            {feud.my_vote && (
              <View style={styles.actionBtnRow}>
                <Pressable
                  onPress={() => setStatsOpen(true)}
                  testID="stats-button"
                  style={styles.statsIconBtn}
                  hitSlop={10}
                  accessibilityLabel="Vedi statistiche dettagliate"
                >
                  <Ionicons name="stats-chart" size={20} color={colors.brandPrimary} />
                </Pressable>
                <Pressable
                  onPress={() => setAiSummaryOpen(true)}
                  testID="ai-summary-button"
                  style={styles.statsIconBtn}
                  hitSlop={10}
                  accessibilityLabel="Sintesi AI degli argomenti delle fazioni"
                >
                  <Ionicons name="sparkles" size={20} color={colors.brandPrimary} />
                </Pressable>
              </View>
            )}
          </View>

          {error && <Text style={styles.err}>{error}</Text>}

          {feud.my_vote && (
            <View style={styles.commentInputWrap}>
              <MentionInput
                inputTestID="comment-input"
                containerStyle={styles.commentInput}
                placeholder="Scrivi il tuo commento... usa @ per taggare"
                placeholderTextColor={colors.muted}
                value={commentText}
                onChangeText={setCommentText}
                feudId={id}
                multiline
              />
              <Pressable
                testID="submit-comment"
                style={[styles.postBtn, { backgroundColor: sideColor(feud.my_vote) }]}
                onPress={submitComment}
                disabled={posting}
              >
                <Text style={[styles.postBtnTxt, { color: onSideColor(feud.my_vote) }]}>
                  {posting ? "..." : "PUBBLICA"}
                </Text>
              </Pressable>
            </View>
          )}

          <View
            style={styles.commentsTabs}
            testID="comments-tabs"
            onLayout={(e) => {
              // Persist the Y position of the comments header — used by the
              // floating scroll-to-top pill so it returns the user right at
              // the start of the comments section (not to the hero image).
              commentsYRef.current = e.nativeEvent.layout.y;
            }}
          >
            <Pressable
              onPress={() => setActiveSide((s) => (s === "A" ? null : "A"))}
              testID="comments-tab-a"
              style={[
                styles.commentsTab,
                { backgroundColor: colors.brandPrimary },
                activeSide !== "A" && styles.commentsTabDim,
              ]}
            >
              {feud.my_vote && (
                <Text style={styles.commentsTabCount}>{sideA.length}</Text>
              )}
              <Text style={styles.commentsTabLabel} numberOfLines={1}>
                PRO {feud.party_a.toUpperCase()}
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setActiveSide((s) => (s === "B" ? null : "B"))}
              testID="comments-tab-b"
              style={[
                styles.commentsTab,
                { backgroundColor: colors.brandSecondary },
                activeSide !== "B" && styles.commentsTabDim,
              ]}
            >
              {feud.my_vote && (
                <Text style={[styles.commentsTabCount, { color: colors.onBrandSecondary }]}>{sideB.length}</Text>
              )}
              <Text style={[styles.commentsTabLabel, { color: colors.onBrandSecondary }]} numberOfLines={1}>
                PRO {feud.party_b.toUpperCase()}
              </Text>
            </Pressable>
          </View>

          {activeSide === null ? (
            <View style={styles.commentsIdle} testID="comments-idle">
              <Ionicons name="chatbubbles-outline" size={44} color={colors.muted} />
              <Text style={styles.commentsIdleTxt}>
                Tocca una fazione per leggere i commenti
              </Text>
            </View>
          ) : (
            <View style={styles.commentsList} testID={`comments-list-${activeSide.toLowerCase()}`}>
              {(activeSide === "A" ? sideA : sideB).length === 0 ? (
                <View style={styles.commentsEmpty}>
                  <Ionicons name="chatbubbles-outline" size={36} color={colors.muted} />
                  <Text style={styles.commentsEmptyTxt}>
                    Ancora nessun commento per {activeSide === "A" ? feud.party_a : feud.party_b}.
                  </Text>
                  {feud.my_vote === activeSide && (
                    <Text style={styles.commentsEmptyHint}>Sii il primo a scrivere!</Text>
                  )}
                </View>
              ) : (
                (activeSide === "A" ? sideA : sideB).map((c) => (
                  <CommentItem
                    key={c.comment_id}
                    c={c}
                    meId={user?.user_id || null}
                    expanded={expanded[c.comment_id]}
                    onToggle={() => toggleReplies(c.comment_id)}
                    onExpand={() => expandReplies(c.comment_id)}
                    replyingTo={replyingTo}
                    setReplyingTo={setReplyingTo}
                    replyText={replyText}
                    setReplyText={setReplyText}
                    onSubmitReply={() => submitReply(c.comment_id)}
                    canReply={!!feud.my_vote}
                    onDeleteComment={() => deleteOwnComment(c.comment_id)}
                    onDeleteReply={(rid) => deleteOwnReply(c.comment_id, rid)}
                  />
                ))
              )}
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
      <ScrollToTopButton
        visible={showTopBtn}
        onPress={() => scrollRef.current?.scrollTo({ y: Math.max(0, commentsYRef.current - 8), animated: true })}
        testID="feud-scroll-top"
      />
      <FeudStatsModal
        visible={statsOpen}
        feudId={feud.feud_id}
        partyA={feud.party_a}
        partyB={feud.party_b}
        onClose={() => setStatsOpen(false)}
      />
      <AiFactionSummaryModal
        visible={aiSummaryOpen}
        feudId={feud.feud_id}
        partyA={feud.party_a}
        partyB={feud.party_b}
        onClose={() => setAiSummaryOpen(false)}
      />
      <ShareSheet
        visible={shareOpen}
        onClose={() => setShareOpen(false)}
        url={buildShareUrl()}
        title={feud.title}
        message={`${feud.title}\n\nCon chi ti schieri? ${feud.party_a} vs ${feud.party_b}`}
        onCopy={copyShareLink}
      />
      <InAppShareSheet
        visible={inAppShareOpen}
        feudId={feud.feud_id}
        feudTitle={feud.title}
        feudCategoryLabel={feud.category_label}
        feudPartyA={feud.party_a}
        feudPartyB={feud.party_b}
        feudImageUrl={feud.image_url}
        onClose={() => setInAppShareOpen(false)}
        onOpenExternal={() => setShareOpen(true)}
      />
    </SafeAreaView>
  );
}

function CommentItem({
  c, meId, expanded, onToggle, onExpand, replyingTo, setReplyingTo, replyText, setReplyText,
  onSubmitReply, canReply, onDeleteComment, onDeleteReply,
}: {
  c: Comment; meId: string | null; expanded?: Reply[]; onToggle: () => void;
  /** Forces the reply thread OPEN (idempotent — no-op if already expanded).
   * Used when the user taps "rispondi" so they can see the conversation
   * history before composing their reply. */
  onExpand: () => void;
  replyingTo: string | null; setReplyingTo: (v: string | null) => void;
  replyText: string; setReplyText: (v: string) => void;
  onSubmitReply: () => void; canReply: boolean;
  onDeleteComment: () => void;
  onDeleteReply: (replyId: string) => void;
}) {
  const router = useRouter();
  const isReplying = replyingTo === c.comment_id;
  const isMine = !!meId && meId === c.user_id;
  const accent = sideColor(c.side as "A" | "B");

  // Local confirmation modal state. Two kinds:
  //  • { kind: "comment" }        → confirm deleting THIS comment
  //  • { kind: "reply", rid: id } → confirm deleting one of its replies
  // Both use the shared themed <ConfirmModal /> so the UI matches the
  // rounded design system used elsewhere in the app (no more grey
  // native browser confirm dialog).
  const [confirm, setConfirm] = useState<
    | { kind: "comment" }
    | { kind: "reply"; rid: string }
    | null
  >(null);

  const confirmDeleteComment = () => setConfirm({ kind: "comment" });
  const confirmDeleteReply = (rid: string) =>
    setConfirm({ kind: "reply", rid });

  return (
    <View style={cs.item} testID={`comment-${c.comment_id}`}>
      <View style={[cs.sideBar, { backgroundColor: accent }]} />
      <View style={cs.body}>
        <View style={cs.headRow}>
          <Pressable
            onPress={() => router.push(`/user/${c.user_id}`)}
            testID={`comment-user-${c.user_id}`}
            hitSlop={6}
          >
            <Text style={[cs.nick, { color: accent }]}>@{c.nickname}</Text>
          </Pressable>
          <View style={cs.headRight}>
            {c.created_at ? (
              <Text style={cs.time} numberOfLines={1}>{formatRelative(c.created_at)}</Text>
            ) : null}
            {isMine ? (
              <Pressable
                onPress={confirmDeleteComment}
                hitSlop={8}
                testID={`comment-delete-${c.comment_id}`}
                style={cs.delBtn}
              >
                <Ionicons name="trash-outline" size={16} color={colors.muted} />
              </Pressable>
            ) : null}
          </View>
        </View>
        <MentionText
          text={c.text}
          mentions={c.mentions}
          style={cs.text}
          accentColor={accent}
        />
        <View style={cs.actions}>
          <Pressable onPress={onToggle} hitSlop={6} testID={`replies-toggle-${c.comment_id}`}>
            <Text style={cs.actionTxt}>
              {expanded
                ? `Nascondi risposte`
                : `${c.reply_count ?? 0} ${(c.reply_count ?? 0) === 1 ? "risposta" : "risposte"}`}
            </Text>
          </Pressable>
          {canReply && (
            <Pressable
              onPress={() => {
                // Toggling: closing the currently-open reply box —
                // discard any unsent draft so opening again starts
                // clean, AND when switching from one comment to
                // another the draft never carries over (user-reported
                // bug: writing a reply to A then tapping "rispondi"
                // on B leaked the A-draft into B's input).
                if (isReplying) {
                  setReplyingTo(null);
                  setReplyText("");
                } else {
                  setReplyingTo(c.comment_id);
                  setReplyText("");
                  // Auto-expand the existing reply thread so the user
                  // can see the conversation history BEFORE composing
                  // their own reply. Previously, replies stayed
                  // collapsed until the user tapped "N risposte" or
                  // submitted their own — the user reported that as
                  // "I only see other replies after I've replied".
                  if (!expanded && (c.reply_count ?? 0) > 0) {
                    onExpand();
                  }
                }
              }}
              testID={`reply-btn-${c.comment_id}`}
              hitSlop={6}
            >
              <Text style={[cs.actionTxt, { color: accent }]}>{isReplying ? "annulla" : "rispondi"}</Text>
            </Pressable>
          )}
        </View>
        {expanded && expanded.length > 0 && (
          <View style={cs.replies}>
            {expanded.map((r) => {
              const rAccent = sideColor(r.side as "A" | "B");
              const replyMine = !!meId && meId === r.user_id;
              return (
                <View key={r.reply_id} style={cs.reply}>
                  <View style={[cs.replySideBar, { backgroundColor: rAccent }]} />
                  <View style={{ flex: 1 }}>
                    <View style={cs.replyHeadRow}>
                      <Pressable onPress={() => router.push(`/user/${r.user_id}`)}>
                        <Text style={[cs.nick, { color: rAccent, fontSize: font.sizes.xs }]}>@{r.nickname}</Text>
                      </Pressable>
                      {replyMine ? (
                        <Pressable
                          onPress={() => confirmDeleteReply(r.reply_id)}
                          hitSlop={8}
                          testID={`reply-delete-${r.reply_id}`}
                        >
                          <Ionicons name="trash-outline" size={14} color={colors.muted} />
                        </Pressable>
                      ) : null}
                    </View>
                    <MentionText
                      text={r.text}
                      mentions={r.mentions}
                      style={[cs.text, { fontSize: font.sizes.sm, marginTop: 2 }]}
                      accentColor={rAccent}
                    />
                  </View>
                </View>
              );
            })}
          </View>
        )}
        {isReplying && (
          <View style={cs.replyInputWrap}>
            <MentionInput
              containerStyle={cs.replyInput}
              value={replyText}
              onChangeText={setReplyText}
              placeholder="Rispondi... usa @ per taggare"
              placeholderTextColor={colors.muted}
              feudId={c.feud_id}
              multiline
              inputTestID={`reply-input-${c.comment_id}`}
            />
            <Pressable onPress={onSubmitReply} style={cs.replySend} testID={`reply-send-${c.comment_id}`}>
              <Text style={cs.replySendTxt}>INVIA</Text>
            </Pressable>
          </View>
        )}
      </View>
      {/* Shared themed confirmation modal — replaces window.confirm /
          Alert.alert with a rounded, on-brand dialog. */}
      <ConfirmModal
        visible={confirm !== null}
        title={confirm?.kind === "reply" ? "Elimina risposta" : "Elimina commento"}
        body={
          confirm?.kind === "reply"
            ? "La risposta verrà eliminata definitivamente."
            : "Il commento e tutte le sue risposte verranno eliminati."
        }
        confirmLabel="ELIMINA"
        cancelLabel="ANNULLA"
        danger
        testID={confirm?.kind === "reply" ? "confirm-delete-reply" : "confirm-delete-comment"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const c2 = confirm;
          setConfirm(null);
          if (c2?.kind === "comment") onDeleteComment();
          else if (c2?.kind === "reply") onDeleteReply(c2.rid);
        }}
      />
    </View>
  );
}

function formatRelative(iso: string): string {
  try {
    const t = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - t);
    const m = Math.floor(diff / 60000);
    if (m < 1) return "ora";
    if (m < 60) return `${m}m fa`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h fa`;
    const d = Math.floor(h / 24);
    if (d < 7) return `${d}g fa`;
    return new Date(iso).toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
  } catch { return ""; }
}

const cs = StyleSheet.create({
  item: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    marginBottom: spacing.sm,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: colors.border,
  },
  sideBar: { width: 4 },
  body: { flex: 1, padding: spacing.md, gap: 4 },
  headRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm },
  headRight: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  delBtn: { padding: 2 },
  replyHeadRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  nick: { fontSize: font.sizes.sm, fontWeight: "700", letterSpacing: 0.3 },
  time: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 0.3 },
  text: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 20, marginTop: 4 },
  actions: { flexDirection: "row", gap: spacing.lg, marginTop: spacing.sm },
  actionTxt: { fontSize: font.sizes.sm, color: colors.muted, letterSpacing: 0.3, fontWeight: "500" },
  replies: { marginTop: spacing.sm, paddingLeft: spacing.sm, borderLeftWidth: 1, borderColor: colors.border, gap: spacing.xs },
  reply: { flexDirection: "row", paddingVertical: spacing.xs, gap: spacing.sm, overflow: "hidden" },
  replySideBar: { width: 3, alignSelf: "stretch", borderRadius: 2 },
  replyInputWrap: { marginTop: spacing.sm, gap: spacing.xs },
  replyInput: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.sm,
    padding: spacing.sm,
    fontSize: font.sizes.sm,
    color: colors.onSurface,
    minHeight: 44,
    backgroundColor: colors.surfaceTertiary,
  },
  replySend: {
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.sm,
    paddingVertical: spacing.xs,
    alignItems: "center",
  },
  replySendTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "800" },
});

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center" },
  topbar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.md, paddingVertical: spacing.md },
  backBtn: { flexDirection: "row", alignItems: "center", gap: 6 },
  backTxt: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1.5, fontWeight: "700" },
  topCat: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "700" },
  hero: { height: 220, justifyContent: "flex-end" },
  heroContent: { padding: spacing.lg },
  favBtn: {
    position: "absolute",
    top: spacing.md,
    right: spacing.md,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.35)",
    borderWidth: 1.5,
    borderColor: "rgba(255,255,255,0.45)",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 3,
  },
  favBtnActive: {
    backgroundColor: colors.brandPrimary,
    borderColor: colors.brandPrimary,
  },
  title: { color: "#FFFFFF", fontSize: font.sizes.xxxl, lineHeight: 38, letterSpacing: 0.3, fontWeight: "800" },
  article: { padding: spacing.lg, backgroundColor: colors.surface },
  hashtagPill: { alignSelf: "flex-start", marginTop: spacing.sm, borderWidth: 1, borderColor: colors.brandPrimary, paddingHorizontal: spacing.sm, paddingVertical: 3, backgroundColor: colors.brandPrimary },
  hashtagText: { fontSize: font.sizes.xs, color: colors.onBrandPrimary, letterSpacing: 0.5, fontWeight: "500" },
  sectionKicker: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.brandPrimary, marginBottom: spacing.xs, fontWeight: "700" },
  articleHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: spacing.xs,
  },
  contextInfoBtn: {
    padding: 4,
    marginLeft: spacing.xs,
    marginBottom: spacing.xs,
    alignItems: "center",
    justifyContent: "center",
  },
  contextBox: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  contextKicker: {
    fontSize: font.sizes.xs,
    letterSpacing: 2,
    color: colors.brandPrimary,
    fontWeight: "600",
    marginBottom: spacing.xs,
  },
  contextText: {
    fontSize: font.sizes.base,
    lineHeight: 20,
    color: colors.onSurface,
    marginBottom: spacing.xs,
    fontStyle: "italic",
  },
  mediaSection: { paddingHorizontal: spacing.lg, marginBottom: spacing.md },
  summary: {
    fontSize: font.sizes.lg,
    lineHeight: 26,
    color: colors.onSurface,
    paddingBottom: spacing.md,
    marginBottom: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  sourcesBox: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, gap: spacing.sm },
  sourcesHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: spacing.xs },
  sourcesCount: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 1, minWidth: 20 },
  sourcesList: { gap: spacing.sm, marginTop: spacing.xs },
  sourceItem: { borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, padding: spacing.sm },
  sourceName: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.brandPrimary },
  sourceTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  sourceLink: { fontSize: font.sizes.xs, color: colors.muted, marginTop: 4 },
  shareBtn: { width: 40, height: 40, borderWidth: 1.5, borderColor: colors.brandSecondary, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  sponsorBox: { padding: spacing.md, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.brandSecondary, gap: spacing.xs },
  sponsorLabel: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.onBrandSecondary, opacity: 0.7 },
  sponsorHeadline: { fontSize: font.sizes.lg, color: colors.onBrandSecondary, lineHeight: 22 },
  sponsorCta: { alignSelf: "flex-start", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse, paddingVertical: spacing.xs, paddingHorizontal: spacing.md, marginTop: spacing.xs },
  sponsorCtaTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  // In-feud ad slot — full-width band sandwiched between the article
  // body and the poll. AdMob policy requires a visible disclosure
  // ("PUBBLICITÀ") directly adjacent to the ad.
  inFeudAdSlot: {
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    paddingVertical: spacing.xs,
    alignItems: "center",
    justifyContent: "center",
  },
  inFeudAdLabel: {
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1.5,
    color: colors.muted,
    marginBottom: 2,
  },
  pollWrap: { padding: spacing.lg, backgroundColor: colors.surface },
  question: { color: colors.onSurface, fontSize: font.sizes.xl, letterSpacing: 0.2, marginBottom: spacing.lg, textAlign: "left", lineHeight: 28, fontWeight: "500" },
  pollSplit: { flexDirection: "row", borderRadius: radius.lg, overflow: "hidden" },
  pollHalf: { flex: 1, paddingVertical: spacing.xl, alignItems: "center", justifyContent: "center", position: "relative" },
  pollPct: { color: colors.onBrandPrimary, fontSize: font.sizes.giant, fontWeight: "800", letterSpacing: 0.5 },
  pollName: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 0.3, marginTop: 4, textAlign: "center", paddingHorizontal: spacing.sm, fontWeight: "600", lineHeight: 20 },
  pollVotes: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, opacity: 0.75, marginTop: 6, fontWeight: "600" },
  checkPill: { position: "absolute", top: 10, right: 10, width: 24, height: 24, borderRadius: 12, borderWidth: 1.5, borderColor: colors.onBrandPrimary, backgroundColor: "transparent", alignItems: "center", justifyContent: "center" },
  pollHint: { color: colors.brandSecondary, fontSize: font.sizes.sm, textAlign: "center", marginTop: spacing.md, letterSpacing: 0.3, fontWeight: "600" },
  statsIconBtn: {
    // Discreet round icon button placed alongside the poll section. Big
    // enough hit target (36×36 + hitSlop) without visual weight.
    width: 44, height: 44, borderRadius: 22,
    borderWidth: 1.5, borderColor: colors.brandPrimary,
    backgroundColor: colors.surface,
    alignItems: "center", justifyContent: "center",
  },
  actionBtnRow: {
    // Horizontal container that holds the stats + AI-summary icon buttons
    // side-by-side, centered under the poll options.
    flexDirection: "row",
    alignSelf: "center",
    marginTop: spacing.md,
    gap: spacing.md,
  },
  err: { color: colors.error, padding: spacing.md, borderWidth: 1.5, borderColor: colors.error, borderRadius: radius.sm, margin: spacing.lg },
  commentInputWrap: { padding: spacing.lg, backgroundColor: colors.surface, gap: spacing.sm },
  commentInput: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    padding: spacing.md,
    minHeight: 60,
    fontSize: font.sizes.base,
    color: colors.onSurface,
    backgroundColor: colors.surfaceSecondary,
  },
  postBtn: { paddingVertical: spacing.md, alignItems: "center", borderRadius: radius.md },
  postBtnTxt: { fontSize: font.sizes.base, letterSpacing: 1.5, fontWeight: "800" },
  commentsTabs: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingBottom: spacing.sm },
  commentsTab: { flex: 1, paddingVertical: spacing.md, paddingHorizontal: spacing.sm, alignItems: "center", justifyContent: "center", gap: 2, position: "relative", overflow: "hidden", borderRadius: radius.md, minHeight: 74 },
  commentsTabDim: { opacity: 0.42 },
  commentsTabCount: { color: colors.onBrandPrimary, fontSize: font.sizes.xxl, fontWeight: "800", letterSpacing: 0.5 },
  commentsTabLabel: { color: colors.onBrandPrimary, fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: spacing.xs, fontWeight: "700" },
  commentsList: { padding: spacing.md, backgroundColor: colors.surface },
  commentsIdle: { alignItems: "center", justifyContent: "center", paddingVertical: spacing.xxxl, gap: spacing.sm, backgroundColor: colors.surface },
  commentsIdleTxt: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 1, textAlign: "center", paddingHorizontal: spacing.xl },
  commentsEmpty: { alignItems: "center", justifyContent: "center", paddingVertical: spacing.xxl, gap: spacing.xs },
  commentsEmptyTxt: { color: colors.muted, fontSize: font.sizes.base, textAlign: "center", paddingHorizontal: spacing.lg, lineHeight: 20 },
  commentsEmptyHint: { color: colors.brandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500" },
  noCmt: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", padding: spacing.md },
  goneBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md },
  goneTitle: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  goneMsg: { color: colors.onSurface, fontSize: font.sizes.lg, textAlign: "center", lineHeight: 24 },
  goneHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 18, marginTop: spacing.xs },
  goneBtn: { marginTop: spacing.lg, paddingVertical: spacing.md, paddingHorizontal: spacing.xl, borderWidth: 2, borderColor: colors.onSurface, backgroundColor: colors.brandPrimary },
  goneBtnTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
});
