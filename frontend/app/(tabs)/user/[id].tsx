import { useEffect, useRef, useState, useCallback } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Image, Linking, Modal, TextInput, Alert,
} from "react-native";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, PublicUser, HistoryItem } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, spacing, font, sideColor, radius } from "@/src/theme";
import { useSmartBack } from "@/src/utils/useSmartBack";
import { scrollMemory } from "@/src/utils/scrollMemory";
import { PhotoGalleryViewer } from "@/src/components/PhotoGalleryViewer";
import CategoryBadgesModal from "@/src/components/CategoryBadgesModal";

const SOCIAL_ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  instagram: "logo-instagram",
  tiktok: "musical-notes",
  twitter: "logo-twitter",
  youtube: "logo-youtube",
  website: "globe-outline",
};
const SOCIAL_LABELS: Record<string, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
  twitter: "X (Twitter)",
  youtube: "YouTube",
  website: "Sito",
};

type HFilter = "all" | "majority" | "minority";

export default function UserPublicScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const goBack = useSmartBack("/");
  const { user: me } = useAuth();
  // Cross-mount scroll memory — keyed by the profile's user_id so
  // multiple public profiles visited in the same session don't
  // clobber each other's offsets. Same mechanism as the Own Profile
  // page: survives tab-navigator remounts while jumping to /feud/[id].
  const scrollRef = useRef<ScrollView>(null);
  const pendingScrollYRef = useRef<number | null>(null);
  // Set to `true` the moment the user starts dragging the ScrollView.
  // Any pending scroll-restore timer will bail out to avoid fighting
  // manual scroll — that was the "can't scroll for 1 second after
  // returning to the profile" bug.
  const userInteractedRef = useRef(false);
  const scrollKey = `user-profile:${id || 'unknown'}`;
  const [profile, setProfile] = useState<PublicUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const [viewerOpen, setViewerOpen] = useState(false);
  // Per-filter cache to avoid the loading flash when flipping between
  // all/majority/minority sub-tabs. Same pattern as the own-profile
  // screen — the previous rows stay visible while a silent background
  // refresh runs (only if the cache is older than the TTL).
  const [filter, setFilter] = useState<HFilter>("all");
  const [historyCache, setHistoryCache] = useState<Record<HFilter, HistoryItem[]>>(
    {} as Record<HFilter, HistoryItem[]>,
  );
  const historyLoadedAtRef = useRef<Record<HFilter, number>>({} as Record<HFilter, number>);
  const HISTORY_CACHE_TTL_MS = 60_000;
  const history = historyCache[filter] || [];
  const [refreshingH, setRefreshingH] = useState(false);
  const loadingH = refreshingH && !historyCache[filter];
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportText, setReportText] = useState("");
  const [isBlocked, setIsBlocked] = useState(false);
  const [inCircle, setInCircle] = useState(false);
  const [circleCount, setCircleCount] = useState(0);
  const [circleWorking, setCircleWorking] = useState(false);
  // Category badges collection modal — shared with the owner's own
  // profile screen so third parties see exactly the same shelf.
  const [badgesOpen, setBadgesOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        const r = await api.publicUser(id);
        setProfile(r);
        const photos = r.photos || [];
        const pIdx = photos.findIndex((p: any) => p.photo_id === r.primary_photo_id);
        setIdx(pIdx >= 0 ? pIdx : 0);
      } catch (e: any) { setError(e?.message || "Errore"); }
      finally { setLoading(false); }
    })();
  }, [id]);

  // Load block state for the current logged-in registered user.
  useEffect(() => {
    if (!id || !me || me.is_anonymous || me.user_id === id) return;
    (async () => {
      try {
        const r = await api.myBlocks();
        const blocked = (r?.blocked_users || []).some((u: any) => u.user_id === id);
        setIsBlocked(blocked);
      } catch { /* silent */ }
    })();
  }, [id, me]);

  // Circle status — whether target is in MY circle, plus the target's own
  // circle count (shown as a chip whether or not I'm the owner).
  // Wrapped in useFocusEffect so returning to this screen after removing
  // the target elsewhere (own circle, someone else's circle) always
  // refreshes the "AGGIUNGI/NELLA CERCHIA" toggle to the correct state.
  useFocusEffect(
    useCallback(() => {
      if (!id) return;
      let cancelled = false;
      (async () => {
        try {
          const c = await api.circleGet(id);
          if (!cancelled) setCircleCount(c?.count ?? 0);
        } catch { /* silent */ }
        if (me && !me.is_anonymous && me.user_id !== id) {
          try {
            const s = await api.circleStatus(id);
            if (!cancelled) setInCircle(!!s?.in_circle);
          } catch { /* silent */ }
        }
      })();
      return () => { cancelled = true; };
    }, [id, me]),
  );

  const toggleCircle = async () => {
    if (!id || circleWorking) return;
    setCircleWorking(true);
    try {
      if (inCircle) {
        await api.circleRemove(id);
        setInCircle(false);
      } else {
        await api.circleAdd(id);
        setInCircle(true);
      }
    } catch (e: any) {
      Alert.alert(inCircle ? "Errore" : "Impossibile aggiungere", e?.detail || "Riprova");
    } finally { setCircleWorking(false); }
  };

  const canMessage = !!me && !me.is_anonymous && me.user_id !== id && !profile?.is_anonymous;

  const openChat = () => {
    if (!id) return;
    router.push({ pathname: "/messages/[userId]", params: { userId: id, from: `/user/${id}` } });
  };

  const toggleBlock = async () => {
    setMenuOpen(false);
    if (!id) return;
    if (isBlocked) {
      try {
        await api.unblockUser(id);
        setIsBlocked(false);
      } catch (e: any) {
        Alert.alert("Errore", e?.detail || "Impossibile sbloccare");
      }
      return;
    }
    Alert.alert("Blocca utente", `Vuoi bloccare @${profile?.nickname}? Non riceverai più messaggi.`, [
      { text: "Annulla", style: "cancel" },
      {
        text: "Blocca",
        style: "destructive",
        onPress: async () => {
          try {
            await api.blockUser(id);
            setIsBlocked(true);
          } catch (e: any) {
            Alert.alert("Errore", e?.detail || "Impossibile bloccare");
          }
        },
      },
    ]);
  };

  const submitReport = async () => {
    if (!id) return;
    const reason = reportText.trim();
    if (reason.length < 2) {
      Alert.alert("Segnalazione", "Descrivi brevemente il motivo (min 2 caratteri).");
      return;
    }
    try {
      await api.reportUser(id, reason);
      setReportOpen(false);
      setReportText("");
      Alert.alert("Grazie", "La segnalazione è stata inviata al team di moderazione.");
    } catch (e: any) {
      Alert.alert("Errore", e?.detail || "Impossibile inviare la segnalazione");
    }
  };

  const [historyHidden, setHistoryHidden] = useState<null | "private" | "mutual_private" | "anonymous">(null);

  const loadHistory = useCallback(async (uid: string, f: HFilter, opts?: { force?: boolean }) => {
    const now = Date.now();
    const lastLoaded = historyLoadedAtRef.current[f] || 0;
    const isFresh = now - lastLoaded < HISTORY_CACHE_TTL_MS;
    // Skip network call + spinner if this filter's cache is still fresh.
    // The user just flipped tabs — no need to hit the backend.
    if (isFresh && !opts?.force) return;
    setRefreshingH(true);
    try {
      const r: any = await api.publicUserHistory(uid, f);
      setHistoryCache((prev) => ({ ...prev, [f]: r.history || [] }));
      historyLoadedAtRef.current[f] = Date.now();
      // The backend surfaces a `hidden` flag + reason when the owner has
      // opted out of showing their voting history to this viewer. Store it
      // so the UI can render an informative empty state instead of a bare
      // "no votes" message.
      if (r.hidden) setHistoryHidden(r.reason || "private");
      else setHistoryHidden(null);
    } catch {
      setHistoryCache((prev) => ({ ...prev, [f]: [] }));
      historyLoadedAtRef.current[f] = Date.now();
      setHistoryHidden(null);
    }
    finally { setRefreshingH(false); }
  }, []);

  useEffect(() => {
    if (!id) return;
    // Skip loading history for anonymous accounts or when the section is
    // collapsed — avoid unnecessary requests + loader flicker.
    if (profile?.is_anonymous) return;
    if (!historyExpanded) return;
    loadHistory(id, filter);
  }, [id, filter, loadHistory, profile?.is_anonymous, historyExpanded]);

  // On focus (e.g. returning from a feud where the viewer may have voted,
  // or coming back to this profile after any action) do a **silent
  // background refresh** of the currently-selected filter and invalidate
  // the cache timestamps for the other filters so tapping them re-fetches
  // as well. The previously-cached rows stay visible during the refetch
  // (no spinner) because `loadingH` only fires when the cache is empty.
  useFocusEffect(
    useCallback(() => {
      if (!id) return;
      if (profile?.is_anonymous) return;
      if (!historyExpanded) return;
      // Invalidate every filter's freshness so subsequent tab clicks also
      // hit the network — but keep the cached data so no loader shows.
      historyLoadedAtRef.current = {} as Record<HFilter, number>;
      loadHistory(id, filter, { force: true });
    }, [id, profile?.is_anonymous, historyExpanded, filter, loadHistory]),
  );

  // Note: we previously auto-refreshed this public history every 30s
  // to keep MAGGIORANZA/MINORANZA labels in real-time sync. That
  // silently reset the scroll position while the observer was still
  // reading — jarring UX. The `useSmartBack`-driven re-focus of this
  // screen already covers the update case when the viewer navigates
  // elsewhere and back.

  // Scroll restoration when returning from a detail screen (e.g. the
  // viewer tapped a feud in this user's history and hit back). Same
  // pattern as the Own Profile page — retry loop + content-size
  // observer to survive late layout changes. Keyed by user_id via
  // `scrollKey` so viewing multiple users doesn't cross-contaminate.
  useFocusEffect(
    useCallback(() => {
      const y = scrollMemory.getY(scrollKey);
      const shouldRestore = scrollMemory.consumeRestore(scrollKey);
      // Reset the "user has touched the list" flag at every focus so a
      // fresh restore attempt is possible when the user re-enters the
      // profile.
      userInteractedRef.current = false;
      if (shouldRestore && y > 0) {
        pendingScrollYRef.current = y;
        // Two lightweight attempts are enough in practice: one right
        // after focus and one after the initial paint. The old 6-timer
        // scheme (0…1200ms) kept firing scrollTo for over a second,
        // which fought the user if they tried to scroll manually in
        // that window — exactly the bug reported.
        const attempts = [0, 80];
        const timers: any[] = [];
        attempts.forEach((ms) => {
          timers.push(setTimeout(() => {
            if (userInteractedRef.current) return; // user is dragging, abort
            const target = pendingScrollYRef.current;
            if (target != null) {
              scrollRef.current?.scrollTo({ y: target, animated: false });
            }
          }, ms));
        });
        // Safety clear of the pending target after a short window so
        // `onContentSizeChange` fallbacks don't fire much later.
        timers.push(setTimeout(() => { pendingScrollYRef.current = null; }, 350));
        return () => { timers.forEach((t) => clearTimeout(t)); };
      }
      // Fresh entry into this profile → no explicit restoration needed.
      // Reset the stored offset so a later navigation to a different
      // user's profile doesn't accidentally inherit our value.
      pendingScrollYRef.current = null;
    }, [scrollKey]),
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}><ActivityIndicator size="large" color={colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }
  if (error || !profile) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}><Text style={styles.err}>{error || "Utente non trovato"}</Text></View>
      </SafeAreaView>
    );
  }

  const photos = profile.photos || [];
  const hasPhotos = photos.length > 0;
  const current = hasPhotos ? photos[idx] : null;
  const socials = profile.social_links || {};
  const socialEntries = Object.entries(socials).filter(([, v]) => v && String(v).trim().length > 0);

  const prev = () => setIdx((i) => (i > 0 ? i - 1 : photos.length - 1));
  const next = () => setIdx((i) => (i < photos.length - 1 ? i + 1 : 0));

  // Anonymous user profile — minimal card, no photos/history/socials/badge.
  if (profile.is_anonymous) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="public-user-screen">
        <View style={styles.topbar}>
          <Pressable onPress={goBack} testID="user-back" style={styles.backBtn}>
            <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
            <Text style={styles.backTxt}>INDIETRO</Text>
          </Pressable>
          <Text style={styles.topNick}>@{profile.nickname}</Text>
        </View>
        <View style={styles.anonBox} testID="public-anonymous">
          <View style={styles.anonAvatar}>
            <Ionicons name="glasses-outline" size={80} color={colors.brandSecondary} />
          </View>
          <Text style={styles.anonTitle}>UTENTE ANONIMO</Text>
          <Text style={styles.anonSubtitle}>@{profile.nickname}</Text>
          <Text style={styles.anonHint}>
            Gli utenti anonimi non condividono foto, storico voti, spille o profilo pubblico.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  const badge = profile.badge;
  const badgeUnlocked = badge?.unlocked === true;
  const badgeType = badge?.type; // 'buon_senso' | 'bastian_contrario' | undefined

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="public-user-screen">
      <View style={styles.topbar}>
        <Pressable onPress={goBack} testID="user-back" style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
          <Text style={styles.backTxt}>INDIETRO</Text>
        </Pressable>
        <Text style={styles.topNick}>@{profile.nickname}</Text>
        {canMessage ? (
          <Pressable onPress={() => setMenuOpen(true)} testID="user-menu" style={styles.menuBtn}>
            <Ionicons name="ellipsis-vertical" size={20} color={colors.onSurfaceInverse} />
          </Pressable>
        ) : (
          <View style={styles.menuBtn} />
        )}
      </View>

      <ScrollView
        ref={scrollRef}
        contentContainerStyle={{ paddingBottom: spacing.xxxl }}
        onScrollBeginDrag={() => {
          // User is manually scrolling — abort any pending restoration
          // so we don't yank the list back to the memorised offset.
          userInteractedRef.current = true;
          pendingScrollYRef.current = null;
        }}
        onScroll={(e) => {
          scrollMemory.setY(scrollKey, e.nativeEvent.contentOffset.y);
        }}
        scrollEventThrottle={16}
        onContentSizeChange={() => {
          // Only fires once at initial paint. If the user has already
          // started scrolling, respect their position instead of
          // snapping back.
          if (userInteractedRef.current) return;
          const target = pendingScrollYRef.current;
          if (target != null) {
            scrollRef.current?.scrollTo({ y: target, animated: false });
          }
        }}
      >
        <View style={styles.galleryWrap}>
          <View style={styles.avatarRing}>
            {hasPhotos ? (
              <Pressable
                onPress={() => setViewerOpen(true)}
                testID="open-gallery-viewer"
                style={{ width: "100%", height: "100%" }}
              >
                <Image
                  source={{ uri: `data:image/jpeg;base64,${current!.data}` }}
                  style={styles.avatarImg}
                  resizeMode="cover"
                  testID={`gallery-image-${idx}`}
                />
              </Pressable>
            ) : (
              <View style={[styles.avatarImg, styles.noPhotoBox]}>
                <Ionicons name="person-outline" size={72} color={colors.muted} />
              </View>
            )}
            {photos.length > 1 && (
              <>
                <Pressable onPress={prev} testID="gallery-prev" style={[styles.arrowSmall, { left: -spacing.xl }]}>
                  <Ionicons name="chevron-back" size={22} color={colors.onSurface} />
                </Pressable>
                <Pressable onPress={next} testID="gallery-next" style={[styles.arrowSmall, { right: -spacing.xl }]}>
                  <Ionicons name="chevron-forward" size={22} color={colors.onSurface} />
                </Pressable>
              </>
            )}
          </View>
          {photos.length > 1 && (
            <View style={styles.dotsRow}>
              {photos.map((_, i) => (
                <View key={i} style={[styles.dot, i === idx && styles.dotOn]} />
              ))}
            </View>
          )}
        </View>

        <View style={styles.body}>
          <Text style={styles.nick} testID="public-nickname">@{profile.nickname}</Text>
          {profile.display_name ? (
            <Text style={styles.displayName} testID="public-display-name">
              {profile.display_name}
            </Text>
          ) : null}
          <Pressable
            onPress={() =>
              router.push({
                pathname: "/circle/[userId]",
                params: { userId: id!, from: `/user/${id}` },
              })
            }
            style={styles.circleChip}
            testID="public-circle-open"
            hitSlop={4}
          >
            <Ionicons name="people" size={14} color={colors.onBrandSecondary} />
            <Text style={styles.circleChipTxt}>
              Cerchia · {circleCount}
            </Text>
          </Pressable>
          <Text style={styles.stat}>
            {profile.total_votes} voti · {profile.majority_votes} maggioranza · {profile.minority_votes} minoranza
          </Text>

          {canMessage && !isBlocked && (
            <View style={styles.ctaRow}>
              <Pressable onPress={openChat} style={[styles.msgCta, { flex: 1 }]} testID="user-send-message">
                <Ionicons name="chatbubble-ellipses" size={18} color={colors.onBrandPrimary} />
                <Text style={styles.msgCtaTxt}>MESSAGGIO</Text>
              </Pressable>
              <Pressable
                onPress={toggleCircle}
                disabled={circleWorking}
                style={[styles.circleCta, inCircle ? styles.circleCtaOn : null]}
                testID="user-circle-toggle"
              >
                <Ionicons
                  name={inCircle ? "checkmark-circle" : "person-add"}
                  size={18}
                  color={inCircle ? colors.onBrandSecondary : colors.onBrandPrimary}
                />
                <Text style={[styles.msgCtaTxt, inCircle ? { color: colors.onBrandSecondary } : null]}>
                  {inCircle ? "NELLA CERCHIA" : "AGGIUNGI"}
                </Text>
              </Pressable>
            </View>
          )}
          {canMessage && isBlocked && (
            <View style={styles.blockedNotice}>
              <Ionicons name="ban" size={16} color={colors.error} />
              <Text style={styles.blockedNoticeTxt}>
                Hai bloccato questo utente. Sbloccalo dal menu per riprendere la chat.
              </Text>
            </View>
          )}

          {badgeUnlocked && badgeType && (
            <Pressable
              style={[
                styles.badgeCard,
                badgeType === "bastian_contrario" ? styles.badgeCardRed : styles.badgeCardYellow,
              ]}
              testID={`public-badge-${badgeType}`}
              onPress={() => setBadgesOpen(true)}
              accessibilityLabel="Vedi la collezione completa delle spille"
            >
              <View
                style={[
                  styles.badgeIconWrap,
                  badgeType === "bastian_contrario" ? styles.badgeIconRed : styles.badgeIconYellow,
                ]}
              >
                <Ionicons
                  name={badgeType === "bastian_contrario" ? "flash" : "shield-checkmark"}
                  size={26}
                  color={badgeType === "bastian_contrario" ? colors.onBrandPrimary : colors.onBrandSecondary}
                />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.badgeKicker}>SPILLA · TOCCA PER LE ALTRE</Text>
                <Text
                  style={[
                    styles.badgeTitle,
                    badgeType === "bastian_contrario" ? { color: colors.onBrandPrimary } : { color: colors.onBrandSecondary },
                  ]}
                >
                  {badgeType === "bastian_contrario" ? "BASTIAN CONTRARIO" : "BUON SENSO"}
                </Text>
                <Text
                  style={[
                    styles.badgeSubtitle,
                    badgeType === "bastian_contrario" ? { color: colors.onBrandPrimary } : { color: colors.onBrandSecondary },
                  ]}
                >
                  {badge?.majority ?? 0} maggioranza · {badge?.minority ?? 0} minoranza
                </Text>
              </View>
              <Ionicons
                name="chevron-forward"
                size={18}
                color={badgeType === "bastian_contrario" ? colors.onBrandPrimary : colors.onBrandSecondary}
              />
            </Pressable>
          )}

          {/* Persistent CTA into the full 9×3 badge collection modal.
              Rendered ALWAYS (not just when the alignment badge is
              locked) so viewers have an unambiguous, discoverable
              entry point regardless of the profile owner's alignment
              status. The fancy badge card above is also tappable, but
              the pill guarantees the affordance is never hidden. */}
          <Pressable
            style={styles.viewBadgesFallback}
            onPress={() => setBadgesOpen(true)}
            testID="public-badges-view-all"
          >
            <Ionicons name="ribbon-outline" size={16} color={colors.brandPrimary} />
            <Text style={styles.viewBadgesFallbackTxt}>VEDI TUTTE LE SPILLE</Text>
            <Ionicons name="chevron-forward" size={16} color={colors.brandPrimary} />
          </Pressable>

          {profile.bio ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>BIO</Text>
              <Text style={styles.bio}>{profile.bio}</Text>
            </View>
          ) : null}

          {socialEntries.length > 0 && (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>SOCIAL</Text>
              {socialEntries.map(([k, v]) => (
                <Pressable
                  key={k}
                  onPress={() => Linking.openURL(String(v))}
                  style={styles.socialRow}
                  testID={`social-${k}`}
                >
                  <Ionicons name={SOCIAL_ICONS[k] || "link-outline"} size={20} color={colors.onSurface} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.socialLabel}>{SOCIAL_LABELS[k] || k}</Text>
                    <Text style={styles.socialUrl} numberOfLines={1}>{String(v)}</Text>
                  </View>
                  <Ionicons name="open-outline" size={18} color={colors.muted} />
                </Pressable>
              ))}
            </View>
          )}

          <View style={styles.section} testID="public-history-section">
            <Pressable
              onPress={() => setHistoryExpanded((v) => !v)}
              testID="public-history-toggle"
              style={styles.sectionHeadRow}
            >
              <Text style={styles.sectionTitle}>STORICO VOTI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{profile.total_votes ?? 0}</Text>
                <Ionicons name={historyExpanded ? "chevron-up" : "chevron-down"} size={20} color={colors.onSurface} />
              </View>
            </Pressable>
            {historyExpanded ? (
              <View testID="public-history-body">
                {/* Owner has opted this history out for this viewer type. Show
                    a clean placeholder with the appropriate reason instead
                    of listing votes. */}
                {historyHidden === "private" ? (
                  <View style={styles.historyHiddenBox} testID="public-history-hidden-private">
                    <Ionicons name="lock-closed" size={22} color={colors.muted} />
                    <Text style={styles.historyHiddenTitle}>Storico voti privato</Text>
                    <Text style={styles.historyHiddenHint}>
                      Questo utente ha scelto di non condividere il suo storico voti.
                    </Text>
                  </View>
                ) : historyHidden === "mutual_private" ? (
                  <View style={styles.historyHiddenBox} testID="public-history-hidden-mutual">
                    <Ionicons name="lock-closed" size={22} color={colors.muted} />
                    <Text style={styles.historyHiddenTitle}>Storico voti privato</Text>
                    <Text style={styles.historyHiddenHint}>
                      Nemmeno i membri della cerchia bilaterale possono vedere lo storico voti.
                    </Text>
                  </View>
                ) : (
                  <>
                <View style={styles.filterRow}>
                  {(["all", "majority", "minority"] as HFilter[]).map((f) => (
                    <Pressable
                      key={f}
                      onPress={() => setFilter(f)}
                      testID={`public-filter-${f}`}
                      style={[
                        styles.filterChip,
                        filter === f && styles.filterChipActive,
                      ]}
                    >
                      <Text style={[
                        styles.filterTxt,
                        filter === f && styles.filterTxtActive,
                      ]}>
                        {f === "all" ? "TUTTI" : f === "majority" ? "MAGGIORANZA" : "MINORANZA"}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                {loadingH && (
                  <View style={{ paddingVertical: spacing.lg, alignItems: "center" }}>
                    <ActivityIndicator color={colors.brandPrimary} />
                  </View>
                )}
                {!loadingH && history.length === 0 && (
                  <Text style={styles.emptyH} testID="public-history-empty">
                    Nessun voto in questa categoria.
                  </Text>
                )}
                {!loadingH && history.length > 0 && (
                  <View style={styles.historyList}>
                    {history.map((h) => {
                      const votedName = h.side_voted === "A" ? h.party_a : h.party_b;
                      return (
                        <Pressable
                          key={h.feud_id + h.voted_at}
                          style={styles.historyItem}
                          onPress={() => {
                            // Arm scroll-restoration for this public
                            // profile before navigating away — the
                            // key includes the user id so multiple
                            // profile visits don't share state.
                            scrollMemory.markRestore(scrollKey);
                            // Pass `?from=/user/{id}` so the feud
                            // back button returns to THIS profile,
                            // not "/" (Expo Router tabs don't build
                            // a real back-stack for href:null routes).
                            router.push({
                              pathname: "/feud/[id]" as any,
                              params: { id: h.feud_id, from: `/user/${id}` },
                            });
                          }}
                          testID={`public-history-${h.feud_id}`}
                        >
                          <View style={[styles.sideBar, { backgroundColor: sideColor(h.side_voted) }]} />
                          <View style={{ flex: 1, padding: spacing.sm }}>
                            <Text style={styles.hCat}>{h.category_label.toUpperCase()}</Text>
                            <Text style={styles.hTitle} numberOfLines={2}>{h.title}</Text>
                            <View style={styles.hMetaRow}>
                              <Text style={[styles.hVoted, { color: sideColor(h.side_voted) }]} numberOfLines={1}>
                                Ha votato: {votedName}
                              </Text>
                              <Text style={[styles.hBadge, h.aligned ? styles.hBadgeMaj : styles.hBadgeMin]}>
                                {h.aligned ? "MAGGIORANZA" : "MINORANZA"}
                              </Text>
                            </View>
                          </View>
                        </Pressable>
                      );
                    })}
                  </View>
                )}
                  </>
                )}
              </View>
            ) : null}
          </View>
        </View>
      </ScrollView>

      {/* Menu (block / report) */}
      <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setMenuOpen(false)}>
          <View style={styles.menuSheet}>
            <Pressable
              onPress={() => {
                setMenuOpen(false);
                openChat();
              }}
              style={styles.menuItem}
            >
              <Ionicons name="chatbubble-ellipses-outline" size={20} color={colors.onSurface} />
              <Text style={styles.menuTxt}>Invia messaggio</Text>
            </Pressable>
            <Pressable onPress={toggleBlock} style={styles.menuItem}>
              <Ionicons name={isBlocked ? "checkmark-circle-outline" : "ban-outline"} size={20} color={colors.error} />
              <Text style={[styles.menuTxt, { color: colors.error }]}>
                {isBlocked ? "Sblocca utente" : "Blocca utente"}
              </Text>
            </Pressable>
            <Pressable
              onPress={() => { setMenuOpen(false); setReportOpen(true); }}
              style={[styles.menuItem, { borderBottomWidth: 0 }]}
            >
              <Ionicons name="flag-outline" size={20} color={colors.error} />
              <Text style={[styles.menuTxt, { color: colors.error }]}>Segnala utente</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

      {/* Report */}
      <Modal visible={reportOpen} transparent animationType="fade" onRequestClose={() => setReportOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setReportOpen(false)}>
          <Pressable style={styles.reportSheet} onPress={() => {}}>
            <Text style={styles.sheetTitle}>SEGNALA @{profile.nickname}</Text>
            <TextInput
              style={styles.reportInput}
              value={reportText}
              onChangeText={setReportText}
              placeholder="Motivo della segnalazione…"
              placeholderTextColor={colors.muted}
              multiline
              maxLength={500}
            />
            <View style={{ flexDirection: "row", gap: spacing.sm }}>
              <Pressable
                onPress={() => setReportOpen(false)}
                style={[styles.reportBtn, { backgroundColor: colors.surfaceTertiary }]}
              >
                <Text style={{ color: colors.onSurface, letterSpacing: 1 }}>ANNULLA</Text>
              </Pressable>
              <Pressable
                onPress={submitReport}
                style={[styles.reportBtn, { backgroundColor: colors.brandPrimary }]}
              >
                <Text style={{ color: colors.onBrandPrimary, letterSpacing: 1, fontWeight: "500" }}>INVIA</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      <PhotoGalleryViewer
        visible={viewerOpen}
        photos={photos}
        initialIndex={idx}
        onClose={() => setViewerOpen(false)}
      />
      {/* Category badges collection — third-party view. Same modal
          component used on the owner's own profile screen so the
          experience is identical from either side. */}
      <CategoryBadgesModal
        visible={badgesOpen}
        userId={String(id)}
        displayName={profile.display_name || profile.nickname || undefined}
        onClose={() => setBadgesOpen(false)}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  err: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.md },
  topbar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.surfaceInverse, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: colors.border },
  backBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  backTxt: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, letterSpacing: 1 },
  topNick: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2 },
  menuBtn: { padding: spacing.xs, minWidth: 32, alignItems: "center" },
  msgCta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    marginTop: spacing.sm,
    backgroundColor: colors.brandPrimary,
    paddingVertical: spacing.md,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: colors.border,
  },
  msgCtaTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
  circleChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    marginTop: 8,
    marginBottom: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    backgroundColor: colors.brandSecondary,
  },
  circleChipTxt: { color: colors.onBrandSecondary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 0.5 },
  ctaRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  circleCta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.md,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.brandPrimary,
  },
  circleCtaOn: { backgroundColor: colors.brandSecondary },
  blockedNotice: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
    borderWidth: 2,
    borderColor: colors.error,
    backgroundColor: "rgba(255,59,48,0.08)",
  },
  blockedNoticeTxt: { color: colors.error, fontSize: font.sizes.sm, flex: 1 },
  modalBg: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "center", padding: spacing.lg },
  menuSheet: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: colors.border,
    overflow: "hidden",
  },
  menuItem: {
    flexDirection: "row",
    alignItems: "center",
    padding: spacing.md,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderColor: colors.surfaceTertiary,
  },
  menuTxt: { fontSize: font.sizes.base, color: colors.onSurface, letterSpacing: 0.5 },
  sheetTitle: { fontSize: font.sizes.sm, letterSpacing: 2, textAlign: "center", color: colors.onSurface, fontWeight: "500" },
  reportSheet: {
    backgroundColor: colors.surfaceSecondary,
    padding: spacing.lg,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: colors.border,
    gap: spacing.md,
  },
  reportInput: {
    minHeight: 100,
    maxHeight: 200,
    borderWidth: 1,
    borderColor: colors.surfaceTertiary,
    borderRadius: 8,
    padding: spacing.md,
    color: colors.onSurface,
    textAlignVertical: "top",
  },
  reportBtn: { flex: 1, padding: spacing.md, alignItems: "center", borderRadius: 8 },
  galleryWrap: { alignItems: "center", justifyContent: "center", backgroundColor: colors.surface, paddingTop: spacing.xl, paddingBottom: spacing.lg, borderBottomWidth: 2, borderColor: colors.border },
  avatarRing: { width: 160, height: 160, borderRadius: 80, backgroundColor: colors.surfaceInverse, alignItems: "center", justifyContent: "center", overflow: "visible", position: "relative" },
  avatarImg: { width: 160, height: 160, borderRadius: 80, backgroundColor: colors.surfaceInverse, overflow: "hidden" },
  arrowSmall: { position: "absolute", top: "50%", marginTop: -20, width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surfaceSecondary, borderWidth: 2, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  dotsRow: { flexDirection: "row", gap: 6, justifyContent: "center", marginTop: spacing.md },
  noPhotoBox: { alignItems: "center", justifyContent: "center", gap: spacing.sm },
  noPhotoTxt: { color: colors.muted, fontSize: font.sizes.base, letterSpacing: 1 },
  arrow: { position: "absolute", top: "50%", marginTop: -24, width: 48, height: 48, backgroundColor: "rgba(0,0,0,0.55)", borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  dots: { position: "absolute", bottom: spacing.md, left: 0, right: 0, flexDirection: "row", gap: 6, justifyContent: "center" },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: "rgba(0,0,0,0.2)" },
  dotOn: { backgroundColor: colors.brandPrimary },
  body: { padding: spacing.lg, gap: spacing.md },
  nick: { fontSize: font.sizes.xxxl, fontWeight: "500", letterSpacing: 1, color: colors.onSurface },
  displayName: { fontSize: font.sizes.base, color: colors.onSurface, opacity: 0.75, marginTop: 2 },
  badge: { alignSelf: "flex-start", paddingHorizontal: spacing.sm, paddingVertical: 4, borderWidth: 2, borderColor: colors.border, fontSize: font.sizes.xs, letterSpacing: 2, fontWeight: "500" },
  badgeRed: { backgroundColor: colors.brandPrimary, color: colors.onBrandPrimary },
  badgeYellow: { backgroundColor: colors.brandSecondary, color: colors.onBrandSecondary },
  badgeCard: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.md, borderWidth: 2, borderColor: colors.border, marginTop: spacing.sm },
  badgeCardRed: { backgroundColor: colors.brandPrimary },
  badgeCardYellow: { backgroundColor: colors.brandSecondary },
  badgeIconWrap: { width: 52, height: 52, borderWidth: 2, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  badgeIconRed: { backgroundColor: "#B31700" },
  badgeIconYellow: { backgroundColor: "#D6A800" },
  badgeKicker: { fontSize: font.sizes.xs, letterSpacing: 2, opacity: 0.7, color: colors.onBrandPrimary },
  badgeTitle: { fontSize: font.sizes.lg, fontWeight: "500", letterSpacing: 1.5, marginTop: 2 },
  badgeSubtitle: { fontSize: font.sizes.xs, letterSpacing: 1, opacity: 0.8, marginTop: 2 },
  viewBadgesFallback: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    paddingVertical: spacing.sm,
    borderWidth: 1,
    borderColor: colors.brandPrimary,
    borderRadius: 999,
  },
  viewBadgesFallbackTxt: {
    color: colors.brandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  anonBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.sm },
  anonAvatar: { width: 140, height: 140, borderRadius: 70, borderWidth: 0, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse, marginBottom: spacing.md, overflow: "hidden" },
  anonTitle: { fontSize: font.sizes.xxl, letterSpacing: 2.5, fontWeight: "500", color: colors.onSurface },
  anonSubtitle: { fontSize: font.sizes.base, color: colors.brandPrimary, letterSpacing: 1 },
  anonHint: { fontSize: font.sizes.sm, color: colors.muted, textAlign: "center", lineHeight: 20, marginTop: spacing.sm, paddingHorizontal: spacing.md },
  stat: { fontSize: font.sizes.sm, color: colors.muted, letterSpacing: 0.5 },
  section: { gap: spacing.xs, marginTop: spacing.md },
  sectionTitle: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.brandPrimary, fontWeight: "500" },
  sectionHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: spacing.sm, borderBottomWidth: 2, borderColor: colors.border },
  sectionHeadRight: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sectionCountBadge: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 1, minWidth: 20, textAlign: "right" },
  bio: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 20 },
  socialRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surfaceSecondary },
  socialLabel: { fontSize: font.sizes.sm, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  socialUrl: { fontSize: font.sizes.xs, color: colors.muted, marginTop: 2 },
  filterRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.xs },
  filterChip: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    borderRadius: radius.pill,
    paddingVertical: spacing.sm + 2,
    paddingHorizontal: spacing.xs,
    alignItems: "center",
    backgroundColor: "transparent",
  },
  filterChipActive: {
    backgroundColor: colors.surface,
    borderColor: colors.brandSecondary,
  },
  filterTxt: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted, fontWeight: "800" },
  filterTxtActive: { color: colors.brandSecondary },
  emptyH: { paddingVertical: spacing.lg, color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
  historyHiddenBox: {
    alignItems: "center",
    padding: spacing.lg,
    gap: spacing.sm,
    borderWidth: 2,
    borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
    marginTop: spacing.sm,
  },
  historyHiddenTitle: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  historyHiddenHint: { color: colors.muted, fontSize: font.sizes.sm, textAlign: "center", lineHeight: 18 },
  historyList: { gap: spacing.sm, marginTop: spacing.sm },
  historyItem: { flexDirection: "row", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, overflow: "hidden" },
  sideBar: { width: 8 },
  hCat: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted },
  hTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  hMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.xs, gap: spacing.sm },
  // Left label — shrinks and truncates so the right-anchored badge
  // never wraps to a new line (and jumps to the left).
  hVoted: { fontSize: font.sizes.xs, fontWeight: "500", flexShrink: 1, flex: 1 },
  hBadge: { fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1, borderRadius: radius.sm, fontWeight: "800" },
  // Neutral outline for MAGGIORANZA/MINORANZA badges on the public
  // profile — keeps them visually distinct from the vote-side colours.
  hBadgeMaj: { backgroundColor: "transparent", borderColor: colors.borderStrong, color: colors.muted },
  hBadgeMin: { backgroundColor: "transparent", borderColor: colors.borderStrong, color: colors.muted },
});
