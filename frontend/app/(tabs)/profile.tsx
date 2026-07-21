import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Modal, TextInput, Image, KeyboardAvoidingView, Platform, Switch, Alert } from "react-native";
import * as ImagePicker from "expo-image-picker";
import * as ImageManipulator from "expo-image-manipulator";
import * as FileSystem from "expo-file-system/legacy";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { api, HistoryItem, UserPhoto } from "@/src/api";
import { colors, spacing, font, sideColor } from "@/src/theme";
import PhotoCropper from "@/src/components/PhotoCropper";
import { sanitizeNicknameInput, validateNickname, NICKNAME_HINT, NICKNAME_MAX } from "@/src/utils/nickname";

/**
 * Module-level cache mapping (photo_id + short data hash) → local file URI.
 *
 * Rendering multiple `data:image/jpeg;base64,<huge>` sources back-to-back in
 * the same view can push RN's Image loader into an out-of-memory state on
 * older devices — the error the user sees as a red-screen after saving many
 * photos. Writing each photo to disk once and referencing it by a file:// URI
 * lets the OS stream & cache the bitmap without holding the full base64 in
 * memory.
 */
const photoUriCache: Map<string, string> = new Map();
function _photoHash(data: string): string {
  // Cheap fingerprint over the payload so a re-crop with different content
  // busts the cache automatically.
  let h = 0;
  const step = Math.max(1, Math.floor(data.length / 128));
  for (let i = 0; i < data.length; i += step) {
    h = (h * 31 + data.charCodeAt(i)) | 0;
  }
  return `${data.length}_${(h >>> 0).toString(36)}`;
}
async function resolvePhotoUri(photo: UserPhoto): Promise<string> {
  const key = `${photo.photo_id}_${_photoHash(photo.data)}`;
  const hit = photoUriCache.get(key);
  if (hit) return hit;
  if (Platform.OS === "web") {
    const uri = `data:image/jpeg;base64,${photo.data}`;
    photoUriCache.set(key, uri);
    return uri;
  }
  try {
    const dir = (FileSystem as any).cacheDirectory || (FileSystem as any).documentDirectory;
    const safe = photo.photo_id.replace(/[^a-zA-Z0-9_]/g, "_");
    const uri = `${dir}profphoto_${safe}.jpg`;
    await FileSystem.writeAsStringAsync(uri, photo.data, {
      encoding: FileSystem.EncodingType.Base64,
    });
    photoUriCache.set(key, uri);
    return uri;
  } catch {
    // Fallback to data URI if FS write fails — still shows the picture.
    const uri = `data:image/jpeg;base64,${photo.data}`;
    photoUriCache.set(key, uri);
    return uri;
  }
}

type Filter = "all" | "majority" | "minority";
type Socials = { instagram: string; tiktok: string; twitter: string; youtube: string; website: string };
const EMPTY_SOCIALS: Socials = { instagram: "", tiktok: "", twitter: "", youtube: "", website: "" };
const SOCIAL_KEYS: (keyof Socials)[] = ["instagram", "tiktok", "twitter", "youtube", "website"];
const SOCIAL_LABELS: Record<keyof Socials, string> = {
  instagram: "Instagram", tiktok: "TikTok", twitter: "X (Twitter)", youtube: "YouTube", website: "Sito web",
};

