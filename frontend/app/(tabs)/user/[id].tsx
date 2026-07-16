import { useEffect, useState, useCallback } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Image, Linking,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, PublicUser, HistoryItem } from "@/src/api";
import { colors, spacing, font, sideColor } from "@/src/theme";

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
  const [profile, setProfile] = useState<PublicUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loadingH, setLoadingH] = useState(false);
  const [filter, setFilter] = useState<HFilter>("all");
  const [historyExpanded, setHistoryExpanded] = useState(false);

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

  const loadHistory = useCallback(async (uid: string, f: HFilter) => {
    setLoadingH(true);
    try {
      const r = await api.publicUserHistory(uid, f);
      setHistory(r.history || []);
    } catch { setHistory([]); }
    finally { setLoadingH(false); }
  }, []);

  useEffect(() => {
    if (!id) return;
    // Skip loading history for anonymous accounts or when the section is
    // collapsed — avoid unnecessary requests + loader flicker.
    if (profile?.is_anonymous) return;
    if (!historyExpanded) return;
    loadHistory(id, filter);
  }, [id, filter, loadHistory, profile?.is_anonymous, historyExpanded]);

  // Auto-refresh public user history every 30s while section is expanded, so
  // the per-vote MAGGIORANZA/MINORANZA labels reflect real-time majority
  // flips even when the observer stays on the page.
  useEffect(() => {
    if (!id || profile?.is_anonymous || !historyExpanded) return;
    const t = setInterval(() => { loadHistory(id, filter); }, 30000);
    return () => clearInterval(t);
  }, [id, filter, loadHistory, profile?.is_anonymous, historyExpanded]);

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
          <Pressable onPress={() => router.back()} testID="user-back" style={styles.backBtn}>
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
        <Pressable onPress={() => router.back()} testID="user-back" style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color={colors.onSurfaceInverse} />
          <Text style={styles.backTxt}>INDIETRO</Text>
        </Pressable>
        <Text style={styles.topNick}>@{profile.nickname}</Text>
      </View>

      <ScrollView contentContainerStyle={{ paddingBottom: spacing.xxxl }}>
        <View style={styles.galleryWrap}>
          <View style={styles.avatarRing}>
            {hasPhotos ? (
              <Image
                source={{ uri: `data:image/jpeg;base64,${current!.data}` }}
                style={styles.avatarImg}
                resizeMode="cover"
                testID={`gallery-image-${idx}`}
              />
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
          <Text style={styles.nick}>@{profile.nickname}</Text>
          <Text style={styles.stat}>
            {profile.total_votes} voti · {profile.majority_votes} maggioranza · {profile.minority_votes} minoranza
          </Text>

          {badgeUnlocked && badgeType && (
            <View
              style={[
                styles.badgeCard,
                badgeType === "bastian_contrario" ? styles.badgeCardRed : styles.badgeCardYellow,
              ]}
              testID={`public-badge-${badgeType}`}
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
                <Text style={styles.badgeKicker}>SPILLA</Text>
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
            </View>
          )}

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
                <View style={styles.filterRow}>
                  {(["all", "majority", "minority"] as HFilter[]).map((f) => (
                    <Pressable
                      key={f}
                      onPress={() => setFilter(f)}
                      testID={`public-filter-${f}`}
                      style={[
                        styles.filterChip,
                        filter === f && (
                          f === "majority" ? { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary } :
                          f === "minority" ? { backgroundColor: colors.brandSecondary, borderColor: colors.brandSecondary } :
                          { backgroundColor: colors.surfaceInverse, borderColor: colors.surfaceInverse }
                        ),
                      ]}
                    >
                      <Text style={[
                        styles.filterTxt,
                        filter === f && (
                          f === "minority" ? { color: colors.onBrandSecondary } : { color: "#FFFFFF" }
                        ),
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
                          onPress={() => router.push(`/feud/${h.feud_id}`)}
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
              </View>
            ) : null}
          </View>
        </View>
      </ScrollView>
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
  filterChip: { flex: 1, borderWidth: 2, borderColor: colors.border, paddingVertical: spacing.sm, alignItems: "center", backgroundColor: colors.surfaceSecondary },
  filterTxt: { fontSize: font.sizes.xs, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  emptyH: { paddingVertical: spacing.lg, color: colors.muted, fontSize: font.sizes.base, textAlign: "center" },
  historyList: { gap: spacing.sm, marginTop: spacing.sm },
  historyItem: { flexDirection: "row", borderWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceSecondary, overflow: "hidden" },
  sideBar: { width: 8 },
  hCat: { fontSize: font.sizes.xs, letterSpacing: 2, color: colors.muted },
  hTitle: { fontSize: font.sizes.base, color: colors.onSurface, marginTop: 2, lineHeight: 18 },
  hMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.xs, flexWrap: "wrap", gap: spacing.xs },
  hVoted: { fontSize: font.sizes.xs, fontWeight: "500", flexShrink: 1 },
  hBadge: { fontSize: font.sizes.xs, letterSpacing: 1, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: colors.border },
  hBadgeMaj: { backgroundColor: colors.brandPrimary, color: colors.onBrandPrimary },
  hBadgeMin: { backgroundColor: colors.brandSecondary, color: colors.onBrandSecondary },
});
