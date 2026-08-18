/**
 * Populus — Il mio profilo (`/(tabs)/profile`).
 * ══════════════════════════════════════════════════════════════════
 *
 * Schermata "profilo mio": foto galleria, dati anagrafici, badges,
 * cronologia dei voti (con filtro maggioranza/minoranza), preferenze
 * privacy, edit profilo, professione.
 *
 * SEZIONI PRINCIPALI:
 *   §1 State + auth binding                   (~L31)
 *   §2 History cache & fetch                  (~L40)
 *   §3 Foto: pick / crop / upload / reorder   (verso la metà)
 *   §4 Filtri cronologia                      (verso la metà)
 *   §5 Modali: EditProfile / Prefs / Profession / CategoryBadges
 *   §6 Styles                                 (~L1465)
 *
 * Note:
 *   - `SCROLL_KEY = "my-profile"` è usato da `scrollMemory` per
 *     preservare la posizione tra remount del tab.
 *   - I sub-modal sono già estratti in `src/components/profile/`.
 * ══════════════════════════════════════════════════════════════════
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Image, Platform, Switch, Alert } from "react-native";
import { Image as ExpoImage } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import * as ImageManipulator from "expo-image-manipulator";
import * as FileSystem from "expo-file-system/legacy";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { api, HistoryItem, UserPhoto } from "@/src/api";
import { colors, spacing, font, sideColor, radius } from "@/src/theme";
import { ScrollToTopButton } from "@/src/components/ScrollToTopButton";
import PhotoCropper from "@/src/components/PhotoCropper";
import CategoryBadgesModal from "@/src/components/CategoryBadgesModal";
import ProfessionModal from "@/src/components/profile/ProfessionModal";
import PrefsModal from "@/src/components/profile/PrefsModal";
import EditProfileModal from "@/src/components/profile/EditProfileModal";
import { validateNickname, sanitizeNicknameInput } from "@/src/utils/nickname";
import { resolvePhotoUri } from "@/src/utils/photoCache";
import { Socials, EMPTY_SOCIALS } from "@/src/utils/socials";
import { scrollMemory } from "@/src/utils/scrollMemory";
import { cachedGet } from "@/src/utils/clientCache";

// Module-level key used to identify this screen's entry in the
// cross-mount scroll memory. `MY_PROFILE` is a stable string so the
// state survives component remounts triggered by Expo Router's tabs
// navigator when navigating to href:null detail screens.
const SCROLL_KEY = "my-profile";

type Filter = "all" | "majority" | "minority";

export default function Profile() {
  const { user, logout, refreshMe } = useAuth();
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("all");
  // Per-filter cache. Prevents a jarring "loading" flash every time
  // the user flips between all/majority/minority — the previous
  // result stays on screen and we only refetch in the background if
  // the cache is older than the TTL.
  const [historyCache, setHistoryCache] = useState<Record<Filter, HistoryItem[]>>(
    {} as Record<Filter, HistoryItem[]>,
  );
  const historyLoadedAtRef = useRef<Record<Filter, number>>({} as Record<Filter, number>);
  const HISTORY_CACHE_TTL_MS = 60_000; // 1 minute — same feel as the AI summary cache
  const history = historyCache[filter] || [];
  // Only surface the spinner when we have absolutely nothing to show
  // for the current filter. If cached rows exist we keep them visible
  // while a silent background refetch (if any) is in flight.
  const [refreshingH, setRefreshingH] = useState(false);
  const loadingH = refreshingH && !historyCache[filter];
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [pushEnabled, setPushEnabled] = useState<boolean>(user?.push_notifications !== false);
  const [cats, setCats] = useState<{ id: string; label: string }[]>([]);
  const [professionsList, setProfessionsList] = useState<string[]>([]);
  const [professionOpen, setProfessionOpen] = useState(false);
  const [savingProfession, setSavingProfession] = useState(false);
  const [editNick, setEditNick] = useState("");
  const [editDisplay, setEditDisplay] = useState("");
  const [editSel, setEditSel] = useState<Set<string>>(new Set());
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  // Profile customization
  const [profileOpen, setProfileOpen] = useState(false);
  // Category badges collection modal — opens when the user taps the
  // main alignment badge (or its label) on the profile.
  const [badgesOpen, setBadgesOpen] = useState(false);

  // Scroll-position preservation across tab focus changes AND across
  // component remounts. Expo Router's tabs navigator can unmount the
  // profile screen when navigating to a hidden detail route (e.g.
  // /feud/[id]), which loses component-local refs. `scrollMemory`
  // stores the offset + restore flag at module scope so they
  // survive the round-trip.
  const scrollRef = useRef<ScrollView>(null);
  // Toggle for the floating "back to top" pill on the profile.
  const [showTopBtn, setShowTopBtn] = useState(false);
  // Y position of the STORICO VOTI section (captured via onLayout).
  // The pill scrolls to this offset so the user lands back at the top
  // of their vote history — not at the avatar/header.
  const historyYRef = useRef(0);
  // Local mirror of the target offset applied by the retry-loop
  // during a restoration. Cleared once we've stopped chasing the
  // offset so normal user scrolling isn't fought.
  const pendingScrollYRef = useRef<number | null>(null);
  // Detects whether the user has manually started dragging the ScrollView
  // after focus. Any pending scroll-restore timer bails out when this is
  // true so we don't yank the list back and fight the user (the reported
  // "can't scroll for 1s after returning to profile" bug).
  const userInteractedRef = useRef(false);
  const [photos, setPhotos] = useState<UserPhoto[]>([]);
  const [loadingPhotos, setLoadingPhotos] = useState(false);
  const [bio, setBio] = useState<string>("");
  const [socials, setSocials] = useState<Socials>(EMPTY_SOCIALS);
  const [savingDetails, setSavingDetails] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [primaryPhotoData, setPrimaryPhotoData] = useState<string | null>(null);
  const [primaryPhotoUri, setPrimaryPhotoUri] = useState<string | null>(null);
  const [photoUris, setPhotoUris] = useState<Record<string, string>>({});
  const [prefsExpanded, setPrefsExpanded] = useState(false);
  const [historyExpanded, setHistoryExpanded] = useState(false);
  // Voting-history privacy flags — mirror the two backend toggles
  // (`generic` covers strangers, `mutual` covers cerchia bilaterale).
  // Local state lets the switches feel instant; failed requests are
  // rolled back so the UI never lies about the persisted value.
  const [histPublicGeneric, setHistPublicGeneric] = useState<boolean>(
    user?.history_public_generic !== false,
  );
  const [histPublicMutual, setHistPublicMutual] = useState<boolean>(
    user?.history_public_mutual !== false,
  );
  // Cropper state
  const [cropperOpen, setCropperOpen] = useState(false);
  const [cropperUri, setCropperUri] = useState<string | null>(null);
  const [cropperSize, setCropperSize] = useState<{ w: number; h: number } | null>(null);
  // If set, we are RE-cropping an existing photo; on confirm we PATCH that
  // photo instead of adding a new one.
  const [cropperReplaceId, setCropperReplaceId] = useState<string | null>(null);
  // File URI pointing at the ORIGINAL (uncropped) source shown inside the
  // cropper. We keep a reference so, on confirm, we can encode the source
  // itself as `original_data` and send it to the backend — this is what
  // makes re-cropping non-destructive (the user can zoom back out later).
  const [cropperOriginalSourceUri, setCropperOriginalSourceUri] = useState<string | null>(null);
  // Pre-encoded original — populated in the background as soon as the user
  // picks a source to crop. By the time they hit "confirm", encoding is
  // already done and the upload feels instantaneous (the biggest chunk of
  // perceived latency in the previous flow was this synchronous encode).
  const preEncodedOriginalRef = useRef<{ uri: string; base64: string | null }>({ uri: "", base64: null });
  const [openingRecrop, setOpeningRecrop] = useState<string | null>(null);
  // Blocked users management — loaded lazily when the user expands the panel.
  const [blocksOpen, setBlocksOpen] = useState(false);
  const [blockedList, setBlockedList] = useState<any[]>([]);
  const [loadingBlocks, setLoadingBlocks] = useState(false);
  const isAnonymous = user?.auth_provider === "anonymous";

  // Track whether we've done at least one fetch so the spinner only shows
  // on the very first load. Subsequent focus refreshes / toggle opens are
  // silent (no spinner flash) but ALWAYS update the list so real-time
  // block/unblock actions from other screens propagate immediately.
  const hasLoadedBlocksRef = useRef(false);
  const loadBlocked = useCallback(async () => {
    if (!hasLoadedBlocksRef.current) setLoadingBlocks(true);
    try {
      const r = await api.myBlocks();
      setBlockedList(r?.blocked_users || []);
      hasLoadedBlocksRef.current = true;
    } catch { /* silent */ }
    finally { setLoadingBlocks(false); }
  }, []);

  const unblockOne = async (uid: string) => {
    try {
      await api.unblockUser(uid);
      setBlockedList((prev) => prev.filter((u) => u.user_id !== uid));
    } catch (e: any) {
      if (Platform.OS === "web" && typeof window !== "undefined") window.alert(e?.detail || "Impossibile sbloccare");
    }
  };

  const loadHistory = useCallback(async (f: Filter, opts?: { force?: boolean }) => {
    const now = Date.now();
    const lastLoaded = historyLoadedAtRef.current[f] || 0;
    const isFresh = now - lastLoaded < HISTORY_CACHE_TTL_MS;
    // Cache hit AND user isn't forcing a refresh → skip entirely.
    // No network call, no spinner, no re-render churn.
    if (isFresh && !opts?.force) return;
    setRefreshingH(true);
    try {
      const r = await api.history(f);
      setHistoryCache((prev) => ({ ...prev, [f]: r.history }));
      historyLoadedAtRef.current[f] = Date.now();
    } catch {
      // Swallow — auth-drop races during logout must never bubble
      // out and crash the tree with a red-screen.
    } finally { setRefreshingH(false); }
  }, []);

  // Centralised logout — kept intentionally minimal now that the
  // (tabs) layout has a safety-net redirect that fires the instant
  // `user` flips to null. We just:
  //   1. Wipe the token + fire the backend + Firebase signout (all
  //      inside AuthContext.logout — never throws).
  //   2. Clear the manual back-stack in the background.
  //
  // On web we still hard-reload after `logout()` so the whole runtime
  // starts from a clean slate (avoids stale provider caches when the
  // user logs BACK IN with a different account in the same tab). On
  // native we let the (tabs) layout handle the redirect — the moment
  // `setUser(null)` fires, `TabsLayout` unmounts every child screen
  // and pushes `/auth`, eliminating the race that used to red-screen.
  const doLogout = useCallback(async () => {
    try { scrollMemory.reset(); } catch { /* noop */ }
    // Clear manual back-stack up front so nothing else can navigate
    // through stale entries while we tear down auth.
    try {
      const { navStack } = await import("@/src/utils/navStack");
      navStack.clear();
    } catch { /* noop */ }
    if (Platform.OS === "web" && typeof window !== "undefined") {
      // WEB: skip the React state update inside logout() so no
      // still-mounted component re-renders with user===null before
      // the hard-reload takes over.
      try { await logout({ skipStateUpdates: true }); } catch { /* noop */ }
      try { window.location.replace("/auth"); return; } catch { /* fall through */ }
    }
    // NATIVE: just flip user to null. TabsLayout's redirect effect
    // handles the navigation atomically. No setTimeout, no explicit
    // router call here — those introduced the very race conditions
    // that caused the "app crashes on every logout" report.
    try { await logout(); } catch { /* noop */ }
  }, [logout]);

  useEffect(() => {
    refreshMe();
    // History is lazy-loaded when the user expands the section (see effect below).
  }, [refreshMe]);

  // Refresh user data (total_votes/majority/minority + badge) and expanded
  // voting history each time the profile tab regains focus — this keeps stats
  // in sync after actions taken elsewhere (voting on a feud, etc.).
  // Isolate the blocked-list refresh into its own focus effect so the
  // `user` reference churn from refreshMe() above doesn't retrigger the
  // main effect. We depend only on stable primitives (uid + anon flag).
  const uid = user?.user_id;
  const isAnon = !!user?.is_anonymous;
  // Focus-based refresh: fires every time the Profile tab regains focus so
  // block/unblock actions performed on other screens propagate back.
  useFocusEffect(
    useCallback(() => {
      if (uid && !isAnon) loadBlocked();
    }, [uid, isAnon, loadBlocked])
  );

  // On tab re-focus, decide whether to preserve scroll or reset to top.
  // Uses the module-scoped `scrollMemory` singleton so the state
  // survives even if the profile component was unmounted during the
  // trip to a detail screen (as happens with `href: null` routes in
  // the Expo Router tabs navigator).
  //
  // Why the retry loop? On focus, `refreshMe()` and other on-focus
  // effects can shift the ScrollView content (avatar image loads,
  // history rows swap in). Applying `scrollTo` a single time gets
  // silently undone by those layout changes. We reapply repeatedly
  // for ~1.2s and also on every `onContentSizeChange` event.
  const pendingScrollYRef2 = pendingScrollYRef; // alias for clarity
  useFocusEffect(
    useCallback(() => {
      const y = scrollMemory.getY(SCROLL_KEY);
      const shouldRestore = scrollMemory.consumeRestore(SCROLL_KEY);
      // Reset "user has touched the ScrollView" flag on every focus so a
      // legitimate restore attempt is possible.
      userInteractedRef.current = false;
      if (shouldRestore && y > 0) {
        pendingScrollYRef2.current = y;
        // Two lightweight attempts — the old 6-timer scheme (0…1200ms)
        // kept fighting the user for over a second if they tried to
        // scroll manually just after returning to the profile.
        const attempts = [0, 80];
        const timers: any[] = [];
        attempts.forEach((ms) => {
          timers.push(setTimeout(() => {
            if (userInteractedRef.current) return; // user is dragging, abort
            const target = pendingScrollYRef2.current;
            if (target != null) {
              scrollRef.current?.scrollTo({ y: target, animated: false });
            }
          }, ms));
        });
        timers.push(setTimeout(() => { pendingScrollYRef2.current = null; }, 350));
        return () => { timers.forEach((t) => clearTimeout(t)); };
      }
      // Not restoring → snap to top and reset stored offset so the
      // next tab-bar re-tap also lands at the top instead of the
      // previously-remembered offset.
      pendingScrollYRef2.current = null;
      scrollMemory.setY(SCROLL_KEY, 0);
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ y: 0, animated: false });
      });
    }, [pendingScrollYRef2]),
  );

  // Mount / auth-ready refresh: if the user was still loading when the
  // profile mounted, useFocusEffect above never fired with a valid uid.
  // This useEffect covers that first-load edge case so the blocked-list
  // is available the moment auth resolves.
  useEffect(() => {
    if (uid && !isAnon) loadBlocked();
  }, [uid, isAnon, loadBlocked]);

  useFocusEffect(
    useCallback(() => {
      refreshMe();
      if (historyExpanded) {
        // Silent background refresh on focus: invalidate every filter's
        // freshness so tapping other tabs also re-fetches, then force-
        // refetch the current one. Cached rows stay visible during the
        // refetch (no spinner) so the user sees updated votes without
        // any loading flash. This is what makes newly-cast votes appear
        // immediately upon returning to the profile tab.
        historyLoadedAtRef.current = {} as Record<Filter, number>;
        loadHistory(filter, { force: true });
      }
    }, [refreshMe, historyExpanded, loadHistory, filter])
  );

  useEffect(() => {
    if (historyExpanded) loadHistory(filter);
  }, [historyExpanded, filter, loadHistory]);

  // Keep the privacy switches in sync with the latest `user` snapshot from
  // AuthContext (e.g. after refreshMe() runs on focus). Anything undefined
  // is treated as True to preserve pre-existing "always public" behaviour.
  //
  // Guard: while an optimistic toggle is IN FLIGHT (pending API roundtrip)
  // we skip the sync — otherwise `refreshMe()` firing between the tap and
  // the response would snap the Switch back to the STALE server value,
  // producing the "capriccio" animation the user reported. The effect also
  // resumes cleanly once the pending counter drops to zero.
  const histPrivacyPendingRef = useRef(0);
  useEffect(() => {
    if (histPrivacyPendingRef.current > 0) return;
    const g = user?.history_public_generic !== false;
    const m = user?.history_public_mutual !== false;
    setHistPublicGeneric((cur) => (cur === g ? cur : g));
    setHistPublicMutual((cur) => (cur === m ? cur : m));
  }, [user?.history_public_generic, user?.history_public_mutual]);

  /**
   * Toggle a single history-privacy flag with optimistic UI + rollback.
   * A single PATCH persists both flags on the server side; only the
   * changed one is sent in the payload.
   *
   * Rapid-tap safety: we take the functional setter form and increment
   * a pending counter so the sync-from-user effect above stays parked
   * until the roundtrip resolves. This kills the double-animation
   * glitch (Switch flipping twice) reported by the founder when the
   * button was tapped in quick succession or while the profile
   * re-focused.
   */
  const updateHistPrivacy = useCallback(async (kind: "generic" | "mutual") => {
    const setter = kind === "generic" ? setHistPublicGeneric : setHistPublicMutual;
    // Capture the previous value FROM the setter itself (not from the
    // outer closure) so rapid taps chain correctly.
    let prev = true;
    setter((cur) => { prev = cur; return !cur; });
    histPrivacyPendingRef.current += 1;
    try {
      await api.updateHistoryPrivacy({ [kind]: !prev } as any);
    } catch {
      // Rollback on failure.
      setter(prev);
      Alert.alert("Errore", "Impossibile aggiornare le impostazioni. Riprova.");
    } finally {
      histPrivacyPendingRef.current = Math.max(0, histPrivacyPendingRef.current - 1);
    }
  }, []);

  // Note: we previously auto-refreshed the vote history every 30s via
  // setInterval so the MAGGIORANZA/MINORANZA badges could reflect
  // real-time majority flips. That turned out to jarringly reset the
  // scroll position and re-render the list while the user was still
  // reading it. We removed the interval: the focus-based refresh
  // above (which re-fires whenever the Profile tab regains focus) is
  // more than enough for the intended UX. Users who want the very
  // latest state can simply leave the tab and come back.

  useEffect(() => {
    (async () => {
      try {
        // Categorie e professioni sono statiche server-side: cache 10min
        // rende l'apertura del profilo istantanea al ri-ingresso.
        const c = await cachedGet('categories', 600_000, () => api.categories());
        setCats(c.categories);
      } catch {}
      try {
        const p = await cachedGet('professions', 600_000, () => api.professions());
        setProfessionsList((p as any).professions || []);
      } catch {}
    })();
  }, []);

  const saveProfession = async (value: string) => {
    if (!user) return;
    if (!user.age || !user.sex || !user.region) {
      // Falls back to prefs modal, which surfaces onboarding message.
      setProfessionOpen(false);
      openPrefs();
      return;
    }
    setSavingProfession(true);
    try {
      await api.updateProfile({
        age: user.age,
        sex: user.sex as "F" | "M" | "other" | "na",
        region: user.region,
        favorite_categories: user.favorite_categories || [],
        profession: value,
      });
      await refreshMe();
    } finally {
      setSavingProfession(false);
      setProfessionOpen(false);
    }
  };

  const loadPhotos = useCallback(async () => {
    setLoadingPhotos(true);
    try {
      const r = await api.myPhotos();
      const list: UserPhoto[] = r.photos || [];
      setPhotos(list);
      const primary = list.find((p) => p.photo_id === r.primary_photo_id) || list[0];
      setPrimaryPhotoData(primary?.data || null);
      // Resolve every photo to a file URI (or data URI fallback on web).
      const entries: Record<string, string> = {};
      for (const p of list) {
        try {
          entries[p.photo_id] = await resolvePhotoUri(p);
        } catch {
          entries[p.photo_id] = `data:image/jpeg;base64,${p.data}`;
        }
      }
      setPhotoUris(entries);
      setPrimaryPhotoUri(primary ? entries[primary.photo_id] || null : null);
    } catch {
      // Swallow — never let an auth-drop race (token cleared while
      // profile.tsx is still mounted during logout) surface as a
      // red-screen "Missing bearer token" uncaught error.
    } finally { setLoadingPhotos(false); }
  }, []);

  useEffect(() => {
    loadPhotos().catch(() => { /* already caught inside loadPhotos, extra belt */ });
  }, [loadPhotos]);

  const openProfileEdit = () => {
    setDetailsError(null);
    setBio(user?.bio || "");
    // Normalize the initial nickname to lowercase so a legacy uppercase
    // value doesn't count as "changed" on first save.
    setEditNick(sanitizeNicknameInput(user?.nickname || ""));
    setEditDisplay(user?.display_name || "");
    const sl = user?.social_links || {};
    setSocials({
      instagram: (sl as any).instagram || "",
      tiktok: (sl as any).tiktok || "",
      twitter: (sl as any).twitter || "",
      youtube: (sl as any).youtube || "",
      website: (sl as any).website || "",
    });
    setProfileOpen(true);
  };

  const pickPhoto = async (source: "library" | "camera") => {
    if (photos.length >= 7) { setDetailsError("Massimo 7 foto totali"); return; }
    setDetailsError(null);
    let perm;
    if (source === "camera") {
      perm = await ImagePicker.requestCameraPermissionsAsync();
    } else {
      perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    }
    if (!perm.granted) {
      if (!perm.canAskAgain) {
        setDetailsError(
          source === "camera"
            ? "Permesso fotocamera negato. Aprilo dalle impostazioni."
            : "Permesso libreria foto negato. Aprilo dalle impostazioni."
        );
      } else {
        setDetailsError(
          source === "camera" ? "Serve il permesso della fotocamera" : "Serve il permesso alla libreria foto"
        );
      }
      return;
    }
    // Native editing is disabled — we use our custom PhotoCropper instead so
    // the user can always choose which portion of the picture is kept, even on
    // the web preview where the native crop UI is inconsistent.
    const opts: ImagePicker.ImagePickerOptions = {
      mediaTypes: ["images"],
      quality: 1,
      base64: false,
      allowsEditing: false,
    };
    const res =
      source === "camera"
        ? await ImagePicker.launchCameraAsync(opts)
        : await ImagePicker.launchImageLibraryAsync(opts);
    if (res.canceled || !res.assets[0]) return;
    const asset = res.assets[0];
    if (!asset.uri) { setDetailsError("Impossibile leggere l'immagine"); return; }
    setCropperUri(asset.uri);
    setCropperOriginalSourceUri(asset.uri);
    setCropperSize(
      asset.width && asset.height ? { w: asset.width, h: asset.height } : null,
    );
    setCropperReplaceId(null);
    setCropperOpen(true);
  };

  /**
   * Encode a source URI as a bounded-size base64 JPEG suitable for storing
   * as `original_data` on the server. We downscale to max 1440px (long
   * side) and compress moderately so a full-quality gallery photo doesn't
   * blow past the 3.5MB payload cap while still preserving enough
   * resolution for meaningful re-crops later.
   */
  const encodeOriginalForUpload = useCallback(async (uri: string): Promise<string | null> => {
    try {
      const out = await ImageManipulator.manipulateAsync(
        uri,
        [{ resize: { width: 1440 } }],
        { compress: 0.72, format: ImageManipulator.SaveFormat.JPEG, base64: true },
      );
      if (!out.base64) return null;
      // Safety net: if resize+compress still produced an oversized payload
      // (very tall images downscale less aggressively), re-shrink harder.
      if (out.base64.length > 3_200_000) {
        const smaller = await ImageManipulator.manipulateAsync(
          uri,
          [{ resize: { width: 1080 } }],
          { compress: 0.65, format: ImageManipulator.SaveFormat.JPEG, base64: true },
        );
        return smaller.base64 || out.base64;
      }
      return out.base64;
    } catch (e) {
      console.warn("encodeOriginal failed", e);
      return null;
    }
  }, []);

  // Kick off the original encoding IN BACKGROUND the moment we know which
  // source URI is going to be cropped. This runs in parallel with the user
  // panning/zooming inside the cropper, so by the time they hit "confirm"
  // the (relatively slow) 1440px re-encode is already sitting in
  // `preEncodedOriginalRef.current.base64` — no user-visible wait.
  useEffect(() => {
    const src = cropperOriginalSourceUri;
    if (!src) return;
    if (cropperReplaceId) return; // re-crop path doesn't need to re-send original
    // Skip if already encoded for this exact URI.
    if (preEncodedOriginalRef.current.uri === src && preEncodedOriginalRef.current.base64) return;
    let cancelled = false;
    preEncodedOriginalRef.current = { uri: src, base64: null };
    (async () => {
      const b64 = await encodeOriginalForUpload(src);
      if (cancelled) return;
      // Only commit if the source URI hasn't changed under us mid-flight.
      if (preEncodedOriginalRef.current.uri === src) {
        preEncodedOriginalRef.current = { uri: src, base64: b64 };
      }
    })();
    return () => { cancelled = true; };
  }, [cropperOriginalSourceUri, cropperReplaceId, encodeOriginalForUpload]);

  const uploadCroppedPhoto = useCallback(async (base64: string) => {
    // Close the cropper modal immediately after we have the crop payload.
    // The heavy work (encoding + network + refresh) continues in the
    // background — the user sees the profile again right away instead of
    // watching a spinner. Any error is surfaced via Alert as before.
    setCropperOpen(false);
    setCropperUri(null);
    setCropperSize(null);
    const replaceId = cropperReplaceId;
    const origUri = cropperOriginalSourceUri;
    setCropperReplaceId(null);
    setCropperOriginalSourceUri(null);

    // Fire off — but do NOT await — the state refreshes at the end. They
    // update UI once they resolve; blocking on them just makes the confirm
    // feel slow. `loadPhotos` + `refreshMe` return quickly enough on their
    // own but we don't want them on the critical path.
    try {
      let payload = base64;
      // Only re-shrink when payload is genuinely huge — the cropper already
      // caps at 480px (see PhotoCropper.tsx) so this rarely triggers.
      if (payload.length > 900_000) {
        try {
          const manipulated = await ImageManipulator.manipulateAsync(
            `data:image/jpeg;base64,${payload}`,
            [{ resize: { width: 900 } }],
            { compress: 0.75, format: ImageManipulator.SaveFormat.JPEG, base64: true },
          );
          if (manipulated.base64) payload = manipulated.base64;
        } catch { /* keep original */ }
      }
      if (replaceId) {
        await api.replacePhoto(replaceId, payload);
      } else {
        // Use the PRE-ENCODED original if we have it (it was encoded in the
        // background while the user was cropping). Fall back to a fresh
        // encode only if the pre-encode didn't finish in time.
        let originalPayload: string | undefined;
        if (origUri) {
          if (preEncodedOriginalRef.current.uri === origUri && preEncodedOriginalRef.current.base64) {
            originalPayload = preEncodedOriginalRef.current.base64;
          } else {
            const encoded = await encodeOriginalForUpload(origUri);
            if (encoded) originalPayload = encoded;
          }
        }
        await api.uploadPhoto(payload, originalPayload);
      }
      // Refresh in background — do not block.
      loadPhotos().catch(() => {});
      refreshMe().catch(() => {});
      // Clear the pre-encoded cache once used.
      preEncodedOriginalRef.current = { uri: "", base64: null };
    } catch (e: any) {
      const msg = e?.detail || e?.message || "Errore durante il salvataggio della foto";
      Alert.alert("Impossibile salvare la foto", String(msg));
      setDetailsError(msg);
    }
  }, [loadPhotos, refreshMe, cropperReplaceId, cropperOriginalSourceUri, encodeOriginalForUpload]);

  const recropPhoto = useCallback(async (p: UserPhoto) => {
    if (openingRecrop) return;
    setOpeningRecrop(p.photo_id);
    try {
      // Fetch the ORIGINAL uncropped source so the user can zoom out again.
      // The list endpoint intentionally omits `original_data` to keep the
      // payload lean, so we grab it lazily here. For legacy photos saved
      // before the field existed, the backend transparently returns `data`.
      let sourceB64: string;
      try {
        const res = await api.getPhotoOriginal(p.photo_id);
        sourceB64 = (res as any)?.original_data || p.data;
      } catch {
        sourceB64 = p.data;
      }
      let sourceUri: string;
      if (Platform.OS === "web") {
        sourceUri = `data:image/jpeg;base64,${sourceB64}`;
      } else {
        const dir = (FileSystem as any).cacheDirectory || (FileSystem as any).documentDirectory;
        if (!dir) throw new Error("Nessuna cache directory disponibile");
        const safe = p.photo_id.replace(/[^a-zA-Z0-9_]/g, "_");
        sourceUri = `${dir}recrop_${safe}_${Date.now()}.jpg`;
        await FileSystem.writeAsStringAsync(sourceUri, sourceB64, {
          encoding: FileSystem.EncodingType.Base64,
        });
      }
      setCropperUri(sourceUri);
      setCropperOriginalSourceUri(sourceUri);
      setCropperSize(null);
      setCropperReplaceId(p.photo_id);
      setCropperOpen(true);
    } catch (e: any) {
      const msg = e?.message || "Impossibile aprire l'editor foto";
      Alert.alert("Errore", String(msg));
      setDetailsError(msg);
    } finally {
      setOpeningRecrop(null);
    }
  }, [openingRecrop]);

  const setPrimary = async (photoId: string) => {
    try {
      await api.setPrimaryPhoto(photoId);
      await loadPhotos();
      await refreshMe();
    } catch (e: any) { setDetailsError(e?.message || "Errore"); }
  };

  const deletePhoto = async (photoId: string) => {
    try {
      await api.deletePhoto(photoId);
      await loadPhotos();
      await refreshMe();
    } catch (e: any) { setDetailsError(e?.message || "Errore"); }
  };

  const saveDetails = async () => {
    setSavingDetails(true); setDetailsError(null);
    try {
      // Nickname / display name are stored on the profile record and require
      // the age / sex / region trio to have been completed at onboarding.
      const nick = sanitizeNicknameInput(editNick).trim();
      const displayName = editDisplay.trim();
      // Compare lowercased forms so a legacy uppercase nickname in the DB
      // doesn't get flagged as changed when the user just re-saves.
      const nickChanged = user && nick !== (user.nickname || "").toLowerCase();
      const displayChanged = user && displayName !== (user.display_name || "");

      // Validate nickname explicitly whenever the user touched the field so
      // that "SALVA" always produces actionable feedback instead of silently
      // proceeding.
      const nickErr = nickChanged ? validateNickname(editNick) : null;
      if (nickErr) {
        setDetailsError(nickErr);
        setSavingDetails(false);
        return;
      }

      // 1) Persist bio + social handles (both always writable).
      await api.updateDetails({ bio: bio.trim(), social_links: socials });

      // 2) If the nickname or display name changed, hit the profile endpoint.
      //    We keep the existing onboarding fields exactly as they are so we
      //    never regress age/sex/region/favorite_categories/profession.
      if (user && (nickChanged || displayChanged)) {
        if (!user.age || !user.sex || !user.region) {
          setDetailsError("Completa prima l'onboarding per modificare il nickname.");
          setSavingDetails(false);
          return;
        }
        await api.updateProfile({
          age: user.age,
          sex: user.sex as "F" | "M" | "other" | "na",
          region: user.region,
          favorite_categories: user.favorite_categories || [],
          ...(nickChanged ? { nickname: nick } : {}),
          ...(displayChanged ? { display_name: displayName } : {}),
          ...(user.profession ? { profession: user.profession } : {}),
        });
      }
      await refreshMe();
      setProfileOpen(false);
    } catch (e: any) {
      // Backend errors bubble up as { detail: "…" }. Prefer that message so the
      // user sees exactly why the save failed (e.g. "nickname già in uso").
      const detail = e?.detail || e?.response?.data?.detail;
      setDetailsError(detail || e?.message || "Impossibile salvare le modifiche. Riprova.");
    }
    finally { setSavingDetails(false); }
  };

  const openPrefs = () => {
    setPrefsError(null);
    setEditSel(new Set(user?.favorite_categories || []));
    setPrefsOpen(true);
  };

  const toggleEdit = (id: string) => {
    setEditSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const allSelected = useMemo(
    () => cats.length > 0 && editSel.size === cats.length,
    [cats, editSel]
  );

  const toggleAllEdit = () => {
    if (allSelected) setEditSel(new Set());
    else setEditSel(new Set(cats.map((c) => c.id)));
  };

  const savePrefs = async () => {
    if (!user) return;
    if (editSel.size === 0) { setPrefsError("Scegli almeno una categoria"); return; }
    if (!user.age || !user.sex || !user.region) {
      setPrefsError("Profilo incompleto — completa prima l'onboarding");
      return;
    }
    setSavingPrefs(true); setPrefsError(null);
    try {
      await api.updateProfile({
        age: user.age,
        sex: user.sex as "F" | "M" | "other" | "na",
        region: user.region,
        favorite_categories: Array.from(editSel),
        ...(user.profession ? { profession: user.profession } : {}),
      });
      await refreshMe();
      setPrefsOpen(false);
    } catch (e: any) {
      setPrefsError(e?.message || "Errore durante il salvataggio");
    } finally {
      setSavingPrefs(false);
    }
  };

  if (!user) return null;

  const badge = user.badge;
  const badgeUnlocked = badge?.unlocked;
  const badgeType = badge?.type;

  // Anonymous users don't have a profile page. Show a full-screen block with
  // the same "profilo bloccato / registrati ora" message + CTA.
  if (isAnonymous) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]} testID="profile-screen">
        <View style={styles.anonLockScreen} testID="anon-lock-screen">
          <View style={styles.anonLockCircle}>
            <Ionicons name="lock-closed-outline" size={72} color={colors.brandSecondary} />
          </View>
          <Text style={styles.anonLockTitle}>PROFILO BLOCCATO</Text>
          <Text style={styles.anonLockSubtitle}>@{user.nickname}</Text>
          <Text style={styles.anonLockBody}>
            Come utente anonimo non hai un profilo pubblico e non puoi aggiungere foto,
            descrizione, link social o vedere il tuo storico voti.
          </Text>
          <Text style={styles.anonLockBody}>
            Registrati con un account per sbloccare tutte le funzionalità.
          </Text>
          <Pressable
            onPress={doLogout}
            testID="anon-register-btn"
            style={styles.anonLockCta}
          >
            <Text style={styles.anonLockCtaTxt}>REGISTRATI ORA  ›</Text>
          </Pressable>
          <Pressable
            onPress={doLogout}
            testID="anon-logout-btn"
            style={styles.anonLockLogout}
            hitSlop={8}
          >
            <Ionicons name="log-out-outline" size={14} color={colors.muted} />
            <Text style={styles.anonLockLogoutTxt}>Esci dalla sessione anonima</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="profile-screen">
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={styles.content}
        onScrollBeginDrag={() => {
          // Manual scroll started — abort any pending restore so we
          // don't yank the list back and lock scrolling for ~1s.
          userInteractedRef.current = true;
          pendingScrollYRef.current = null;
        }}
        onScroll={(e) => {
          const y = e.nativeEvent.contentOffset.y;
          // Persist to module scope so a component remount during
          // navigation to a detail screen doesn't wipe the offset.
          scrollMemory.setY(SCROLL_KEY, y);
          // Floating "back to top" pill appears only when the user has
          // scrolled DEEP into the STORICO VOTI history (500 px past
          // the section header). This has two advantages over a fixed
          // threshold:
          //   1. The pill doesn't nag the user during the earlier
          //      sections of the profile.
          //   2. When the pill scrolls them back to `historyYRef - 8`
          //      the new position is ~508 px above the visibility
          //      threshold, so the pill hides on its own and
          //      re-appears correctly the moment the user scrolls
          //      back down past the threshold — no gate/lock needed.
          const hy = historyYRef.current;
          const threshold = hy > 0 ? hy + 500 : 1200;
          setShowTopBtn(y > threshold);
        }}
        // 16ms throttle is enough to persist the offset without
        // adding perceptible lag to the scroll gesture.
        scrollEventThrottle={16}
        onContentSizeChange={() => {
          // If we're in the middle of a scroll restoration and the
          // content resized (avatar image loaded, history data swapped
          // in, etc.), immediately re-apply the target offset. Without
          // this the ScrollView silently snaps back to top when its
          // content grows/shrinks under us. Also respect the user's
          // manual scroll — if they're already dragging, abort.
          if (userInteractedRef.current) return;
          const target = pendingScrollYRef.current;
          if (target != null) {
            scrollRef.current?.scrollTo({ y: target, animated: false });
          }
        }}
      >
        <View style={styles.header}>
          <View style={styles.headerRow}>
            {/* Immediate avatar source that survives the async
                `myPhotos()` roundtrip: use `user.primary_photo`
                hydrated by /auth/me on the very first render. This
                eliminates the "empty circle → initials → real photo"
                flash the user was seeing when opening the profile
                page. */}
            {(() => {
              const authPhoto = user?.primary_photo?.data
                ? `data:${user.primary_photo.mime || "image/jpeg"};base64,${user.primary_photo.data}`
                : null;
              const avatarSrc = primaryPhotoUri
                || (primaryPhotoData ? `data:image/jpeg;base64,${primaryPhotoData}` : null)
                || authPhoto;

              const inner = avatarSrc ? (
                <ExpoImage source={{ uri: avatarSrc }} style={styles.avatarImg} contentFit="cover" cachePolicy="memory-disk" />
              ) : (
                <View style={[styles.avatarImg, styles.avatarPlaceholder]}>
                  <Ionicons name="person" size={40} color={colors.brandSecondary} />
                </View>
              );

              return isAnonymous ? (
                <View style={styles.avatarWrap}>{inner}</View>
              ) : (
                <Pressable onPress={openProfileEdit} testID="profile-avatar" style={styles.avatarWrap}>
                  {inner}
                  <View style={styles.avatarEditBadge}>
                    <Ionicons name="camera" size={12} color={colors.onBrandPrimary} />
                  </View>
                </Pressable>
              );
            })()}
            <View style={{ flex: 1 }}>
              <Text style={styles.nickname} testID="profile-nickname" numberOfLines={1} ellipsizeMode="tail">
                @{(user.nickname || "").replace(/\s+/g, "")}
              </Text>
              {user.display_name ? (
                <Text style={styles.displayName} testID="profile-display-name">
                  {user.display_name}
                </Text>
              ) : null}
              {!isAnonymous ? (
                <Pressable
                  onPress={() =>
                    router.push({
                      pathname: "/circle/[userId]",
                      params: { userId: user.user_id, from: "/profile" },
                    })
                  }
                  style={styles.circleChip}
                  testID="profile-circle-open"
                  hitSlop={6}
                >
                  <Ionicons name="people" size={14} color={colors.onBrandSecondary} />
                  <Text style={styles.circleChipTxt}>Cerchia del gossip</Text>
                </Pressable>
              ) : null}
            </View>
          </View>
          {!isAnonymous && user.bio ? <Text style={styles.headerBio} testID="profile-bio">{user.bio}</Text> : null}
          {isAnonymous ? (
            <View style={styles.anonBanner} testID="anon-banner">
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Ionicons name="lock-closed-outline" size={16} color={colors.brandSecondary} />
                <Text style={styles.anonBannerTxt}>PROFILO BLOCCATO</Text>
              </View>
              <Text style={styles.anonBannerBody}>
                Come utente anonimo non puoi aggiungere foto, descrizione o link.
                Registrati con un account per personalizzare tutto.
              </Text>
              <Pressable
                onPress={doLogout}
                testID="anon-register-btn"
                style={styles.anonRegisterBtn}
              >
                <Text style={styles.anonRegisterTxt}>REGISTRATI ORA ›</Text>
              </Pressable>
            </View>
          ) : (
            <Pressable onPress={openProfileEdit} testID="profile-edit-button" style={styles.headerEditBtn}>
              <Ionicons name="pencil" size={14} color={colors.brandSecondary} />
              <Text style={styles.headerEditTxt}>MODIFICA PROFILO</Text>
            </Pressable>
          )}
        </View>

        <Pressable
          style={styles.badgeBlock}
          testID="profile-badge"
          onPress={() => setBadgesOpen(true)}
          accessibilityRole="button"
          accessibilityLabel="Apri la collezione spille"
        >
          <View style={[
            styles.badgeIcon,
            badgeUnlocked && (badgeType === "bastian_contrario"
              ? styles.badgeIconUnlockedRed
              : styles.badgeIconUnlockedYellow),
          ]}>
            {badgeUnlocked ? (
              // Emoji-based badge art. Sits inside a rounded inner disc
              // so it reads as a proper "coin" no matter the accent
              // colour of the outer ring. Coherent with the tier-card
              // look in the full badge shelf.
              <View style={[
                styles.badgeInnerDisc,
                badgeType === "bastian_contrario" && styles.badgeInnerDiscRed,
                badgeType === "buon_senso" && styles.badgeInnerDiscYellow,
              ]}>
                <Text style={styles.badgeEmoji}>
                  {badgeType === "bastian_contrario" ? "🎭" : "⚖️"}
                </Text>
              </View>
            ) : (
              <Ionicons
                name="lock-closed"
                size={54}
                color={colors.muted}
              />
            )}
          </View>
          <Text style={styles.badgeTitle}>
            {badgeUnlocked
              ? badgeType === "bastian_contrario" ? "BASTIAN CONTRARIO" : "BUON SENSO"
              : "SPILLA BLOCCATA"}
          </Text>
          <Text style={styles.badgeSubtitle}>
            {badgeUnlocked
              ? `Maggioranza ${badge?.majority ?? 0} · Minoranza ${badge?.minority ?? 0}`
              : `Progresso ${badge?.progress ?? 0}/${badge?.target ?? 5} voti`}
          </Text>
          {/* Small affordance hint so users know the block is tappable
              and leads to the full collection. Kept intentionally low-key
              so it doesn't compete with the main badge visual. */}
          <View style={styles.badgeMoreHint}>
            <Ionicons name="ribbon-outline" size={14} color={colors.brandPrimary} />
            <Text style={styles.badgeMoreHintTxt}>VEDI TUTTE LE SPILLE</Text>
            <Ionicons name="chevron-forward" size={14} color={colors.brandPrimary} />
          </View>
        </Pressable>

        <View style={styles.statsRow}>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{user.total_votes}</Text>
            <Text style={styles.statLabel}>VOTI</Text>
          </View>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{user.majority_votes}</Text>
            <Text style={styles.statLabel}>MAGGIORANZA</Text>
          </View>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{user.minority_votes}</Text>
            <Text style={styles.statLabel}>MINORANZA</Text>
          </View>
        </View>

        {!isAnonymous && (
          <View style={styles.prefsSection} testID="prefs-section">
            <Pressable
              onPress={() => setPrefsExpanded((v) => !v)}
              testID="prefs-section-toggle"
              style={styles.prefsHeadRow}
            >
              <Text style={styles.prefsTitle}>ARGOMENTI PREFERITI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{user.favorite_categories?.length ?? 0}</Text>
                <Ionicons name={prefsExpanded ? "chevron-up" : "chevron-down"} size={20} color={colors.onSurface} />
              </View>
            </Pressable>
            {prefsExpanded && (
              <View style={styles.prefsBody} testID="prefs-body">
                <View style={styles.prefsChipsRow}>
                  {(user.favorite_categories && user.favorite_categories.length > 0) ? (
                    user.favorite_categories.map((id) => {
                      const label = cats.find((c) => c.id === id)?.label || id;
                      return (
                        <View key={id} style={styles.prefChip} testID={`pref-chip-${id}`}>
                          <Text style={styles.prefChipTxt}>{label}</Text>
                        </View>
                      );
                    })
                  ) : (
                    <Text style={styles.prefEmpty}>Nessuna preferenza impostata.</Text>
                  )}
                </View>
                <Pressable onPress={openPrefs} testID="prefs-edit-button" style={styles.prefsEditBtnFull}>
                  <Ionicons name="pencil" size={14} color={colors.brandSecondary} />
                  <Text style={styles.prefsEditTxt}>MODIFICA</Text>
                </Pressable>
              </View>
            )}
          </View>
        )}

        {!isAnonymous && (
          <View style={styles.prefsSection} testID="profession-section">
            <Pressable
              onPress={() => setProfessionOpen(true)}
              testID="profession-open"
              style={styles.prefsHeadRow}
            >
              <View style={styles.sectionIcon}>
                <Ionicons name="briefcase" size={18} color={colors.brandSecondary} />
              </View>
              <Text style={[styles.prefsTitle, { flex: 1 }]}>PROFESSIONE</Text>
              <View style={styles.sectionHeadRight}>
                <Text
                  style={[styles.professionValue, !user.profession && { color: colors.muted }]}
                  numberOfLines={1}
                >
                  {user.profession || "Non impostata"}
                </Text>
                <Ionicons name="chevron-forward" size={20} color={colors.muted} />
              </View>
            </Pressable>
          </View>
        )}

        {!isAnonymous && (
          <View style={styles.prefsSection} testID="blocked-section">
            <Pressable
              onPress={async () => {
                const willOpen = !blocksOpen;
                setBlocksOpen(willOpen);
                if (willOpen) await loadBlocked();
              }}
              testID="blocked-toggle"
              style={styles.prefsHeadRow}
            >
              <View style={styles.sectionIcon}>
                <Ionicons name="person-remove" size={18} color={colors.brandSecondary} />
              </View>
              <Text style={[styles.prefsTitle, { flex: 1 }]}>UTENTI BLOCCATI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{blockedList.length}</Text>
                <Ionicons name={blocksOpen ? "chevron-up" : "chevron-down"} size={20} color={colors.muted} />
              </View>
            </Pressable>
            {blocksOpen ? (<View style={{ marginTop: spacing.sm, gap: spacing.sm }}>
                {loadingBlocks ? (
                  <ActivityIndicator color={colors.brandPrimary} />
                ) : blockedList.length === 0 ? (
                  <Text style={styles.blockedEmpty}>Non hai bloccato nessun utente.</Text>
                ) : (
                  blockedList.map((u) => (
                    <View key={u.user_id} style={styles.blockedRow} testID={`blocked-row-${u.user_id}`}>
                      <Pressable
                        onPress={() => router.push(`/user/${u.user_id}`)}
                        style={{ flex: 1 }}
                        hitSlop={4}
                      >
                        <Text style={styles.blockedNick}>@{u.nickname || "utente"}</Text>
                        {u.display_name ? (
                          <Text style={styles.blockedSub}>{u.display_name}</Text>
                        ) : null}
                      </Pressable>
                      <Pressable
                        onPress={() => unblockOne(u.user_id)}
                        testID={`blocked-unblock-${u.user_id}`}
                        style={styles.unblockBtn}
                      >
                        <Text style={styles.unblockTxt}>SBLOCCA</Text>
                      </Pressable>
                    </View>
                  ))
                )}
              </View>
            ) : null}
          </View>
        )}

        {/* Story privacy shortcut — takes the user to the dynamic
            audience roster where they can silence individual followers.
            Hidden for anonymous accounts (they can't publish stories). */}
        {!isAnonymous && (
          <Pressable
            style={styles.storyPrivacyRow}
            onPress={() => router.push("/stories/hidden_viewers" as any)}
            testID="stories-privacy-open"
          >
            <View style={styles.storyPrivacyIcon}>
              <Ionicons name="eye-off-outline" size={20} color={colors.brandPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.storyPrivacyTitle}>CHI VEDE LE MIE STORIE</Text>
              <Text style={styles.storyPrivacySub}>Gestisci chi può vedere ciò che pubblichi</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.muted} />
          </Pressable>
        )}

        <View
          style={styles.historySection}
          testID="history-section"
          onLayout={(e) => {
            // Y offset of the STORICO VOTI section header inside the
            // profile ScrollView — used by the floating scroll-to-top
            // pill so it returns to the first history post rather than
            // all the way to the avatar at the top of the page.
            historyYRef.current = e.nativeEvent.layout.y;
          }}
        >
          <View style={styles.historyHeadRow}>
            <Pressable
              onPress={() => setHistoryExpanded((v) => !v)}
              testID="history-section-toggle"
              style={{ flex: 1, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}
            >
              <View style={styles.sectionIcon}>
                <Ionicons name="time" size={18} color={colors.brandSecondary} />
              </View>
              <Text style={[styles.historyTitle, { flex: 1, marginLeft: spacing.md }]}>STORICO VOTI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{user.total_votes ?? 0}</Text>
                <Ionicons name={historyExpanded ? "chevron-up" : "chevron-down"} size={20} color={colors.muted} />
              </View>
            </Pressable>
          </View>
          {historyExpanded && (
            <View testID="history-body">
              {/* Two independent privacy switches. `generic` covers strangers
                  browsing the public profile; `mutual` covers members of the
                  "cerchia bilaterale" (people in my circle who also have me
                  in theirs). Toggling one never affects the other so the
                  owner can, for example, keep the history visible to close
                  friends while hiding it from everyone else. */}
              <View style={styles.historyPrivacyBox} testID="history-privacy-box">
                <View style={styles.historyPrivacyRow}>
                  <View style={{ flex: 1, paddingRight: spacing.sm }}>
                    <Text style={styles.historyPrivacyTitle}>Visibile a tutti</Text>
                    <Text style={styles.historyPrivacyHint}>
                      Chiunque può vedere il tuo storico voti.
                    </Text>
                  </View>
                  <Switch
                    testID="hist-privacy-generic"
                    value={histPublicGeneric}
                    onValueChange={() => updateHistPrivacy("generic")}
                    trackColor={{ false: colors.border, true: colors.brandPrimary }}
                    thumbColor="#FFFFFF"
                  />
                </View>
                <View style={styles.historyPrivacyDivider} />
                <View style={styles.historyPrivacyRow}>
                  <View style={{ flex: 1, paddingRight: spacing.sm }}>
                    <Text style={styles.historyPrivacyTitle}>Visibile alla cerchia bilaterale</Text>
                    <Text style={styles.historyPrivacyHint}>
                      Chi hai nella cerchia e ti ha nella sua può vedere lo storico.
                    </Text>
                  </View>
                  <Switch
                    testID="hist-privacy-mutual"
                    value={histPublicMutual}
                    onValueChange={() => updateHistPrivacy("mutual")}
                    trackColor={{ false: colors.border, true: colors.brandPrimary }}
                    thumbColor="#FFFFFF"
                  />
                </View>
              </View>
              <View style={styles.filterRow}>
                {(["all", "majority", "minority"] as Filter[]).map((f) => (
                  <Pressable
                    key={f}
                    onPress={() => setFilter(f)}
                    testID={`filter-${f}`}
                    // Selected chip: dark filled ("recessed") background so
                    // it clearly stands apart from the card wrapper, with
                    // yellow border + yellow bold label. Team colours are
                    // intentionally NOT used here — they'd clash with the
                    // vote-side red/yellow further down the list.
                    style={[
                      styles.filterChip,
                      filter === f && styles.filterChipActive,
                    ]}
                  >
                    <Text style={[styles.filterTxt, filter === f && styles.filterTxtActive]}>
                      {f === "all" ? "TUTTI" : f === "majority" ? "MAGGIORANZA" : "MINORANZA"}
                    </Text>
                  </Pressable>
                ))}
              </View>

              {loadingH ? (
                <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
              ) : history.length === 0 ? (
                <Text style={styles.emptyH}>Nessun voto in questa categoria.</Text>
              ) : (
                <View style={styles.historyList}>
                  {history.map((h) => {
                    const votedName = h.side_voted === "A" ? h.party_a : h.party_b;
                    return (
                      <Pressable
                        key={h.feud_id + h.voted_at}
                        style={styles.historyItem}
                        onPress={() => {
                          // Arm scroll-restoration BEFORE navigating.
                          // Uses module-scope memory so it survives
                          // if the tabs navigator unmounts this
                          // component during the round-trip.
                          scrollMemory.markRestore(SCROLL_KEY);
                          // Pass `?from=/profile` so the feud
                          // detail's back button navigates back HERE
                          // instead of falling through to "/".
                          // Expo Router tabs don't grow a real
                          // back-stack when jumping between hidden
                          // href:null routes, so we rely on the
                          // explicit `from` hint the feud screen
                          // already understands.
                          router.push({
                            pathname: "/feud/[id]" as any,
                            params: { id: h.feud_id, from: "/profile" },
                          });
                        }}
                        testID={`history-${h.feud_id}`}
                      >
                        <View style={[styles.sideBar, { backgroundColor: sideColor(h.side_voted) }]} />
                        <View style={{ flex: 1, padding: spacing.sm }}>
                          <Text style={styles.hCat}>{(h.category_label || h.category || "").toString().toUpperCase()}</Text>
                          <Text style={styles.hTitle} numberOfLines={2}>{h.title}</Text>
                          <View style={styles.hMetaRow}>
                            <Text
                              style={[styles.hVoted, { color: sideColor(h.side_voted) }]}
                              numberOfLines={1}
                              ellipsizeMode="tail"
                            >
                              Hai votato: {votedName}
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
            </View>
          )}
        </View>

        <View style={styles.pushRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.pushLabel}>NOTIFICHE PUSH</Text>
            <Text style={styles.pushHint}>Riceverai avvisi sul telefono per faide calde, ribaltamenti e risposte.</Text>
          </View>
          <Switch
            testID="push-toggle"
            value={pushEnabled}
            onValueChange={async (v) => {
              setPushEnabled(v); // optimistic
              try { await api.togglePush(v); } catch { setPushEnabled(!v); }
              if (v && Platform.OS !== 'web') {
                // Immediately register so the switch has an effect on-device.
                try {
                  const { registerForPush } = await import('@/src/notifications/push');
                  await registerForPush();
                } catch { /* silent */ }
              }
            }}
            trackColor={{ false: colors.border, true: colors.brandPrimary }}
            thumbColor={pushEnabled ? colors.onBrandPrimary : colors.muted}
          />
        </View>

        <Pressable
          style={styles.supportBtn}
          onPress={() => router.push("/support")}
          testID="profile-support"
        >
          <Ionicons name="help-circle-outline" size={20} color={colors.brandSecondary} />
          <Text style={styles.supportTxt}>RICHIEDI ASSISTENZA</Text>
          <Ionicons name="chevron-forward" size={18} color={colors.brandSecondary} />
        </Pressable>

        <Pressable
          style={styles.logout}
          onPress={doLogout}
          testID="profile-logout"
        >
          <Text style={styles.logoutText}>ESCI</Text>
        </Pressable>

        {/* Admin link — only rendered for the app owner. Any other
            account never even sees this entry point. */}
        {(user?.email || "").toLowerCase() === "carlofarinapayme@gmail.com" ? (
          <Pressable
            style={styles.adminLink}
            onPress={() => router.push("/admin")}
            testID="profile-admin-link"
          >
            <Ionicons name="shield-checkmark-outline" size={14} color={colors.muted} />
            <Text style={styles.adminLinkTxt}>PANNELLO ADMIN</Text>
          </Pressable>
        ) : null}
      </ScrollView>

      <ScrollToTopButton
        visible={showTopBtn}
        onPress={() => {
          // Bring the user to the START of the STORICO VOTI section
          // (header + first history rows visible at the top of the
          // viewport). The pill's visibility threshold is
          // `historyYRef.current + 500`, so this target (~ historyYRef
          // - 8) is ~500 px above the threshold and the pill hides
          // naturally without any gate.
          const target = Math.max(0, historyYRef.current - 8);
          scrollRef.current?.scrollTo({ y: target, animated: true });
          // Safety net: on very tall content the animated scroll can
          // undershoot — snap to the exact target after the animation
          // has had time to run so the user really lands on the
          // section start.
          setTimeout(() => {
            try {
              scrollRef.current?.scrollTo({ y: target, animated: false });
            } catch { /* noop */ }
          }, 750);
        }}
        testID="profile-scroll-top"
      />

      <PrefsModal
        visible={prefsOpen}
        onClose={() => setPrefsOpen(false)}
        categories={cats}
        selected={editSel}
        onToggleOne={toggleEdit}
        onToggleAll={toggleAllEdit}
        allSelected={allSelected}
        onSave={savePrefs}
        saving={savingPrefs}
        error={prefsError}
      />

      <EditProfileModal
        visible={profileOpen}
        onClose={() => setProfileOpen(false)}
        nickname={editNick}
        onNicknameChange={setEditNick}
        displayName={editDisplay}
        onDisplayNameChange={setEditDisplay}
        photos={photos}
        loadingPhotos={loadingPhotos}
        photoUris={photoUris}
        primaryPhotoId={user.primary_photo_id}
        onSetPrimary={setPrimary}
        onRecropPhoto={recropPhoto}
        openingRecropId={openingRecrop}
        onDeletePhoto={deletePhoto}
        onPickPhoto={pickPhoto}
        bio={bio}
        onBioChange={setBio}
        socials={socials}
        onSocialsChange={setSocials}
        saving={savingDetails}
        onSave={saveDetails}
        error={detailsError}
      />

      <PhotoCropper
        visible={cropperOpen}
        uri={cropperUri}
        originalWidth={cropperSize?.w}
        originalHeight={cropperSize?.h}
        onCancel={() => { setCropperOpen(false); setCropperUri(null); setCropperSize(null); setCropperReplaceId(null); setCropperOriginalSourceUri(null); }}
        onConfirm={uploadCroppedPhoto}
      />

      <ProfessionModal
        visible={professionOpen}
        onClose={() => setProfessionOpen(false)}
        professions={professionsList}
        currentValue={user.profession}
        saving={savingProfession}
        onSelect={saveProfession}
      />

      {/* Category badges collection — full-screen shelf reachable by
          tapping the primary alignment badge above. Rendered here so
          it can overlay the whole profile scroll view regardless of
          which modal (photo cropper, prefs, edit) is currently open. */}
      <CategoryBadgesModal
        visible={badgesOpen}
        userId={user.user_id}
        displayName={user.display_name || user.nickname || undefined}
        extraTotal={1}
        extraUnlocked={badgeUnlocked ? 1 : 0}
        onClose={() => setBadgesOpen(false)}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  content: { paddingBottom: spacing.xxxl },
  header: { padding: spacing.lg, backgroundColor: colors.surfaceInverse, gap: spacing.md },
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  avatarWrap: { position: "relative" },
  avatarImg: { width: 80, height: 80, borderRadius: 40, borderWidth: 0, overflow: "hidden", backgroundColor: colors.surfaceInverse },
  avatarPlaceholder: { alignItems: "center", justifyContent: "center" },
  avatarEditBadge: { position: "absolute", right: -2, bottom: -2, width: 24, height: 24, borderRadius: 12, backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.surface, alignItems: "center", justifyContent: "center" },
  headerBio: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 20, borderLeftWidth: 2, borderColor: colors.brandSecondary, paddingLeft: spacing.sm },
  headerEditBtn: { flexDirection: "row", alignItems: "center", gap: 8, alignSelf: "flex-start", borderWidth: 1.5, borderColor: colors.brandSecondary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  headerEditTxt: { fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "800", color: colors.brandSecondary },
  anonBanner: { borderWidth: 2, borderColor: colors.brandSecondary, padding: spacing.md, gap: spacing.sm, backgroundColor: "rgba(255,230,0,0.08)" },
  anonBannerTxt: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  anonBannerBody: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, lineHeight: 18 },
  anonRegisterBtn: { alignSelf: "flex-start", backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.brandSecondary, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  anonRegisterTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
  anonLockScreen: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md, backgroundColor: colors.surface },
  anonLockCircle: { width: 140, height: 140, borderRadius: 70, borderWidth: 0, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceSecondary, marginBottom: spacing.sm, overflow: "hidden" },
  anonLockTitle: { fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "800", color: colors.onSurface, textAlign: "center" },
  anonLockSubtitle: { fontSize: font.sizes.base, color: colors.brandSecondary, letterSpacing: 1, marginTop: -spacing.xs, fontWeight: "700" },
  anonLockBody: { fontSize: font.sizes.sm, color: colors.muted, textAlign: "center", lineHeight: 20, paddingHorizontal: spacing.md },
  // Refreshed CTA to match the rest of the rounded design system:
  // • pill-shaped (`radius.pill`)
  // • no aggressive white border
  // • slightly smaller/less shouty label
  anonLockCta: {
    marginTop: spacing.lg,
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.sm + 4,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  anonLockCtaTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.base,
    letterSpacing: 2,
    fontWeight: "800",
  },
  anonLockLogout: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: spacing.md, paddingVertical: spacing.xs },
  anonLockLogoutTxt: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 1 },
  editSectionTitle: { fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500", color: colors.brandPrimary },
  photosGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xs },
  photoBox: { width: 90, height: 90, borderWidth: 2, borderColor: colors.border, position: "relative", overflow: "hidden", backgroundColor: colors.surfaceSecondary },
  photoImg: { width: "100%", height: "100%" },
  primaryBadge: { position: "absolute", top: 4, left: 4, width: 20, height: 20, backgroundColor: colors.brandSecondary, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border },
  photoActions: { position: "absolute", bottom: 4, right: 4, flexDirection: "row", gap: 4 },
  photoAct: { width: 26, height: 26, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  photoAdd: { alignItems: "center", justifyContent: "center", gap: 4, backgroundColor: colors.surfaceSecondary, borderStyle: "dashed" },
  photoAddTxt: { fontSize: 10, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  bioInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, minHeight: 90, fontSize: font.sizes.base, color: colors.onSurface, backgroundColor: colors.surfaceSecondary, textAlignVertical: "top" },
  socialField: { gap: 4 },
  socialFieldLabel: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted },
  socialInput: { borderWidth: 2, borderColor: colors.border, padding: spacing.sm, fontSize: font.sizes.base, color: colors.onSurface, backgroundColor: colors.surfaceSecondary },
  brand: { color: colors.onSurface, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  nickname: { color: colors.brandSecondary, fontSize: font.sizes.xxl, fontWeight: "500" },
  provider: { color: colors.onSurface, fontSize: font.sizes.sm, opacity: 0.7, marginTop: spacing.xs },
  displayName: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    opacity: 0.75,
    marginTop: 2,
  },
  circleChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    marginTop: 8,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    backgroundColor: colors.brandSecondary,
  },
  circleChipTxt: { color: colors.onBrandSecondary, fontSize: font.sizes.xs, fontWeight: "700", letterSpacing: 0.5 },
  badgeBlock: {
    alignItems: "center",
    padding: spacing.xl,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    marginBottom: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
  },
  badgeIcon: {
    width: 140,
    height: 140,
    borderRadius: 70,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    position: "relative",
    overflow: "hidden",
  },
  // Unlocked variants — a colored ring hints at the badge type.
  badgeIconUnlockedRed: {
    borderColor: colors.brandPrimary,
    backgroundColor: colors.surfaceSecondary,
  },
  badgeIconUnlockedYellow: {
    borderColor: colors.brandSecondary,
    backgroundColor: colors.surfaceSecondary,
  },
  badgeInnerDisc: {
    width: 108,
    height: 108,
    borderRadius: 54,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  badgeInnerDiscRed: {
    // subtle red-tinted core so the coin doesn't feel flat
    backgroundColor: "rgba(255,69,58,0.10)",
    borderColor: "rgba(255,69,58,0.35)",
  },
  badgeInnerDiscYellow: {
    backgroundColor: "rgba(255,199,0,0.10)",
    borderColor: "rgba(255,199,0,0.40)",
  },
  badgeEmoji: {
    fontSize: 60,
    lineHeight: 72,
    textAlign: "center",
    textShadowColor: "rgba(0,0,0,0.35)",
    textShadowOffset: { width: 0, height: 2 },
    textShadowRadius: 4,
  },
  badgeTitle: { fontSize: font.sizes.xxl, letterSpacing: 1.5, fontWeight: "800", color: colors.onSurface, marginTop: spacing.md },
  badgeSubtitle: { fontSize: font.sizes.base, color: colors.muted, marginTop: spacing.xs },
  badgeMoreHint: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    borderWidth: 1.5,
    borderColor: colors.brandPrimary,
    borderRadius: 999,
  },
  badgeMoreHintTxt: {
    color: colors.brandPrimary,
    fontSize: font.sizes.sm,
    fontWeight: "800",
    letterSpacing: 1.2,
  },
  statsRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, marginBottom: spacing.md },
  statBox: {
    flex: 1,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.sm,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
  },
  statValue: { fontSize: font.sizes.xxxl, fontWeight: "800", color: colors.onSurface },
  statLabel: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1, marginTop: 4, fontWeight: "700" },
  historyHeader: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  historyTitle: { fontSize: font.sizes.xl, letterSpacing: 1.2, fontWeight: "800", color: colors.onSurface },
  historySection: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
  },
  historyHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: spacing.xs },
  sectionHeadRight: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sectionCountBadge: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 1, minWidth: 20, textAlign: "right" },
  filterRow: { flexDirection: "row", gap: spacing.sm, paddingBottom: spacing.md },
  historyPrivacyBox: {
    marginBottom: spacing.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
  },
  historyPrivacyRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: spacing.sm,
  },
  historyPrivacyDivider: { height: 1, backgroundColor: colors.border, marginHorizontal: -spacing.sm },
  historyPrivacyTitle: { color: colors.onSurface, fontSize: font.sizes.sm, fontWeight: "600" },
  historyPrivacyHint: { color: colors.muted, fontSize: font.sizes.xs, marginTop: 2, lineHeight: 14 },
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
    // Same "pressed" look on all three (TUTTI/MAGGIORANZA/MINORANZA):
    // dark bg + yellow border + yellow bold text. No team colours here.
    backgroundColor: colors.surface,
    borderColor: colors.brandSecondary,
  },
  filterTxt: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.muted, fontWeight: "800" },
  filterTxtActive: { color: colors.brandSecondary },
  center: { padding: spacing.xl, alignItems: "center" },
  emptyH: { paddingHorizontal: spacing.lg, paddingVertical: spacing.xl, color: colors.muted, fontSize: font.sizes.base },
  historyList: { gap: spacing.sm },
  historyItem: {
    flexDirection: "row",
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
    overflow: "hidden",
  },
  sideBar: { width: 4 },
  hCat: { fontSize: font.sizes.xs, letterSpacing: 1.5, color: colors.muted, fontWeight: "700" },
  hTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 4, lineHeight: 20, fontWeight: "700" },
  // Row keeps the "Hai votato: X" label on the LEFT and the MAGGIORANZA /
  // MINORANZA badge anchored on the RIGHT. `flexWrap: wrap` used to be
  // enabled here — that caused the badge to drop to a second line and
  // reflow to the LEFT when the votedName was too long, which is exactly
  // the bug the user reported. We now shrink the label instead so the
  // badge is always pinned to the right edge.
  hMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.xs, gap: spacing.sm },
  // Left label — shrinks and truncates so the right-anchored badge
  // never wraps to the next line and jumps to the left.
  hVoted: { fontSize: font.sizes.sm, fontWeight: "500", flexShrink: 1, flex: 1 },
  hBadge: { fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1, borderRadius: radius.sm, fontWeight: "800" },
  // Neutral outline for MAGGIORANZA/MINORANZA badges: keeping them
  // team-coloured (red/yellow) collided with the vote colours used on
  // poll splits and filter chips. A muted grey outline reads clearly
  // without competing for meaning.
  hBadgeMaj: { backgroundColor: "transparent", borderColor: colors.borderStrong, color: colors.muted },
  hBadgeMin: { backgroundColor: "transparent", borderColor: colors.borderStrong, color: colors.muted },
  logout: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    borderRadius: radius.md,
    padding: spacing.md,
    alignItems: "center",
    backgroundColor: colors.brandPrimary,
  },
  pushRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
  },
  pushLabel: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1.5, fontWeight: "800" },
  pushHint: { color: colors.onSurfaceInverse, fontSize: font.sizes.xs, marginTop: 4, opacity: 0.7 },
  supportBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    padding: spacing.md,
    borderWidth: 1.5,
    borderColor: colors.brandSecondary,
    borderRadius: radius.md,
    backgroundColor: "transparent",
  },
  supportTxt: { flex: 1, color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 1.5, fontWeight: "800" },
  logoutText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 1.5, fontWeight: "800" },
  adminLink: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: spacing.md, marginBottom: spacing.lg },
  adminLinkTxt: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted, fontWeight: "500" },
  prefsSection: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    gap: spacing.sm,
  },
  blockedEmpty: { color: colors.muted, fontSize: font.sizes.sm, fontStyle: "italic" },
  storyPrivacyRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
  },
  storyPrivacyIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  storyPrivacyTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
    letterSpacing: 1.2,
  },
  storyPrivacySub: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 2,
  },
  blockedRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
  },
  blockedNick: { color: colors.onSurface, fontSize: font.sizes.base, fontWeight: "600" },
  blockedSub: { color: colors.muted, fontSize: font.sizes.sm, marginTop: 2 },
  unblockBtn: {
    borderWidth: 2,
    borderColor: colors.brandPrimary,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  unblockTxt: { color: colors.brandPrimary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "700" },
  prefsHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: spacing.md, paddingVertical: spacing.xs },
  sectionIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  prefsBody: { gap: spacing.sm, marginTop: spacing.xs },
  prefsTitle: { fontSize: font.sizes.xl, letterSpacing: 1.2, fontWeight: "800", color: colors.onSurface },
  prefsEditBtn: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1.5, borderColor: colors.brandSecondary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: 8, backgroundColor: "transparent" },
  prefsEditBtnFull: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderWidth: 1.5, borderColor: colors.brandSecondary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: "transparent", alignSelf: "flex-start" },
  prefsEditTxt: { fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "800", color: colors.brandSecondary },
  prefsChipsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xs },
  // Read-only chip shown on the profile summary. Represents a category the
  // user has already picked as a favourite → styled like the "selected"
  // state of the edit-modal chips (red fill, fully rounded).
  prefChip: {
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  prefChipTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 0.5, fontWeight: "700" },
  prefEmpty: { fontSize: font.sizes.base, color: colors.muted },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
  modalSheetTall: { height: "92%", maxHeight: "92%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  modalTitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  modalBody: { padding: spacing.lg, gap: spacing.md },
  prefsErr: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  saveErrorBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.error,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  saveErrorTxt: { flex: 1, color: "#FFFFFF", fontSize: font.sizes.sm, fontWeight: "600" },
  prefsSaveBtn: { backgroundColor: colors.brandPrimary, borderTopWidth: 2, borderColor: colors.border, paddingVertical: spacing.lg, alignItems: "center" },
  prefsSaveTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  // Profession row
  professionValue: {
    fontSize: font.sizes.base,
    color: colors.onSurface,
    maxWidth: 180,
  },
  professionItem: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  professionItemOn: { backgroundColor: colors.brandPrimary },
  professionItemTxt: { fontSize: font.sizes.lg, color: colors.onSurface },
  professionItemTxtOn: { color: colors.onBrandPrimary, fontWeight: "500" },
  savingBar: { paddingVertical: spacing.sm, alignItems: "center", backgroundColor: colors.surfaceSecondary },
});
