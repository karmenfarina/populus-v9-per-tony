import { useEffect, useState } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Image, Linking,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, PublicUser } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

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

export default function UserPublicScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [profile, setProfile] = useState<PublicUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        const r = await api.publicUser(id);
        setProfile(r);
        // Start on primary photo
        const pIdx = r.photos.findIndex((p: any) => p.photo_id === r.primary_photo_id);
        setIdx(pIdx >= 0 ? pIdx : 0);
      } catch (e: any) { setError(e?.message || "Errore"); }
      finally { setLoading(false); }
    })();
  }, [id]);

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
          {hasPhotos ? (
            <Image
              source={{ uri: `data:image/jpeg;base64,${current!.data}` }}
              style={styles.galleryImg}
              resizeMode="cover"
              testID={`gallery-image-${idx}`}
            />
          ) : (
            <View style={[styles.galleryImg, styles.noPhotoBox]}>
              <Ionicons name="person-outline" size={96} color={colors.muted} />
              <Text style={styles.noPhotoTxt}>Nessuna foto</Text>
            </View>
          )}
          {photos.length > 1 && (
            <>
              <Pressable onPress={prev} testID="gallery-prev" style={[styles.arrow, { left: spacing.md }]}>
                <Ionicons name="chevron-back" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
              <Pressable onPress={next} testID="gallery-next" style={[styles.arrow, { right: spacing.md }]}>
                <Ionicons name="chevron-forward" size={26} color={colors.onSurfaceInverse} />
              </Pressable>
              <View style={styles.dots}>
                {photos.map((_, i) => (
                  <View key={i} style={[styles.dot, i === idx && styles.dotOn]} />
                ))}
              </View>
            </>
          )}
        </View>

        <View style={styles.body}>
          <Text style={styles.nick}>@{profile.nickname}</Text>
          {profile.badge?.unlocked && (
            <Text style={[styles.badge, profile.badge.type === "bastian_contrario" ? styles.badgeRed : styles.badgeYellow]}>
              {profile.badge.type === "bastian_contrario" ? "BASTIAN CONTRARIO" : "BUON SENSO"}
            </Text>
          )}
          <Text style={styles.stat}>
            {profile.total_votes} voti · {profile.majority_votes} maggioranza · {profile.minority_votes} minoranza
          </Text>

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
  galleryWrap: { position: "relative", backgroundColor: colors.surfaceInverse, borderBottomWidth: 2, borderColor: colors.border },
  galleryImg: { width: "100%", aspectRatio: 1, backgroundColor: colors.surfaceInverse },
  noPhotoBox: { alignItems: "center", justifyContent: "center", gap: spacing.sm },
  noPhotoTxt: { color: colors.muted, fontSize: font.sizes.base, letterSpacing: 1 },
  arrow: { position: "absolute", top: "50%", marginTop: -24, width: 48, height: 48, backgroundColor: "rgba(0,0,0,0.55)", borderWidth: 2, borderColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  dots: { position: "absolute", bottom: spacing.md, left: 0, right: 0, flexDirection: "row", gap: 6, justifyContent: "center" },
  dot: { width: 8, height: 8, backgroundColor: "rgba(255,255,255,0.35)", borderWidth: 1, borderColor: "#000" },
  dotOn: { backgroundColor: colors.brandSecondary },
  body: { padding: spacing.lg, gap: spacing.md },
  nick: { fontSize: font.sizes.xxxl, fontWeight: "500", letterSpacing: 1, color: colors.onSurface },
  badge: { alignSelf: "flex-start", paddingHorizontal: spacing.sm, paddingVertical: 4, borderWidth: 2, borderColor: colors.border, fontSize: font.sizes.xs, letterSpacing: 2, fontWeight: "500" },
  badgeRed: { backgroundColor: colors.brandPrimary, color: colors.onBrandPrimary },
  badgeYellow: { backgroundColor: colors.brandSecondary, color: colors.onBrandSecondary },
  stat: { fontSize: font.sizes.sm, color: colors.muted, letterSpacing: 0.5 },
  section: { gap: spacing.xs, marginTop: spacing.md },
  sectionTitle: { fontSize: font.sizes.sm, letterSpacing: 2, color: colors.brandPrimary, fontWeight: "500" },
  bio: { fontSize: font.sizes.base, color: colors.onSurface, lineHeight: 20 },
  socialRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderWidth: 2, borderColor: colors.border, padding: spacing.sm, backgroundColor: colors.surfaceSecondary },
  socialLabel: { fontSize: font.sizes.sm, letterSpacing: 1, color: colors.onSurface, fontWeight: "500" },
  socialUrl: { fontSize: font.sizes.xs, color: colors.muted, marginTop: 2 },
});
