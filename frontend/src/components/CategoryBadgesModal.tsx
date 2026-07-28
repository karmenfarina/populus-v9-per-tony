import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  Modal,
  Pressable,
  ActivityIndicator,
  ScrollView,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { api, ApiError } from "@/src/api";
import { useAuth } from "@/src/auth/AuthContext";
import { colors, font, spacing, radius } from "@/src/theme";

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
  // Inline confirmation state. We can't use `Alert.alert` here
  // because this whole component IS a <Modal>, and on RN nested
  // Modal contexts frequently swallow the native Alert popup — so
  // the user's tap looks like it does nothing. Rendering our own
  // confirm sheet inside this modal is the only 100% reliable
  // feedback path.
  const [pendingShare, setPendingShare] = useState<
    | { key: string; categoryId: string; tier: 1 | 2 | 3; tierName: string; color: string; emoji: string }
    | null
  >(null);
  // In-modal toast for post-share feedback (success or error). Same
  // reasoning as the pendingShare dialog above.
  const [toast, setToast] = useState<
    | { kind: "success" | "error"; title: string; message: string }
    | null
  >(null);

  const shareBadge = useCallback(
    (categoryId: string, tier: 1 | 2 | 3, tierName: string, color: string, emoji: string) => {
      if (!isOwnShelf) return;
      setToast(null);
      setPendingShare({
        key: `${categoryId}:${tier}`,
        categoryId,
        tier,
        tierName,
        color,
        emoji,
      });
    },
    [isOwnShelf],
  );

  const confirmShare = useCallback(async () => {
    if (!pendingShare) return;
    const { key, categoryId, tier } = pendingShare;
    setSharingKey(key);
    setPendingShare(null);
    try {
      await api.createBadgeStory(categoryId, tier);
      setSharingKey(null);
      setToast({
        kind: "success",
        title: "Storia pubblicata",
        message: "La tua spilla è visibile alla Cerchia per 24 ore.",
      });
    } catch (e: any) {
      setSharingKey(null);
      const status = e instanceof ApiError ? e.status : 0;
      const detail = (e?.message || "").trim();
      let title = "Impossibile pubblicare";
      let message = detail || "Errore sconosciuto";
      if (status === 429) {
        title = "Limite giornaliero raggiunto";
        message =
          detail || "Hai raggiunto il limite di storie di oggi. Riprova domani.";
      } else if (status === 403) {
        title = "Non autorizzato";
        message = detail || "Non puoi condividere questa spilla.";
      } else if (status === 0) {
        title = "Connessione assente";
        message = "Impossibile contattare il server. Controlla la connessione.";
      }
      setToast({ kind: "error", title, message });
    }
  }, [pendingShare]);

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
                          onPress={canShare ? () => shareBadge(cat.category_id, t.tier, t.name, cat.color, t.emoji) : undefined}
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

          {/* Inline confirm dialog — rendered ABOVE the modal content
              via absolute positioning + backdrop. Using our own
              overlay instead of Alert.alert because native alerts
              are swallowed by nested Modals on RN. */}
          {pendingShare && (
            <View style={styles.confirmBackdrop}>
              <View style={styles.confirmSheet} testID="badge-share-confirm">
                <View style={[styles.confirmEmojiWrap, { backgroundColor: pendingShare.color }]}>
                  <Text style={styles.confirmEmoji}>{pendingShare.emoji}</Text>
                </View>
                <Text style={styles.confirmTitle}>Condividere questa spilla?</Text>
                <Text style={styles.confirmBody}>
                  Pubblicheremo una storia di 24 ore che mostra{" "}
                  <Text style={{ fontWeight: "700" }}>«{pendingShare.tierName}»</Text> alla tua Cerchia.
                </Text>
                <View style={styles.confirmRow}>
                  <Pressable
                    onPress={() => setPendingShare(null)}
                    style={[styles.confirmBtn, styles.confirmBtnSecondary]}
                    testID="badge-share-cancel"
                  >
                    <Text style={styles.confirmBtnSecondaryTxt}>Annulla</Text>
                  </Pressable>
                  <Pressable
                    onPress={confirmShare}
                    style={[styles.confirmBtn, styles.confirmBtnPrimary]}
                    testID="badge-share-confirm-btn"
                  >
                    <Text style={styles.confirmBtnPrimaryTxt}>Condividi</Text>
                  </Pressable>
                </View>
              </View>
            </View>
          )}

          {/* Post-share toast — same absolute-overlay pattern. Auto
              dismisses when the user taps anywhere on the sheet. */}
          {toast && (
            <Pressable
              onPress={() => setToast(null)}
              style={styles.toastBackdrop}
              testID={toast.kind === "success" ? "badge-share-toast-success" : "badge-share-toast-error"}
            >
              <View
                style={[
                  styles.toastSheet,
                  toast.kind === "success" ? styles.toastSuccess : styles.toastError,
                ]}
              >
                <Ionicons
                  name={toast.kind === "success" ? "checkmark-circle" : "alert-circle"}
                  size={22}
                  color={toast.kind === "success" ? "#0F8F4B" : "#B71C1C"}
                />
                <View style={{ flex: 1 }}>
                  <Text style={[styles.toastTitle, toast.kind === "error" && { color: "#7F1D1D" }]}>
                    {toast.title}
                  </Text>
                  <Text style={[styles.toastMsg, toast.kind === "error" && { color: "#7F1D1D" }]}>
                    {toast.message}
                  </Text>
                </View>
                <Ionicons name="close" size={16} color={colors.muted} />
              </View>
            </Pressable>
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
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    maxHeight: "88%",
    minHeight: "70%",
    paddingTop: spacing.md,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "800",
    letterSpacing: 1.5,
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
    borderRadius: radius.md,
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
  // ── Inline confirm sheet (Alert.alert replacement) ──
  confirmBackdrop: {
    position: "absolute",
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.lg,
  },
  confirmSheet: {
    width: "100%",
    maxWidth: 360,
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: spacing.lg,
    alignItems: "center",
    gap: spacing.sm,
  },
  confirmEmojiWrap: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 4,
  },
  confirmEmoji: {
    fontSize: 34,
    lineHeight: 38,
    textAlign: "center",
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.md,
    fontWeight: "800",
    textAlign: "center",
  },
  confirmBody: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    textAlign: "center",
    lineHeight: 20,
  },
  confirmRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.md,
    width: "100%",
  },
  confirmBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
  },
  confirmBtnSecondary: {
    backgroundColor: colors.surfaceSecondary || colors.surfaceTertiary,
  },
  confirmBtnSecondaryTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
  },
  confirmBtnPrimary: {
    backgroundColor: colors.brandPrimary,
  },
  confirmBtnPrimaryTxt: {
    color: "#fff",
    fontSize: font.sizes.sm,
    fontWeight: "800",
  },
  // ── Inline toast ──
  toastBackdrop: {
    position: "absolute",
    left: 0, right: 0, bottom: 0,
    padding: spacing.md,
  },
  toastSheet: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.sm + 2,
    borderRadius: 12,
    borderWidth: 1,
  },
  toastSuccess: {
    backgroundColor: "#E6F7EE",
    borderColor: "#B5E5C6",
  },
  toastError: {
    backgroundColor: "#FDECEA",
    borderColor: "#F5C2C0",
  },
  toastTitle: {
    color: "#0F5C2E",
    fontSize: font.sizes.sm,
    fontWeight: "700",
    marginBottom: 2,
  },
  toastMsg: {
    color: "#0F5C2E",
    fontSize: font.sizes.xs,
    lineHeight: 16,
  },
});
