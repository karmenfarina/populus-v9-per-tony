import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  Modal,
  Pressable,
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { api, ApiError } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, font, spacing } from "@/src/theme";

/**
 * Full badge shelf modal.
 *
 * Presented when the user taps the primary alignment badge on any
 * profile screen (their own or a third party's). Shows every category
 * × tier combination as a card:
 *   - unlocked → full color + emoji + tier label + "SBLOCCATA"
 *   - locked   → grey/opaque + progress line ("77 / 100 commenti")
 *
 * The whole payload is fetched from `/api/users/{id}/category_badges`
 * — see `_build_category_badge_payload` on the backend for the exact
 * shape. Anonymous accounts always come back with an all-zero grid so
 * the layout stays consistent.
 */

type Tier = {
  tier: 1 | 2 | 3;
  name: string;
  emoji: string;
  threshold: number;
  unlocked: boolean;
};

type CategoryBadge = {
  category_id: string;
  color: string;
  icon: string;
  count: number;
  tiers: Tier[];
};

type Props = {
  visible: boolean;
  userId: string;
  displayName?: string;
  onClose: () => void;
};

// Category label lookup — mirrors the backend registry order but keeps
// human-readable Italian labels for the section header rows.
const CATEGORY_LABEL: Record<string, string> = {
  politica: "Politica",
  tv: "Programmi TV",
  musica: "Musica",
  sport: "Sport",
  cinema: "Cinema",
  social: "Social",
  gossip: "Gossip",
  cronaca: "Cronaca",
  tech: "Tech",
};

