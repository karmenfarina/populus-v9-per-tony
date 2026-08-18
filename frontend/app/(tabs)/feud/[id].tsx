/**
 * Populus — Schermata dettaglio faida (`/feud/[id]`).
 * ══════════════════════════════════════════════════════════════════
 *
 * File corposo perché concentra molte responsabilità legate al ciclo
 * di vita di una faida: caricamento dati, voto, commenti nested,
 * risposte, share, admin edit, deep-link a commento specifico.
 *
 * INDICE INTERNO (cerca in file con questi tag):
 * ─────────────────────────────────────────────────────────────────
 *   §1 Parametri route + refs                            (~L33)
 *   §2 Bootstrap effect + focus lifecycle                (~L108)
 *   §3 loadAll (fetch feud + comments + user vote)       (~L209)
 *   §4 Deep-link → scroll & highlight commento           (~L283, §5, §6)
 *      Nota: il flow deep-link è fragile — vedi navNonce +
 *      scrolledToCommentRef + highlightAnim. Non toccare senza
 *      testare tap ripetuti su notifica mention/reply.
 *   §5 Voto (submit, cambio lato)                        (~L402)
 *   §6 Comment submit / reply / delete                   (~L452)
 *   §7 UI: header, ImageBackground, PartyChips           (~L800)
 *   §8 Commenti list + `CommentItem`                     (~L1417)
 *   §9 Styles                                            (~L1721)
 *
 * Comportamento deep-link (dettaglio):
 *   - Se la route ha `?comment=<id>&t=<nonce>`:
 *     • Aprire automaticamente la sezione commenti.
 *     • Scroll animato al commento con highlight giallo (1.2s hold + 1s fade).
 *     • `t` è un nonce che cambia ad ogni tap sulla notifica → forza
 *       il ri-trigger dell'effect anche se l'utente è già sulla pagina.
 * ══════════════════════════════════════════════════════════════════
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  KeyboardAvoidingView, Platform, ImageBackground, Linking, Alert, BackHandler,
  Modal, Animated, Easing,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useLocalSearchParams, useRouter, useFocusEffect, usePathname } from "expo-router";
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
import { navStack } from "@/src/utils/navStack";
import { reviewManager } from "@/src/utils/reviewManager";

export default function FeudDetail() {
  const { id, comment: commentParam, side: sideParam, t: navNonce, from, archiveCat, archiveDate, messagesUserId } =
    useLocalSearchParams<{
      id: string; comment?: string; side?: string; t?: string; from?: string;
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
  // Founder-admin gate. Hardcoded email per spec — do NOT wire this to a
  // generic is_admin flag. Controls the visibility of Edit/Delete buttons
  // in the topbar and the corresponding modals below.
  const ADMIN_EMAIL = "carlofarinapayme@gmail.com";
  const isAdmin = !!user && (user.email || "").trim().toLowerCase() === ADMIN_EMAIL;
  const [adminEditOpen, setAdminEditOpen] = useState(false);
  const [adminConfirmDelete, setAdminConfirmDelete] = useState(false);
  const [adminSaving, setAdminSaving] = useState(false);
  const [adminEditTitle, setAdminEditTitle] = useState("");
  const [adminEditQuestion, setAdminEditQuestion] = useState("");
  const [adminEditCategory, setAdminEditCategory] = useState<string>("");
  const [adminEditSummary, setAdminEditSummary] = useState("");
  const [adminEditPartyA, setAdminEditPartyA] = useState("");
  const [adminEditPartyB, setAdminEditPartyB] = useState("");
  const [adminCategories, setAdminCategories] = useState<{ id: string; label: string }[]>([]);
  // Deep-link target: the specific comment we should scroll to + flash.
  // Keyed by comment_id → the actual View ref (used by measureLayout).
  const commentRefsRef = useRef<Record<string, View | null>>({});
  // The `highlightCommentId` tells the child which row currently owns the
  // fade animation; the `highlightAnim` (0..1) is what actually drives the
  // border/bg opacity so it eases out smoothly instead of blinking off.
  const [highlightCommentId, setHighlightCommentId] = useState<string | null>(null);
  const highlightAnim = useRef(new Animated.Value(0)).current;
  const scrolledToCommentRef = useRef<string | null>(null);
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
    // NOTE: intentionally do NOT wipe scrollMemory here. On web,
    // expo-router unmounts the feud screen when the user taps into a
    // child route (/user/X) and re-mounts it on back, which would fire
    // this effect and destroy the offset we just persisted. The
    // useFocusEffect below reads whatever's currently in memory:
    //   • Fresh visit → memory is 0, ScrollView starts at top anyway.
    //   • Return from child → memory holds the last-scrolled Y and we
    //     restore to it.
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
  const restoreTargetRef = useRef<number>(0);
  const restoreDoneRef = useRef<boolean>(false);
  const pathname = usePathname();
  useFocusEffect(
    useCallback(() => {
      // Register the feud path in the manual navigation stack so that
      // `useSmartBack` (used by /user/[id] and other detail screens)
      // can pop back HERE instead of falling through to the tab root.
      // Without this, tapping @avatar → INDIETRO would go straight to
      // /home instead of returning to this feud — which also nukes any
      // scroll-restore attempt because the feud screen never remounts.
      if (pathname) navStack.push(pathname);
      if (!id) return;
      const y = scrollMemory.getY(`feud:${id}`);
      if (y <= 0) {
        restoreTargetRef.current = 0;
        restoreDoneRef.current = true;
        return;
      }
      restoreTargetRef.current = y;
      restoreDoneRef.current = false;
      const t1 = setTimeout(() => {
        try { scrollRef.current?.scrollTo({ y, animated: false }); } catch { /* noop */ }
      }, 0);
      return () => { clearTimeout(t1); };
    }, [id, pathname]),
  );

  // Second-chance restore once the feud data + comments have loaded.
  // On web the screen is remounted on back-navigation, so the very
  // first scrollTo above runs against an EMPTY ScrollView (contentSize
  // ≈ viewport) and clamps back to 0. This effect fires as soon as
  // loading flips false — the ScrollView is now tall enough to accept
  // the target, so scrolling actually sticks.
  useEffect(() => {
    if (loading) return;
    if (restoreDoneRef.current) return;
    const target = restoreTargetRef.current;
    if (target <= 0) { restoreDoneRef.current = true; return; }
    // Fire twice: once immediately (RN native tends to be ready on the
    // same tick) and once after a short delay to catch web's async
    // layout pass. Marking done after the second attempt keeps this
    // idempotent — subsequent renders won't fight the user's scroll.
    const t1 = setTimeout(() => {
      try { scrollRef.current?.scrollTo({ y: target, animated: false }); } catch { /* noop */ }
    }, 0);
    const t2 = setTimeout(() => {
      try { scrollRef.current?.scrollTo({ y: target, animated: false }); } catch { /* noop */ }
      restoreDoneRef.current = true;
    }, 200);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [loading, sideA.length, sideB.length]);

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
  // user sees the reply without any extra taps. Also scrolls the ScrollView
  // to the target comment (measured via onLayout) and briefly flashes its
  // border so the user immediately spots it in a long thread.
  //
  // Robustness rules:
  //   • The `deepLinkHandledRef` is keyed on the current `commentParam`
  //     value, so tapping a NEW mention notification for the same feud
  //     opens the new comment (previously the ref stayed true once set,
  //     silently ignoring subsequent notifications).
  //   • When we know a commentParam but sideParam is missing AND the
  //     comment hasn't been fetched yet, we still ACTIVATE a side using
  //     the viewer's own vote as a fallback so the comments section
  //     opens immediately — the user's biggest complaint was that the
  //     screen stayed on the "Tocca una fazione per leggere i commenti"
  //     placeholder even after tapping a mention notification.
  // Deep-link from a notification. Two responsibilities:
  //  (a) OPEN the comments section on the correct side whenever a
  //      `?comment=` param is present. This must ALWAYS force the side
  //      open — even if the user had previously closed the comments
  //      by tapping the active tab, or if we're re-arriving from a
  //      second notification tap. The earlier version cached a "handled"
  //      ref and returned early once fired, which meant a re-open of
  //      the same comment silently did nothing.
  //  (b) EXPAND that comment's reply thread once, so a mention buried
  //      inside a reply is visible without any extra tap.
  const repliesExpandedForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!feud || !commentParam) return;

    const cA = sideA.find((c) => c.comment_id === commentParam);
    const cB = sideB.find((c) => c.comment_id === commentParam);
    let target: "A" | "B" | null = null;
    let confirmed = false;
    if (cA) { target = "A"; confirmed = true; }
    else if (cB) { target = "B"; confirmed = true; }
    else if (sideParam === "A" || sideParam === "B") {
      target = sideParam as "A" | "B"; confirmed = true;
    } else if (feud.my_vote === "A" || feud.my_vote === "B") {
      // Soft fallback: keep the effect open so a later sideA/sideB
      // update can refine the side to the CONFIRMED one.
      target = feud.my_vote;
    } else {
      target = "A";
    }

    // Only bump activeSide if it's null OR pointing at the wrong side.
    // Re-tapping the same notification with a still-open section keeps
    // the section open (avoids the visible flicker of state churn).
    setActiveSide((cur) => {
      if (cur === target) return cur;
      // If cur is set to a DIFFERENT side and the target is CONFIRMED,
      // move to the confirmed side. If unconfirmed (soft fallback) and
      // the user already has a side open, don't yank them around.
      if (cur && !confirmed) return cur;
      return target;
    });

    if (confirmed && repliesExpandedForRef.current !== commentParam) {
      repliesExpandedForRef.current = commentParam as string;
      // Auto-expand replies to reveal the notification's context. Silent
      // failure is fine — the target may be a top-level comment with no
      // replies yet.
      toggleReplies(commentParam).catch(() => { /* silent */ });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feud, sideA, sideB, commentParam, sideParam]);

  // Once the side tab is set AND the target comment has been mounted
  // (ref registered via <CommentItem onRef>), scroll to it precisely
  // using measureLayout(ScrollView) so the target ends near the top
  // of the visible area regardless of how deep it sits in the list.
  //
  // Re-triggering: `scrolledToCommentRef` is keyed on `commentParam +
  // navNonce`. Every tap on a notification carries a fresh `t=` nonce
  // (see notifications.tsx), so the same comment scrolls into view
  // EVERY time the user taps its notification — not just the first.
  useEffect(() => {
    if (!commentParam || activeSide === null) return;
    const scrollKey = `${commentParam}::${navNonce || ""}`;
    if (scrolledToCommentRef.current === scrollKey) return;
    let cancelled = false;
    let tries = 0;
    const tryScroll = () => {
      if (cancelled) return;
      const node = commentRefsRef.current[commentParam as string];
      const scrollNode = scrollRef.current as any;
      if (node && scrollNode) {
        // Some RN versions expose the inner scrollable via getInnerViewNode
        // or getNode; measureLayout can accept either. We try the inner
        // node first for accuracy, then fall back to the top-level ref.
        const targetHandle =
          (scrollNode.getInnerViewNode && scrollNode.getInnerViewNode()) ||
          (scrollNode.getScrollableNode && scrollNode.getScrollableNode()) ||
          scrollNode;
        try {
          (node as any).measureLayout(
            targetHandle,
            (_x: number, y: number) => {
              if (cancelled) return;
              scrolledToCommentRef.current = scrollKey;
              try {
                // -24px so the target isn't glued to the very top edge.
                scrollRef.current?.scrollTo({ y: Math.max(0, y - 24), animated: true });
              } catch { /* noop */ }
              // Kick off the fade animation: instantly bump the tint to
              // full brightness, hold briefly, then ease it back to 0 so
              // the border/background dissolve smoothly.
              setHighlightCommentId(commentParam as string);
              highlightAnim.setValue(1);
              // Shorter, punchier highlight per user feedback: 1.2s hold
              // then 1s ease-out (total 2.2s) — enough to catch the eye
              // without lingering on the comment.
              Animated.sequence([
                Animated.delay(1200),
                Animated.timing(highlightAnim, {
                  toValue: 0,
                  duration: 1000,
                  easing: Easing.out(Easing.cubic),
                  // border colors + backgroundColor aren't supported by the
                  // native driver — keep JS-driven so the interpolation
                  // works everywhere (web + native).
                  useNativeDriver: false,
                }),
              ]).start(({ finished }) => {
                if (finished) {
                  setHighlightCommentId((cur) => (cur === commentParam ? null : cur));
                }
              });
            },
            () => {
              // measureLayout failed — retry a couple of times before giving up.
              if (tries++ < 15) setTimeout(tryScroll, 120);
            }
          );
          return;
        } catch {
          // fall through to retry
        }
      }
      if (tries++ < 15) {
        setTimeout(tryScroll, 120);
      }
    };
    // First attempt after a beat so the side flip has time to reflow.
    setTimeout(tryScroll, 150);
    return () => { cancelled = true; };
  }, [commentParam, activeSide, sideA, sideB]);

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
      // Record a meaningful engagement for the store-review gate — a
      // vote counts as one action. `maybePrompt` will silently no-op
      // until every gate (sessions ≥3, actions ≥5, days ≥3, cooldown)
      // is passed, so calling it here is safe.
      reviewManager.recordAction("vote")
        .then(() => reviewManager.maybePrompt())
        .catch(() => { /* noop */ });
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
      reviewManager.recordAction("comment")
        .then(() => reviewManager.maybePrompt())
        .catch(() => { /* noop */ });
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
      reviewManager.recordAction("reply")
        .then(() => reviewManager.maybePrompt())
        .catch(() => { /* noop */ });
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

  // ── Founder-admin: open the edit modal pre-filled with current values ──
  const openAdminEdit = async () => {
    if (!feud) return;
    setAdminEditTitle(feud.title || "");
    setAdminEditQuestion(feud.question || "");
    setAdminEditCategory(feud.category || "");
    setAdminEditSummary(feud.summary || "");
    setAdminEditPartyA(feud.party_a || "");
    setAdminEditPartyB(feud.party_b || "");
    setAdminEditOpen(true);
    // Load the category whitelist lazily the first time the admin opens
    // the modal — same source of truth the backend PATCH validates against.
    if (adminCategories.length === 0) {
      try {
        const c = await api.categories();
        setAdminCategories(c.categories || []);
      } catch { /* silent */ }
    }
  };

  const submitAdminEdit = async () => {
    if (!feud) return;
    const title = adminEditTitle.trim();
    const question = adminEditQuestion.trim();
    const summary = adminEditSummary.trim();
    const partyA = adminEditPartyA.trim();
    const partyB = adminEditPartyB.trim();
    if (!title) { setError("Il titolo non può essere vuoto"); return; }
    if (!question) { setError("La domanda non può essere vuota"); return; }
    if (!summary) { setError("Il testo non può essere vuoto"); return; }
    if (!partyA) { setError("La fazione A non può essere vuota"); return; }
    if (!partyB) { setError("La fazione B non può essere vuota"); return; }
    setAdminSaving(true);
    try {
      const res = await api.adminEditFeud(feud.feud_id, {
        title,
        question,
        category: adminEditCategory || undefined,
        summary,
        party_a: partyA,
        party_b: partyB,
      });
      setFeud(res.feud);
      setAdminEditOpen(false);
    } catch (e: any) {
      setError(e?.detail || e?.message || "Impossibile aggiornare la faida");
    } finally {
      setAdminSaving(false);
    }
  };

  const submitAdminHide = async () => {
    if (!feud) return;
    setAdminSaving(true);
    try {
      await api.adminHideFeud(feud.feud_id);
      setAdminConfirmDelete(false);
      // Feed the admin a clear confirmation, then send them home.
      // Non-blocking Alert; on web we just go straight back.
      try {
        Alert.alert("Faida nascosta", "La faida è stata rimossa dai feed. Puoi ripristinarla dalla lista faide nascoste in Admin.");
      } catch { /* noop */ }
      goBack();
    } catch (e: any) {
      setError(e?.detail || e?.message || "Impossibile nascondere la faida");
    } finally {
      setAdminSaving(false);
    }
  };

  const submitAdminRestore = async () => {
    if (!feud) return;
    setAdminSaving(true);
    try {
      const res = await api.adminRestoreFeud(feud.feud_id);
      // Reload the feud so `is_hidden` flips back to false in the UI.
      try {
        const fresh = await api.feud(feud.feud_id);
        setFeud(fresh.feud);
      } catch {
        setFeud({ ...feud, is_hidden: false });
      }
      void res;
    } catch (e: any) {
      setError(e?.detail || e?.message || "Impossibile ripristinare la faida");
    } finally {
      setAdminSaving(false);
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
            <Ionicons name="arrow-back" size={16} color={colors.onBrandSecondary} />
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
          {isAdmin && (
            <>
              <Pressable
                onPress={openAdminEdit}
                testID="admin-edit-button"
                hitSlop={8}
                style={styles.adminBtn}
              >
                <Ionicons name="create-outline" size={18} color={colors.brandSecondary} />
              </Pressable>
              {feud.is_hidden ? (
                <Pressable
                  onPress={submitAdminRestore}
                  testID="admin-restore-button"
                  hitSlop={8}
                  disabled={adminSaving}
                  style={styles.adminBtn}
                >
                  <Ionicons name="refresh-outline" size={18} color={colors.brandSecondary} />
                </Pressable>
              ) : (
                <Pressable
                  onPress={() => setAdminConfirmDelete(true)}
                  testID="admin-delete-button"
                  hitSlop={8}
                  style={styles.adminBtn}
                >
                  <Ionicons name="trash-outline" size={18} color={colors.brandPrimary} />
                </Pressable>
              )}
            </>
          )}
          <Text style={styles.topCat}>{(feud.category_label || feud.category || "").toString().toUpperCase()}</Text>
          <Pressable onPress={onShare} testID="share-button" style={styles.shareBtn}>
            <Ionicons name="share-outline" size={18} color={colors.brandSecondary} />
          </Pressable>
        </View>
      </View>
      {isAdmin && feud.is_hidden && (
        <View style={styles.hiddenBanner} testID="admin-hidden-banner">
          <Ionicons name="eye-off-outline" size={16} color={colors.brandPrimary} />
          <Text style={styles.hiddenBannerTxt}>
            FAIDA NASCOSTA — visibile solo a te. Tocca l'icona ↻ per ripristinare.
          </Text>
        </View>
      )}

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
                      <Text style={styles.sourceName}>{(s.source || "").toString().toUpperCase()}</Text>
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
                PRO {(feud.party_a || "").toString().toUpperCase()}
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
                PRO {(feud.party_b || "").toString().toUpperCase()}
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
                    onRegisterRef={(node) => {
                      // Store/clear the ref so the deep-link scroll effect
                      // above can call measureLayout on the exact row.
                      if (node) commentRefsRef.current[c.comment_id] = node;
                      else delete commentRefsRef.current[c.comment_id];
                    }}
                    highlighted={highlightCommentId === c.comment_id}
                    highlightAnim={highlightCommentId === c.comment_id ? highlightAnim : null}
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

      {/* ── Founder-admin: edit modal ─────────────────────────── */}
      <Modal
        visible={isAdmin && adminEditOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setAdminEditOpen(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.adminModalOverlay}
        >
          <View style={styles.adminModalCard} testID="admin-edit-modal">
            <Text style={styles.adminModalTitle}>MODIFICA FAIDA</Text>
            <Text style={styles.adminModalHint}>
              Visibile solo all'admin. Le modifiche sono immediate per tutti gli utenti.
            </Text>

            <ScrollView
              style={styles.adminModalScroll}
              contentContainerStyle={{ paddingBottom: spacing.sm }}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator
            >
              <Text style={styles.adminFieldLabel}>TITOLO</Text>
              <TextInput
                value={adminEditTitle}
                onChangeText={setAdminEditTitle}
                placeholder="Titolo della faida"
                placeholderTextColor={colors.muted}
                style={styles.adminInput}
                testID="admin-edit-title"
                multiline
              />

              <Text style={styles.adminFieldLabel}>DOMANDA</Text>
              <TextInput
                value={adminEditQuestion}
                onChangeText={setAdminEditQuestion}
                placeholder="Domanda del sondaggio"
                placeholderTextColor={colors.muted}
                style={styles.adminInput}
                testID="admin-edit-question"
                multiline
              />

              <Text style={styles.adminFieldLabel}>FAZIONE A</Text>
              <TextInput
                value={adminEditPartyA}
                onChangeText={setAdminEditPartyA}
                placeholder="Nome fazione A"
                placeholderTextColor={colors.muted}
                style={styles.adminInput}
                testID="admin-edit-party-a"
              />

              <Text style={styles.adminFieldLabel}>FAZIONE B</Text>
              <TextInput
                value={adminEditPartyB}
                onChangeText={setAdminEditPartyB}
                placeholder="Nome fazione B"
                placeholderTextColor={colors.muted}
                style={styles.adminInput}
                testID="admin-edit-party-b"
              />

              <Text style={styles.adminFieldLabel}>TESTO ARTICOLO</Text>
              <TextInput
                value={adminEditSummary}
                onChangeText={setAdminEditSummary}
                placeholder="Testo/riassunto dell'articolo"
                placeholderTextColor={colors.muted}
                style={[styles.adminInput, styles.adminInputTall]}
                testID="admin-edit-summary"
                multiline
                textAlignVertical="top"
              />

              <Text style={styles.adminFieldLabel}>CATEGORIA</Text>
              <View style={styles.adminCatWrap}>
                {(adminCategories.length ? adminCategories : [{ id: feud.category, label: feud.category_label }]).map((cat) => {
                  const active = adminEditCategory === cat.id;
                  return (
                    <Pressable
                      key={cat.id}
                      onPress={() => setAdminEditCategory(cat.id)}
                      testID={`admin-edit-cat-${cat.id}`}
                      style={[styles.adminCatChip, active && styles.adminCatChipActive]}
                    >
                      <Text style={[styles.adminCatChipTxt, active && styles.adminCatChipTxtActive]}>
                        {cat.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </ScrollView>

            <View style={styles.adminModalActions}>
              <Pressable
                onPress={() => setAdminEditOpen(false)}
                style={[styles.adminModalBtn, styles.adminModalBtnGhost]}
                testID="admin-edit-cancel"
                disabled={adminSaving}
              >
                <Text style={styles.adminModalBtnGhostTxt}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={submitAdminEdit}
                style={[styles.adminModalBtn, styles.adminModalBtnPrimary]}
                testID="admin-edit-save"
                disabled={adminSaving}
              >
                <Text style={styles.adminModalBtnPrimaryTxt}>
                  {adminSaving ? "SALVATAGGIO..." : "SALVA"}
                </Text>
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* ── Founder-admin: soft-delete confirmation modal ─────── */}
      <ConfirmModal
        visible={isAdmin && adminConfirmDelete}
        title="Nascondi faida"
        body="La faida verrà rimossa da tutti i feed. Potrai ripristinarla in qualsiasi momento dalla lista faide nascoste in Admin."
        confirmLabel="NASCONDI"
        cancelLabel="ANNULLA"
        danger
        testID="admin-confirm-delete"
        onCancel={() => setAdminConfirmDelete(false)}
        onConfirm={submitAdminHide}
      />
    </SafeAreaView>
  );
}

function CommentItem({
  c, meId, expanded, onToggle, onExpand, replyingTo, setReplyingTo, replyText, setReplyText,
  onSubmitReply, canReply, onDeleteComment, onDeleteReply, onRegisterRef, highlighted, highlightAnim,
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
  /** Registers/unregisters this row's outer View so the parent's deep-link
   *  effect can measure and scroll to it. */
  onRegisterRef?: (node: View | null) => void;
  /** When true, apply a highlight border (used to draw the user's eye to
   *  the comment they were deep-linked to). The actual opacity is driven
   *  by `highlightAnim` (0..1) so the effect fades out smoothly. */
  highlighted?: boolean;
  highlightAnim?: Animated.Value | null;
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
    <View
      ref={onRegisterRef}
      style={cs.item}
      testID={`comment-${c.comment_id}`}
    >
      {/* Deep-link highlight overlay — a full-cover Animated.View that
          eases its border + tint from 1 → 0 so the yellow accent
          dissolves smoothly instead of blinking off. */}
      {highlighted && highlightAnim && (
        <Animated.View
          pointerEvents="none"
          style={[
            cs.highlightOverlay,
            {
              opacity: highlightAnim,
              // Bumped from 0.14 → 0.28 so the yellow tint reads as a
              // proper highlight instead of a subtle tint. Combined with
              // the 3px yellow border on `.highlightOverlay`, the deep-
              // linked comment now visibly pops for the full 3s hold.
              backgroundColor: highlightAnim.interpolate({
                inputRange: [0, 1],
                outputRange: ["rgba(255,216,20,0)", "rgba(255,216,20,0.28)"],
              }),
            },
          ]}
        />
      )}
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
  itemHighlighted: {
    // Deprecated (kept as fallback). Actual highlight is now an animated
    // overlay child (see `highlightOverlay`) so the yellow accent can
    // ease out gracefully instead of snapping off.
    borderWidth: 2,
    borderColor: colors.brandSecondary,
    backgroundColor: "rgba(255, 216, 20, 0.10)",
  },
  highlightOverlay: {
    // Absolute-positioned tint that sits above the base comment card and
    // is faded via the parent's Animated.Value. `borderRadius` matches
    // the card so the fade tint clips cleanly at the rounded corners.
    // Thick yellow border + bright semi-transparent fill make the
    // deep-link target immediately visible to a user tracking down
    // a mention notification in a long thread.
    ...StyleSheet.absoluteFillObject,
    borderRadius: radius.md,
    borderWidth: 3,
    borderColor: "#FFD814",
    zIndex: 5,
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
  // Founder-admin controls (edit/delete/restore) live inside the topbar.
  adminBtn: {
    padding: 6,
    borderWidth: 1,
    borderColor: colors.brandSecondary,
    borderRadius: radius.sm,
  },
  hiddenBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: "rgba(255,69,58,0.15)",
    borderBottomWidth: 1,
    borderColor: colors.brandPrimary,
  },
  hiddenBannerTxt: {
    flex: 1,
    color: colors.brandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  adminModalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.65)",
    padding: spacing.lg,
    justifyContent: "center",
  },
  adminModalCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    gap: spacing.sm,
    maxHeight: "90%",
  },
  adminModalScroll: {
    // Let the inner form scroll within the modal instead of getting cut off
    // when the admin edits the long article text on smaller screens.
    maxHeight: "78%",
  },
  adminModalTitle: {
    color: colors.brandSecondary,
    fontSize: font.sizes.lg,
    fontWeight: "800",
    letterSpacing: 1.5,
  },
  adminModalHint: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    lineHeight: 16,
    marginBottom: spacing.sm,
  },
  adminFieldLabel: {
    color: colors.brandPrimary,
    fontSize: font.sizes.xs,
    letterSpacing: 1.5,
    fontWeight: "700",
    marginTop: spacing.xs,
  },
  adminInput: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.sm,
    padding: spacing.sm,
    fontSize: font.sizes.base,
    color: colors.onSurface,
    minHeight: 44,
    textAlignVertical: "top",
    backgroundColor: colors.surfaceSecondary,
  },
  adminInputTall: {
    // The article-summary textarea gets extra room since it hosts several
    // paragraphs of body copy.
    minHeight: 140,
    maxHeight: 260,
  },
  adminCatWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  adminCatChip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  adminCatChipActive: {
    borderColor: colors.brandSecondary,
    backgroundColor: colors.brandSecondary,
  },
  adminCatChipTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "600",
    letterSpacing: 0.3,
  },
  adminCatChipTxtActive: {
    color: colors.onBrandSecondary,
    fontWeight: "800",
  },
  adminModalActions: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  adminModalBtn: {
    flex: 1,
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
  },
  adminModalBtnGhost: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: "transparent",
  },
  adminModalBtnGhostTxt: {
    color: colors.onSurface,
    fontWeight: "700",
    letterSpacing: 1,
    fontSize: font.sizes.sm,
  },
  adminModalBtnPrimary: {
    backgroundColor: colors.brandSecondary,
  },
  adminModalBtnPrimaryTxt: {
    color: colors.onBrandSecondary,
    fontWeight: "800",
    letterSpacing: 1,
    fontSize: font.sizes.sm,
  },
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
  goneBtn: {
    // Modern CTA styling — matches the yellow-pill primary buttons used
    // throughout the app (feed empty state, unlock badges, etc.) instead
    // of the older red-with-white-border look.
    marginTop: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.sm + 2,
    paddingHorizontal: spacing.xl,
    borderRadius: radius.pill,
    backgroundColor: colors.brandSecondary,
    minHeight: 48,
  },
  goneBtnTxt: {
    color: colors.onBrandSecondary,
    fontSize: font.sizes.sm,
    letterSpacing: 1.5,
    fontWeight: "800",
  },
});