export default function Profile() {
  const { user, logout, refreshMe } = useAuth();
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("all");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loadingH, setLoadingH] = useState(false);
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

  const loadHistory = useCallback(async (f: Filter) => {
    setLoadingH(true);
    try {
      const r = await api.history(f);
      setHistory(r.history);
    } finally { setLoadingH(false); }
  }, []);

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
      if (historyExpanded) loadHistory(filter);
    }, [refreshMe, historyExpanded, loadHistory, filter])
  );

  useEffect(() => {
    if (historyExpanded) loadHistory(filter);
  }, [historyExpanded, filter, loadHistory]);

  // Auto-refresh the vote history every 30s while the section is expanded.
  // The per-vote `aligned` badge is recomputed by the backend on every call
  // against the CURRENT feud vote counts, so this keeps the "MAGGIORANZA /
  // MINORANZA" labels in sync with real-time majority flips caused by other
  // users voting after us. Cleanup on collapse/unmount avoids leaks.
  useEffect(() => {
    if (!historyExpanded) return;
    const t = setInterval(() => { loadHistory(filter); }, 30000);
    return () => clearInterval(t);
  }, [historyExpanded, filter, loadHistory]);

  useEffect(() => {
    (async () => {
      try {
        const c = await api.categories();
        setCats(c.categories);
      } catch {}
      try {
        const p = await api.professions();
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
    } finally { setLoadingPhotos(false); }
  }, []);

  useEffect(() => {
    loadPhotos();
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
            onPress={async () => { await logout(); router.replace("/auth"); }}
            testID="anon-register-btn"
            style={styles.anonLockCta}
          >
            <Text style={styles.anonLockCtaTxt}>REGISTRATI ORA  ›</Text>
          </Pressable>
          <Pressable
            onPress={async () => { await logout(); router.replace("/auth"); }}
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
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <View style={styles.headerRow}>
            {isAnonymous ? (
              <View style={styles.avatarWrap}>
                {primaryPhotoUri ? (
                  <Image source={{ uri: primaryPhotoUri }} style={styles.avatarImg} />
                ) : primaryPhotoData ? (
                  <Image source={{ uri: `data:image/jpeg;base64,${primaryPhotoData}` }} style={styles.avatarImg} />
                ) : (
                  <View style={[styles.avatarImg, styles.avatarPlaceholder]}>
                    <Ionicons name="person" size={40} color={colors.brandSecondary} />
                  </View>
                )}
              </View>
            ) : (
              <Pressable onPress={openProfileEdit} testID="profile-avatar" style={styles.avatarWrap}>
                {primaryPhotoUri ? (
                  <Image source={{ uri: primaryPhotoUri }} style={styles.avatarImg} />
                ) : primaryPhotoData ? (
                  <Image source={{ uri: `data:image/jpeg;base64,${primaryPhotoData}` }} style={styles.avatarImg} />
                ) : (
                  <View style={[styles.avatarImg, styles.avatarPlaceholder]}>
                    <Ionicons name="person" size={40} color={colors.brandSecondary} />
                  </View>
                )}
                <View style={styles.avatarEditBadge}>
                  <Ionicons name="camera" size={12} color={colors.onBrandPrimary} />
                </View>
              </Pressable>
            )}
            <View style={{ flex: 1 }}>
              <Text style={styles.nickname} testID="profile-nickname">@{user.nickname}</Text>
              {user.display_name ? (
                <Text style={styles.displayName} testID="profile-display-name">
                  {user.display_name}
                </Text>
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
                onPress={async () => { await logout(); router.replace("/auth"); }}
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

        <View style={styles.badgeBlock} testID="profile-badge">
          <View style={[
            styles.badgeIcon,
            badgeUnlocked && badgeType === "bastian_contrario" && { backgroundColor: colors.brandPrimary },
            badgeUnlocked && badgeType === "buon_senso" && { backgroundColor: colors.brandSecondary },
          ]}>
            <Ionicons
              name={badgeUnlocked ? (badgeType === "bastian_contrario" ? "flash" : "shield-checkmark") : "lock-closed"}
              size={64}
              color={badgeUnlocked && badgeType === "buon_senso" ? colors.onBrandSecondary : colors.onBrandPrimary}
            />
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
        </View>

        <View style={styles.statsRow}>
          <View style={styles.statBox}>
            <Text style={styles.statValue}>{user.total_votes}</Text>
            <Text style={styles.statLabel}>VOTI</Text>
          </View>
          <View style={[styles.statBox, { borderLeftWidth: 2 }]}>
            <Text style={[styles.statValue, { color: colors.brandPrimary }]}>{user.majority_votes}</Text>
            <Text style={styles.statLabel}>MAGGIORANZA</Text>
          </View>
          <View style={[styles.statBox, { borderLeftWidth: 2, backgroundColor: colors.surfaceInverse }]}>
            <Text style={[styles.statValue, { color: colors.brandSecondary }]}>{user.minority_votes}</Text>
            <Text style={[styles.statLabel, { color: colors.brandSecondary }]}>MINORANZA</Text>
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
                  <Ionicons name="pencil" size={14} color={colors.onSurface} />
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
              <Text style={styles.prefsTitle}>PROFESSIONE</Text>
              <View style={styles.sectionHeadRight}>
                <Text
                  style={[styles.professionValue, !user.profession && { color: colors.muted }]}
                  numberOfLines={1}
                >
                  {user.profession || "Non impostata"}
                </Text>
                <Ionicons name="chevron-forward" size={20} color={colors.onSurface} />
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
              <Text style={styles.prefsTitle}>UTENTI BLOCCATI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{blockedList.length}</Text>
                <Ionicons name={blocksOpen ? "chevron-up" : "chevron-down"} size={20} color={colors.onSurface} />
              </View>
            </Pressable>
            {blocksOpen ? (
              <View style={{ marginTop: spacing.sm, gap: spacing.sm }}>
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

        <View style={styles.historySection} testID="history-section">
          <View style={styles.historyHeadRow}>
            <Pressable
              onPress={() => setHistoryExpanded((v) => !v)}
              testID="history-section-toggle"
              style={{ flex: 1, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}
            >
              <Text style={styles.historyTitle}>STORICO VOTI</Text>
              <View style={styles.sectionHeadRight}>
                <Text style={styles.sectionCountBadge}>{user.total_votes ?? 0}</Text>
                <Ionicons name={historyExpanded ? "chevron-up" : "chevron-down"} size={20} color={colors.onSurface} />
              </View>
            </Pressable>
            {historyExpanded && (
              <Pressable
                onPress={() => loadHistory(filter)}
                testID="history-refresh"
                hitSlop={8}
                style={{ paddingHorizontal: spacing.sm, paddingVertical: spacing.xs }}
              >
                <Ionicons
                  name="refresh"
                  size={20}
                  color={loadingH ? colors.muted : colors.brandPrimary}
                />
              </Pressable>
            )}
          </View>
          {historyExpanded && (
            <View testID="history-body">
              <View style={styles.filterRow}>
                {(["all", "majority", "minority"] as Filter[]).map((f) => (
                  <Pressable
                    key={f}
                    onPress={() => setFilter(f)}
                    testID={`filter-${f}`}
                    style={[styles.filterChip, filter === f && (
                      f === "majority" ? { backgroundColor: colors.brandPrimary } :
                      f === "minority" ? { backgroundColor: colors.brandSecondary } :
                      { backgroundColor: colors.surfaceInverse }
                    )]}
                  >
                    <Text style={[styles.filterTxt,
                      filter === f && (
                        f === "minority" ? { color: colors.onBrandSecondary } : { color: "#FFFFFF" }
                      )
                    ]}>
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
                        onPress={() => router.push(`/feud/${h.feud_id}`)}
                        testID={`history-${h.feud_id}`}
                      >
                        <View style={[styles.sideBar, { backgroundColor: sideColor(h.side_voted) }]} />
                        <View style={{ flex: 1, padding: spacing.sm }}>
                          <Text style={styles.hCat}>{h.category_label.toUpperCase()}</Text>
                          <Text style={styles.hTitle} numberOfLines={2}>{h.title}</Text>
                          <View style={styles.hMetaRow}>
                            <Text style={[styles.hVoted, { color: sideColor(h.side_voted) }]}>Hai votato: {votedName}</Text>
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
          onPress={async () => {
            await logout();
            router.replace("/auth");
          }}
          testID="profile-logout"
        >
          <Text style={styles.logoutText}>ESCI</Text>
        </Pressable>

        <Pressable
          style={styles.adminLink}
          onPress={() => router.push("/admin")}
          testID="profile-admin-link"
        >
          <Ionicons name="shield-checkmark-outline" size={14} color={colors.muted} />
          <Text style={styles.adminLinkTxt}>PANNELLO ADMIN</Text>
        </Pressable>
      </ScrollView>

      <Modal visible={prefsOpen} animationType="slide" transparent onRequestClose={() => setPrefsOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet} testID="prefs-modal">
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>MODIFICA ARGOMENTI</Text>
              <Pressable onPress={() => setPrefsOpen(false)} testID="prefs-modal-close">
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={styles.modalBody}>
              <Pressable onPress={toggleAllEdit} testID="prefs-select-all" style={styles.prefsSelectAllRow}>
                <Ionicons
                  name={allSelected ? "checkbox" : "square-outline"}
                  size={16}
                  color={colors.onSurface}
                />
                <Text style={styles.prefsSelectAllTxt}>
                  {allSelected ? "TOGLI TUTTE" : "SELEZIONA TUTTE"}
                </Text>
              </Pressable>
              <View style={styles.prefsCatsGrid}>
                {cats.map((c) => {
                  const on = editSel.has(c.id);
                  return (
                    <Pressable
                      key={c.id}
                      onPress={() => toggleEdit(c.id)}
                      testID={`prefs-cat-${c.id}`}
                      style={[styles.prefsCatChip, on && styles.prefsCatChipOn]}
                    >
                      <Ionicons
                        name={on ? "checkbox" : "square-outline"}
                        size={20}
                        color={on ? colors.onBrandPrimary : colors.onSurface}
                      />
                      <Text style={[styles.prefsCatTxt, on && styles.prefsCatTxtOn]}>{c.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
              {prefsError && <Text style={styles.prefsErr} testID="prefs-error">{prefsError}</Text>}
            </ScrollView>
            <Pressable onPress={savePrefs} disabled={savingPrefs} testID="prefs-save" style={styles.prefsSaveBtn}>
              {savingPrefs ? (
                <ActivityIndicator color={colors.onBrandPrimary} />
              ) : (
                <Text style={styles.prefsSaveTxt}>SALVA</Text>
              )}
            </Pressable>
          </View>
        </View>
      </Modal>

      <Modal visible={profileOpen} animationType="slide" transparent onRequestClose={() => setProfileOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setProfileOpen(false)}>
          <Pressable style={[styles.modalSheet, styles.modalSheetTall]} onPress={(e) => e.stopPropagation()} testID="profile-edit-modal">
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>MODIFICA PROFILO</Text>
              <Pressable onPress={() => setProfileOpen(false)} testID="profile-edit-close" hitSlop={10}>
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
              <ScrollView
                style={{ flex: 1 }}
                contentContainerStyle={styles.modalBody}
                keyboardShouldPersistTaps="handled"
                showsVerticalScrollIndicator={false}
              >
                <Text style={styles.editSectionTitle}>NICKNAME</Text>
                <TextInput
                  testID="edit-nickname-input"
                  value={editNick}
                  onChangeText={(t) => setEditNick(sanitizeNicknameInput(t))}
                  placeholder="es. gossip_queen"
                  placeholderTextColor={colors.muted}
                  autoCapitalize="none"
                  autoCorrect={false}
                  maxLength={NICKNAME_MAX}
                  style={styles.identInput}
                />
                <Text style={styles.identHint}>2-24 caratteri. {NICKNAME_HINT} Deve essere unico.</Text>

                <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>NOME</Text>
                <TextInput
                  testID="edit-display-input"
                  value={editDisplay}
                  onChangeText={(t) => setEditDisplay(t.slice(0, 40))}
                  placeholder="Es. Mario Rossi (opzionale)"
                  placeholderTextColor={colors.muted}
                  maxLength={40}
                  style={styles.identInput}
                />
                <Text style={styles.identHint}>Nome visibile sotto al nickname. Lascia vuoto per rimuoverlo.</Text>

                <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>FOTO ({photos.length}/7)</Text>
                <View style={styles.photosGrid}>
                  {loadingPhotos ? (
                    <ActivityIndicator color={colors.brandPrimary} />
                  ) : (
                    <>
                      {photos.map((p) => {
                        const isPrimary = p.photo_id === user.primary_photo_id;
                        return (
                          <View key={p.photo_id} style={styles.photoBox} testID={`photo-${p.photo_id}`}>
                            <Image
                              source={{ uri: photoUris[p.photo_id] || `data:image/jpeg;base64,${p.data}` }}
                              style={styles.photoImg}
                            />
                            {isPrimary && (
                              <View style={styles.primaryBadge}>
                                <Ionicons name="star" size={12} color={colors.onBrandSecondary} />
                              </View>
                            )}
                            <View style={styles.photoActions}>
                              {!isPrimary && (
                                <Pressable onPress={() => setPrimary(p.photo_id)} testID={`photo-set-primary-${p.photo_id}`} style={styles.photoAct}>
                                  <Ionicons name="star-outline" size={14} color={colors.onSurface} />
                                </Pressable>
                              )}
                              <Pressable onPress={() => recropPhoto(p)} disabled={openingRecrop === p.photo_id} testID={`photo-recrop-${p.photo_id}`} style={styles.photoAct}>
                                {openingRecrop === p.photo_id ? (
                                  <ActivityIndicator size="small" color={colors.onSurface} />
                                ) : (
                                  <Ionicons name="crop-outline" size={14} color={colors.onSurface} />
                                )}
                              </Pressable>
                              <Pressable onPress={() => deletePhoto(p.photo_id)} testID={`photo-delete-${p.photo_id}`} style={[styles.photoAct, { backgroundColor: colors.brandPrimary }]}>
                                <Ionicons name="trash" size={14} color={colors.onBrandPrimary} />
                              </Pressable>
                            </View>
                          </View>
                        );
                      })}
                      {photos.length < 7 && (
                        <>
                          <Pressable onPress={() => pickPhoto("library")} testID="photo-add-library" style={[styles.photoBox, styles.photoAdd]}>
                            <Ionicons name="images-outline" size={30} color={colors.onSurface} />
                            <Text style={styles.photoAddTxt}>GALLERIA</Text>
                          </Pressable>
                          <Pressable onPress={() => pickPhoto("camera")} testID="photo-add-camera" style={[styles.photoBox, styles.photoAdd]}>
                            <Ionicons name="camera-outline" size={30} color={colors.onSurface} />
                            <Text style={styles.photoAddTxt}>FOTOCAMERA</Text>
                          </Pressable>
                        </>
                      )}
                    </>
                  )}
                </View>

                <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>BIO ({bio.length}/200)</Text>
                <TextInput
                  value={bio}
                  onChangeText={(t) => setBio(t.slice(0, 200))}
                  placeholder="Racconta chi sei..."
                  placeholderTextColor={colors.muted}
                  multiline
                  style={styles.bioInput}
                  testID="bio-input"
                />

                <Text style={[styles.editSectionTitle, { marginTop: spacing.md }]}>SOCIAL</Text>
                {SOCIAL_KEYS.map((k) => (
                  <View key={k} style={styles.socialField}>
                    <Text style={styles.socialFieldLabel}>{SOCIAL_LABELS[k]}</Text>
                    <TextInput
                      value={socials[k]}
                      onChangeText={(t) => setSocials((s) => ({ ...s, [k]: t }))}
                      placeholder={k === "website" ? "esempio.it" : `@handle o url`}
                      placeholderTextColor={colors.muted}
                      autoCapitalize="none"
                      keyboardType="url"
                      style={styles.socialInput}
                      testID={`social-input-${k}`}
                    />
                  </View>
                ))}

                {detailsError && <Text style={styles.prefsErr} testID="details-error">{detailsError}</Text>}
              </ScrollView>
              {detailsError ? (
                <View style={styles.saveErrorBar} testID="details-error-bar">
                  <Ionicons name="alert-circle" size={16} color="#FFFFFF" />
                  <Text style={styles.saveErrorTxt} numberOfLines={2}>{detailsError}</Text>
                </View>
              ) : null}
              <Pressable onPress={saveDetails} disabled={savingDetails} testID="profile-edit-save" style={styles.prefsSaveBtn}>
                {savingDetails ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={styles.prefsSaveTxt}>SALVA</Text>}
              </Pressable>
            </KeyboardAvoidingView>
          </Pressable>
        </Pressable>
      </Modal>

      <PhotoCropper
        visible={cropperOpen}
        uri={cropperUri}
        originalWidth={cropperSize?.w}
        originalHeight={cropperSize?.h}
        onCancel={() => { setCropperOpen(false); setCropperUri(null); setCropperSize(null); setCropperReplaceId(null); setCropperOriginalSourceUri(null); }}
        onConfirm={uploadCroppedPhoto}
      />

      <Modal
        visible={professionOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setProfessionOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet} testID="profession-modal">
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>PROFESSIONE</Text>
              <Pressable onPress={() => setProfessionOpen(false)} testID="profession-modal-close" hitSlop={10}>
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            {savingProfession && (
              <View style={styles.savingBar}>
                <ActivityIndicator color={colors.brandPrimary} />
              </View>
            )}
            <ScrollView contentContainerStyle={{ paddingBottom: spacing.lg }}>
              {professionsList.map((p) => {
                const isSel = user.profession === p;
                return (
                  <Pressable
                    key={p}
                    onPress={() => saveProfession(p)}
                    disabled={savingProfession}
                    style={[styles.professionItem, isSel && styles.professionItemOn]}
                    testID={`profession-opt-${p.replace(/[^a-zA-Z0-9]/g, '-')}`}
                  >
                    <Text style={[styles.professionItemTxt, isSel && styles.professionItemTxtOn]}>
                      {p}
                    </Text>
                    {isSel && <Ionicons name="checkmark" size={20} color={colors.onBrandPrimary} />}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  content: { paddingBottom: spacing.xxxl },
  header: { padding: spacing.lg, backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border, gap: spacing.md },
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  avatarWrap: { position: "relative" },
  avatarImg: { width: 80, height: 80, borderRadius: 40, borderWidth: 0, overflow: "hidden", backgroundColor: colors.surfaceInverse },
  avatarPlaceholder: { alignItems: "center", justifyContent: "center" },
  avatarEditBadge: { position: "absolute", right: -2, bottom: -2, width: 24, height: 24, borderRadius: 12, backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.surface, alignItems: "center", justifyContent: "center" },
  headerBio: { fontSize: font.sizes.base, color: colors.onSurfaceInverse, lineHeight: 20, borderLeftWidth: 2, borderColor: colors.brandSecondary, paddingLeft: spacing.sm },
  headerEditBtn: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "flex-start", borderWidth: 2, borderColor: colors.brandSecondary, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  headerEditTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.brandSecondary },
  anonBanner: { borderWidth: 2, borderColor: colors.brandSecondary, padding: spacing.md, gap: spacing.sm, backgroundColor: "rgba(255,230,0,0.08)" },
  anonBannerTxt: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  anonBannerBody: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, lineHeight: 18 },
  anonRegisterBtn: { alignSelf: "flex-start", backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.brandSecondary, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  anonRegisterTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.base, letterSpacing: 2, fontWeight: "500" },
  anonLockScreen: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl, gap: spacing.md, backgroundColor: colors.surface },
  anonLockCircle: { width: 140, height: 140, borderRadius: 70, borderWidth: 0, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceInverse, marginBottom: spacing.sm, overflow: "hidden" },
  anonLockTitle: { fontSize: font.sizes.xxxl, letterSpacing: 3, fontWeight: "500", color: colors.onSurface, textAlign: "center" },
  anonLockSubtitle: { fontSize: font.sizes.base, color: colors.brandPrimary, letterSpacing: 1, marginTop: -spacing.xs },
  anonLockBody: { fontSize: font.sizes.sm, color: colors.muted, textAlign: "center", lineHeight: 20, paddingHorizontal: spacing.md },
  anonLockCta: { marginTop: spacing.lg, backgroundColor: colors.brandPrimary, borderWidth: 3, borderColor: colors.onSurface, paddingHorizontal: spacing.xxl, paddingVertical: spacing.md },
  anonLockCtaTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 3, fontWeight: "500" },
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
  brand: { color: colors.onSurfaceInverse, fontSize: font.sizes.xxxl, letterSpacing: 2, fontWeight: "500" },
  nickname: { color: colors.brandSecondary, fontSize: font.sizes.xxl, fontWeight: "500" },
  provider: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, opacity: 0.7, marginTop: spacing.xs },
  displayName: {
    color: colors.onSurfaceInverse,
    fontSize: font.sizes.base,
    opacity: 0.75,
    marginTop: 2,
  },
  badgeBlock: { alignItems: "center", padding: spacing.xl, borderBottomWidth: 2, borderColor: colors.border },
  badgeIcon: { width: 140, height: 140, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  badgeTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface, marginTop: spacing.md },
  badgeSubtitle: { fontSize: font.sizes.base, color: colors.muted, marginTop: spacing.xs },
  statsRow: { flexDirection: "row", borderBottomWidth: 2, borderColor: colors.border },
  statBox: { flex: 1, padding: spacing.md, alignItems: "center", borderColor: colors.border, backgroundColor: colors.surfaceSecondary },
  statValue: { fontSize: font.sizes.xxxl, fontWeight: "500", color: colors.onSurface },
  statLabel: { fontSize: font.sizes.xs, color: colors.muted, letterSpacing: 1, marginTop: 2 },
  historyHeader: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  historyTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  historySection: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 2, borderColor: colors.border },
  historyHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: spacing.xs },
  sectionHeadRight: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sectionCountBadge: { color: colors.muted, fontSize: font.sizes.sm, letterSpacing: 1, minWidth: 20, textAlign: "right" },
  filterRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingBottom: spacing.md },
  filterChip: { flex: 1, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center", backgroundColor: colors.surfaceSecondary },
  filterTxt: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  center: { padding: spacing.xl, alignItems: "center" },
  emptyH: { paddingHorizontal: spacing.lg, paddingVertical: spacing.xl, color: colors.muted, fontSize: font.sizes.base },
  historyList: { paddingHorizontal: spacing.lg, gap: spacing.sm },
  historyItem: { flexDirection: "row", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, overflow: "hidden" },
  sideBar: { width: 8 },
  hCat: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted },
  hTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  hMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.xs, flexWrap: "wrap", gap: spacing.xs },
  hVoted: { fontSize: font.sizes.xs, fontWeight: "500" },
  hBadge: { fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: colors.border },
  hBadgeMaj: { backgroundColor: colors.brandPrimary, color: colors.onBrandPrimary },
  hBadgeMin: { backgroundColor: colors.brandSecondary, color: colors.onBrandSecondary },
  logout: { margin: spacing.lg, borderWidth: 2, borderColor: colors.border, padding: spacing.md, alignItems: "center", backgroundColor: colors.brandPrimary },
  pushRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, marginHorizontal: spacing.lg, marginTop: spacing.md, padding: spacing.md, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  pushLabel: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  pushHint: { color: colors.onSurfaceInverse, fontSize: font.sizes.xs, marginTop: 4, opacity: 0.75 },
  supportBtn: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginHorizontal: spacing.lg, marginTop: spacing.md, padding: spacing.md, borderWidth: 2, borderColor: colors.brandSecondary, backgroundColor: colors.surfaceInverse },
  supportTxt: { flex: 1, color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 2, fontWeight: "500" },
  logoutText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500" },
  adminLink: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: spacing.md, marginBottom: spacing.lg },
  adminLinkTxt: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted, fontWeight: "500" },
  prefsSection: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, gap: spacing.sm },
  blockedEmpty: { color: colors.muted, fontSize: font.sizes.sm, fontStyle: "italic" },
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
  prefsHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: spacing.xs },
  prefsBody: { gap: spacing.sm, marginTop: spacing.xs },
  prefsTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  prefsEditBtn: { flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceSecondary },
  prefsEditBtnFull: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceSecondary, alignSelf: "flex-start" },
  prefsEditTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.onSurface },
  prefsChipsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xs },
  prefChip: { borderWidth: 2, borderColor: colors.brandPrimary, backgroundColor: colors.brandPrimary, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  prefChipTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500" },
  prefEmpty: { fontSize: font.sizes.base, color: colors.muted },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
  modalSheetTall: { height: "92%", maxHeight: "92%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  modalTitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  modalBody: { padding: spacing.lg, gap: spacing.md },
  prefsSelectAllRow: { alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceSecondary },
  prefsSelectAllTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.onSurface },
  prefsCatsGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  prefsCatChip: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.surfaceSecondary },
  prefsCatChipOn: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  prefsCatTxt: { fontSize: font.sizes.base, color: colors.onSurface, fontWeight: "500" },
  prefsCatTxtOn: { color: colors.onBrandPrimary },
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