export default function CategoryBadgesModal({ visible, userId, displayName, onClose }: Props) {
  const { user } = useAuth();
  const isOwnShelf = !!user && user.user_id === userId;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [badges, setBadges] = useState<CategoryBadge[]>([]);
  // Which tier the user is currently sharing — used to disable the
  // tap and show an inline spinner without blocking the whole modal.
  const [sharingKey, setSharingKey] = useState<string | null>(null);

  const shareBadge = useCallback(
    async (categoryId: string, tier: 1 | 2 | 3, tierName: string) => {
      // Only owner can share their own badges — enforced on backend
      // as well, but we don't even show the prompt on other profiles.
      if (!isOwnShelf) return;
      const key = `${categoryId}:${tier}`;
      // Confirm dialog. Uses Alert.alert because this modal isn't
      // itself nested inside another Modal — safe on iOS/Android/Web.
      Alert.alert(
        "Condividere questa spilla?",
        `Pubblicheremo una storia di 24h che mostra "${tierName}" alla tua Cerchia.`,
        [
          { text: "Annulla", style: "cancel" },
          {
            text: "Condividi",
            style: "default",
            onPress: async () => {
              setSharingKey(key);
              try {
                await api.createBadgeStory(categoryId, tier);
                setSharingKey(null);
                Alert.alert(
                  "Storia pubblicata",
                  "La tua spilla è ora visibile alla Cerchia per 24 ore.",
                );
              } catch (e: any) {
                setSharingKey(null);
                const status = e instanceof ApiError ? e.status : 0;
                const detail = (e?.message || "").trim();
                let title = "Impossibile pubblicare";
                let message = detail || "Errore sconosciuto";
                if (status === 429) {
                  title = "Limite giornaliero raggiunto";
                  message = detail || "Hai raggiunto il limite di storie di oggi. Riprova domani.";
                } else if (status === 403) {
                  title = "Non autorizzato";
                  message = detail || "Non puoi condividere questa spilla.";
                }
                Alert.alert(title, message);
              }
            },
          },
        ],
      );
    },
    [isOwnShelf],
  );

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    try {
      const r: any = await api.categoryBadges(userId);
      setBadges((r?.badges || []) as CategoryBadge[]);
    } catch (e: any) {
      setError(e?.message || "Impossibile caricare le spille");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  // Precompute totals for the header stat strip so users get an
  // at-a-glance sense of their progress before scrolling the grid.
  const unlockedCount = badges.reduce((acc, cat) => acc + cat.tiers.filter((t) => t.unlocked).length, 0);
  const totalCount = badges.reduce((acc, cat) => acc + cat.tiers.length, 0);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet} testID="category-badges-modal">
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>COLLEZIONE SPILLE</Text>
              {displayName ? (
                <Text style={styles.subtitle} numberOfLines={1}>{displayName}</Text>
              ) : null}
            </View>
            <Pressable onPress={onClose} style={styles.closeBtn} testID="category-badges-close" hitSlop={10}>
              <Ionicons name="close" size={22} color={colors.onSurface} />
            </Pressable>
          </View>

          {!loading && !error ? (
            <View style={styles.statStrip}>
              <Text style={styles.statTxt}>
                <Text style={styles.statNum}>{unlockedCount}</Text>
                <Text> / {totalCount} spille sbloccate</Text>
              </Text>
              <Text style={styles.statHint}>
                {unlockedCount === 0
                  ? "Commenta le faide per iniziare la collezione"
                  : "Continua a commentare per sbloccarne di nuove"}
              </Text>
              {isOwnShelf && unlockedCount > 0 && (
                <View style={styles.shareHintRow}>
                  <Ionicons name="arrow-redo" size={12} color={colors.brandPrimary} />
                  <Text style={styles.shareHintTxt}>
                    Tocca una spilla sbloccata per condividerla nelle storie
                  </Text>
                </View>
              )}
            </View>
          ) : null}

          {loading ? (
            <View style={styles.loading}>
              <ActivityIndicator color={colors.brandPrimary} />
            </View>
          ) : error ? (
            <View style={styles.errorBox}>
              <Ionicons name="alert-circle" size={28} color={colors.brandPrimary} />
              <Text style={styles.errorTxt}>{error}</Text>
              <Pressable onPress={load} style={styles.retry}>
                <Text style={styles.retryTxt}>RIPROVA</Text>
              </Pressable>
            </View>
          ) : (
            <ScrollView contentContainerStyle={styles.scrollBody} showsVerticalScrollIndicator={false}>
              {badges.map((cat) => (
                <View key={cat.category_id} style={styles.section} testID={`badges-section-${cat.category_id}`}>
                  <View style={styles.sectionHeader}>
                    <View style={[styles.sectionIcon, { backgroundColor: cat.color }]}>
                      <Ionicons name={cat.icon as any} size={16} color="#fff" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.sectionLabel}>{CATEGORY_LABEL[cat.category_id] || cat.category_id}</Text>
                      <Text style={styles.sectionCount}>{cat.count} commenti totali</Text>
                    </View>
                  </View>

                  <View style={styles.tierRow}>
                    {cat.tiers.map((t) => {
                      const bgColor = t.unlocked ? cat.color : "#3A3A3A";
                      const key = `${cat.category_id}:${t.tier}`;
                      const isSharing = sharingKey === key;
                      // Tap-to-share is only available on the owner's
                      // own shelf AND only for UNLOCKED tiers. Locked
                      // tiers stay non-interactive (nothing to share yet).
                      const canShare = isOwnShelf && t.unlocked;
                      return (
                        <Pressable
                          key={t.tier}
                          onPress={canShare ? () => shareBadge(cat.category_id, t.tier, t.name) : undefined}
                          disabled={!canShare || isSharing}
                          android_ripple={canShare ? { color: "rgba(255,255,255,0.25)", borderless: false } : undefined}
                          style={({ pressed }) => [
                            styles.tierCard,
                            {
                              backgroundColor: bgColor,
                              opacity: t.unlocked ? (pressed && canShare ? 0.85 : 1) : 0.55,
                              transform: pressed && canShare ? [{ scale: 0.97 }] : undefined,
                            },
                          ]}
                          testID={`badge-${cat.category_id}-tier${t.tier}`}
                          accessibilityRole={canShare ? "button" : "text"}
                          accessibilityLabel={
                            canShare
                              ? `${t.name}, tocca per condividere nelle storie`
                              : `${t.name}, ${t.unlocked ? "sbloccata" : "non ancora sbloccata"}`
                          }
                        >
                          {/* Small share affordance in the corner of
                              every unlocked tier owned by the current
                              user — signals the tap is available. */}
                          {canShare && (
                            <View style={styles.shareChip}>
                              {isSharing ? (
                                <ActivityIndicator size={10} color="#fff" />
                              ) : (
                                <Ionicons name="arrow-redo" size={11} color="#fff" />
                              )}
                            </View>
                          )}
                          <Text style={styles.tierEmoji} numberOfLines={1}>{t.emoji}</Text>
                          <Text style={styles.tierName} numberOfLines={2}>{t.name}</Text>
                          <View style={styles.tierBadge}>
                            <Text style={styles.tierBadgeTxt}>LIV. {t.tier}</Text>
                          </View>
                          <Text style={styles.tierProgress} numberOfLines={1}>
                            {t.unlocked
                              ? `SBLOCCATA`
                              : `${Math.min(cat.count, t.threshold)}/${t.threshold}`}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              ))}
              <View style={{ height: spacing.xl }} />
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.72)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
    maxHeight: "88%",
    minHeight: "70%",
    paddingTop: spacing.md,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: 2,
    borderBottomColor: colors.border,
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 2,
  },
  subtitle: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 2,
  },
  closeBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  statStrip: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  statTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
  },
  statNum: {
    fontSize: font.sizes.xxl,
    fontWeight: "700",
    color: colors.brandPrimary,
  },
  statHint: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 4,
  },
  loading: {
    paddingVertical: spacing.xl * 2,
    alignItems: "center",
  },
  errorBox: {
    paddingVertical: spacing.xl,
    alignItems: "center",
    gap: spacing.sm,
  },
  errorTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    textAlign: "center",
    paddingHorizontal: spacing.lg,
  },
  retry: {
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    marginTop: spacing.sm,
  },
  retryTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  scrollBody: {
    paddingTop: spacing.md,
    paddingBottom: spacing.lg,
  },
  section: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  sectionIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  sectionLabel: {
    color: colors.onSurface,
    fontSize: font.sizes.base,
    fontWeight: "700",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  sectionCount: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: 1,
  },
  tierRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  tierCard: {
    flex: 1,
    padding: spacing.sm,
    borderRadius: 8,
    minHeight: 130,
    alignItems: "center",
    justifyContent: "space-between",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
  },
  tierEmoji: {
    fontSize: 34,
    marginTop: 2,
  },
  tierName: {
    color: "#fff",
    fontSize: font.sizes.xs,
    fontWeight: "700",
    textAlign: "center",
    letterSpacing: 0.5,
  },
  tierBadge: {
    backgroundColor: "rgba(0,0,0,0.35)",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  tierBadgeTxt: {
    color: "#fff",
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  tierProgress: {
    color: "rgba(255,255,255,0.9)",
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  // Small share affordance overlaid on the top-right of every
  // unlocked tier card owned by the current user. Absolute positioning
  // keeps the existing card layout intact.
  shareChip: {
    position: "absolute",
    top: 6,
    right: 6,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center",
    justifyContent: "center",
  },
  shareHintRow: {
    marginTop: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  shareHintTxt: {
    color: colors.brandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "600",
  },
});
