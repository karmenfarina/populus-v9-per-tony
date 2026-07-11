import { useCallback, useEffect, useMemo, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Modal, TextInput, Image, KeyboardAvoidingView, Platform } from "react-native";
import * as ImagePicker from "expo-image-picker";
import * as ImageManipulator from "expo-image-manipulator";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { api, HistoryItem, UserPhoto } from "@/src/api";
import { colors, spacing, font, sideColor } from "@/src/theme";

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
  const [cats, setCats] = useState<{ id: string; label: string }[]>([]);
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
  const isAnonymous = user?.auth_provider === "anonymous";

  const loadHistory = useCallback(async (f: Filter) => {
    setLoadingH(true);
    try {
      const r = await api.history(f);
      setHistory(r.history);
    } finally { setLoadingH(false); }
  }, []);

  useEffect(() => {
    refreshMe();
    loadHistory(filter);
  }, [filter, loadHistory, refreshMe]);

  useEffect(() => {
    (async () => {
      try {
        const c = await api.categories();
        setCats(c.categories);
      } catch {}
    })();
  }, []);

  const loadPhotos = useCallback(async () => {
    setLoadingPhotos(true);
    try {
      const r = await api.myPhotos();
      const list: UserPhoto[] = r.photos || [];
      setPhotos(list);
      const primary = list.find((p) => p.photo_id === r.primary_photo_id) || list[0];
      setPrimaryPhotoData(primary?.data || null);
    } finally { setLoadingPhotos(false); }
  }, []);

  useEffect(() => {
    loadPhotos();
  }, [loadPhotos]);

  const openProfileEdit = () => {
    setDetailsError(null);
    setBio(user?.bio || "");
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
    const opts: ImagePicker.ImagePickerOptions = {
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.7,
      base64: true,
      allowsEditing: true,
      aspect: [1, 1],
    };
    const res =
      source === "camera"
        ? await ImagePicker.launchCameraAsync(opts)
        : await ImagePicker.launchImageLibraryAsync(opts);
    if (res.canceled || !res.assets[0]) return;
    const asset = res.assets[0];
    let base64 = asset.base64 || "";
    // Client-side compression if too large: shrink long-edge to 1080, quality 0.6
    // Threshold ~700_000 base64 chars ≈ ~500KB decoded.
    if (base64.length > 700_000 && asset.uri) {
      try {
        const manipulated = await ImageManipulator.manipulateAsync(
          asset.uri,
          [{ resize: { width: 1080 } }],
          { compress: 0.6, format: ImageManipulator.SaveFormat.JPEG, base64: true }
        );
        if (manipulated.base64) base64 = manipulated.base64;
      } catch {
        // fall through with original base64
      }
    }
    if (!base64) { setDetailsError("Impossibile leggere l'immagine"); return; }
    try {
      await api.uploadPhoto(base64);
      await loadPhotos();
      await refreshMe();
    } catch (e: any) { setDetailsError(e?.message || "Errore upload"); }
  };

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
      await api.updateDetails({ bio: bio.trim(), social_links: socials });
      await refreshMe();
      setProfileOpen(false);
    } catch (e: any) { setDetailsError(e?.message || "Errore salvataggio"); }
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

  return (
    <SafeAreaView style={styles.safe} edges={["top"]} testID="profile-screen">
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <View style={styles.headerRow}>
            {isAnonymous ? (
              <View style={styles.avatarWrap}>
                {primaryPhotoData ? (
                  <Image source={{ uri: `data:image/jpeg;base64,${primaryPhotoData}` }} style={styles.avatarImg} />
                ) : (
                  <View style={[styles.avatarImg, styles.avatarPlaceholder]}>
                    <Ionicons name="person" size={40} color={colors.brandSecondary} />
                  </View>
                )}
              </View>
            ) : (
              <Pressable onPress={openProfileEdit} testID="profile-avatar" style={styles.avatarWrap}>
                {primaryPhotoData ? (
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
              <Text style={styles.brand}>PROFILO</Text>
              <Text style={styles.nickname} testID="profile-nickname">@{user.nickname}</Text>
              <Text style={styles.provider}>
                {user.auth_provider === "email" ? "Email" : user.auth_provider === "google" ? "Google" : "Anonimo"}
                {user.email ? ` · ${user.email}` : ""}
              </Text>
            </View>
          </View>
          {!isAnonymous && user.bio ? <Text style={styles.headerBio} testID="profile-bio">{user.bio}</Text> : null}
          {isAnonymous ? (
            <View style={styles.anonBanner} testID="anon-banner">
              <Ionicons name="information-circle-outline" size={14} color={colors.brandSecondary} />
              <Text style={styles.anonBannerTxt}>Registrati per personalizzare il tuo profilo.</Text>
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
            <View style={styles.prefsHeadRow}>
              <Text style={styles.prefsTitle}>ARGOMENTI PREFERITI</Text>
              <Pressable onPress={openPrefs} testID="prefs-edit-button" style={styles.prefsEditBtn}>
                <Ionicons name="pencil" size={14} color={colors.onSurface} />
                <Text style={styles.prefsEditTxt}>MODIFICA</Text>
              </Pressable>
            </View>
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
          </View>
        )}

        <View style={styles.historyHeader}>
          <Text style={styles.historyTitle}>STORICO VOTI</Text>
        </View>
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

        <Pressable style={styles.logout} onPress={logout} testID="profile-logout">
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
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalSheet, { maxHeight: "92%" }]} testID="profile-edit-modal">
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>MODIFICA PROFILO</Text>
              <Pressable onPress={() => setProfileOpen(false)} testID="profile-edit-close">
                <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
            </View>
            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
              <ScrollView contentContainerStyle={styles.modalBody}>
                <Text style={styles.editSectionTitle}>FOTO ({photos.length}/7)</Text>
                <View style={styles.photosGrid}>
                  {loadingPhotos ? (
                    <ActivityIndicator color={colors.brandPrimary} />
                  ) : (
                    <>
                      {photos.map((p) => {
                        const isPrimary = p.photo_id === user.primary_photo_id;
                        return (
                          <View key={p.photo_id} style={styles.photoBox} testID={`photo-${p.photo_id}`}>
                            <Image source={{ uri: `data:image/jpeg;base64,${p.data}` }} style={styles.photoImg} />
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
              <Pressable onPress={saveDetails} disabled={savingDetails} testID="profile-edit-save" style={styles.prefsSaveBtn}>
                {savingDetails ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={styles.prefsSaveTxt}>SALVA</Text>}
              </Pressable>
            </KeyboardAvoidingView>
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
  avatarImg: { width: 80, height: 80, borderWidth: 2, borderColor: colors.brandSecondary, backgroundColor: colors.surfaceInverse },
  avatarPlaceholder: { alignItems: "center", justifyContent: "center" },
  avatarEditBadge: { position: "absolute", right: -4, bottom: -4, width: 22, height: 22, borderRadius: 11, backgroundColor: colors.brandPrimary, borderWidth: 2, borderColor: colors.surfaceInverse, alignItems: "center", justifyContent: "center" },
  headerBio: { fontSize: font.sizes.base, color: colors.onSurfaceInverse, lineHeight: 20, borderLeftWidth: 2, borderColor: colors.brandSecondary, paddingLeft: spacing.sm },
  headerEditBtn: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "flex-start", borderWidth: 2, borderColor: colors.brandSecondary, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  headerEditTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.brandSecondary },
  anonBanner: { flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 2, borderColor: colors.brandSecondary, paddingHorizontal: spacing.sm, paddingVertical: 6, alignSelf: "flex-start" },
  anonBannerTxt: { color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500" },
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
  nickname: { color: colors.brandSecondary, fontSize: font.sizes.xl, marginTop: spacing.sm },
  provider: { color: colors.onSurfaceInverse, fontSize: font.sizes.sm, opacity: 0.7, marginTop: spacing.xs },
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
  logoutText: { color: colors.onBrandPrimary, fontSize: font.sizes.lg, letterSpacing: 2, fontWeight: "500" },
  adminLink: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: spacing.md, marginBottom: spacing.lg },
  adminLinkTxt: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted, fontWeight: "500" },
  prefsSection: { padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, gap: spacing.sm },
  prefsHeadRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  prefsTitle: { fontSize: font.sizes.xxl, letterSpacing: 2, fontWeight: "500", color: colors.onSurface },
  prefsEditBtn: { flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 2, borderColor: colors.border, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceSecondary },
  prefsEditTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500", color: colors.onSurface },
  prefsChipsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xs },
  prefChip: { borderWidth: 2, borderColor: colors.brandPrimary, backgroundColor: colors.brandPrimary, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  prefChipTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.sm, letterSpacing: 1, fontWeight: "500" },
  prefEmpty: { fontSize: font.sizes.base, color: colors.muted },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
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
  prefsSaveBtn: { backgroundColor: colors.brandPrimary, borderTopWidth: 2, borderColor: colors.border, paddingVertical: spacing.lg, alignItems: "center" },
  prefsSaveTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
});
