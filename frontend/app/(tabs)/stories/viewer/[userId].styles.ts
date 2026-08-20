// Styles for the fullscreen story viewer.
//
// Extracted from `[userId].tsx` purely to keep the main component
// file focused on state/timer logic. No behaviour change — this
// file is a static StyleSheet consumed by the viewer component.
import { StyleSheet } from "react-native";
import { colors, spacing, font } from "@/src/theme";

export const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#000" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  progressStrip: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    gap: 4,
  },
  progressTrack: {
    flex: 1,
    height: 3,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.28)",
    overflow: "hidden",
  },
  progressFill: {
    // Full-width fill anchored to left; the visible portion is driven
    // by `transform: scaleX(pct)`. Left origin so the fill grows from
    // the left edge instead of the center (default). This is set
    // per-platform: on web we use `transformOrigin`; on native the
    // View is fully-wide by default, so scaleX shrinks around center
    // unless we pin the origin. `transformOrigin` on the style is
    // supported by RN >=0.74 & react-native-web.
    height: "100%",
    width: "100%",
    backgroundColor: "#fff",
    transformOrigin: "left center",
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
  },
  headerAuthorPressable: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  headerAvatarWrap: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 2,
    borderColor: "#fff",
    padding: 1,
  },
  headerAvatar: {
    width: 30,
    height: 30,
    borderRadius: 15,
  },
  headerAvatarFallback: {
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  headerNick: {
    color: "#fff",
    fontSize: font.sizes.sm,
    fontWeight: "700",
  },
  headerTime: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 11,
    marginTop: 1,
  },
  headerBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    // NO horizontal padding here — the tap zones (position:absolute
    // left:0 / right:0) must cover the ENTIRE screen width, edge to
    // edge, so a user resting their thumb near the border still hits
    // prev/next. Card padding is applied via `cardWrap` below.
    justifyContent: "center",
  },
  cardWrap: {
    paddingHorizontal: spacing.md,
    alignItems: "stretch",
  },
  tapZone: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: "50%",
    zIndex: 0,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    overflow: "hidden",
    zIndex: 1,
  },
  cardImage: {
    width: "100%",
    height: 180,
    backgroundColor: colors.surfaceTertiary,
  },
  cardBody: {
    padding: spacing.md,
  },
  cardCat: {
    color: colors.brandPrimary,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.5,
    marginBottom: 4,
  },
  cardTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    lineHeight: 24,
  },
  cardVsRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: spacing.md,
    gap: spacing.sm,
  },
  cardParty: {
    flex: 1,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    borderRadius: 6,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  cardPartyTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    textAlign: "center",
    letterSpacing: 0.5,
  },
  cardVs: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    fontWeight: "700",
  },
  cardCta: {
    marginTop: 0,
    marginHorizontal: 0,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
  },
  cardCtaTxt: {
    color: colors.brandPrimary,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  openFeudBtn: {
    marginTop: spacing.sm,
    alignSelf: "center",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 2,
    backgroundColor: colors.surface,
    borderRadius: 999,
    borderWidth: 1.5,
    borderColor: colors.brandSecondary,
    zIndex: 2,
  },
  openFeudTxt: {
    color: colors.brandSecondary,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 1.5,
  },
  cardMissing: {
    padding: spacing.xl,
    alignItems: "center",
    gap: spacing.sm,
  },
  cardMissingTxt: {
    color: colors.muted,
    fontSize: font.sizes.sm,
  },
  // ─── Badge showcase card ─────────────────────────────────────
  badgeCard: {
    minHeight: 320,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.xl,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
  },
  badgeCat: {
    color: "rgba(255,255,255,0.85)",
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 2,
  },
  badgeEmoji: {
    fontSize: 96,
    lineHeight: 108,
    textAlign: "center",
  },
  badgeName: {
    color: "#fff",
    fontSize: 22,
    fontWeight: "800",
    textAlign: "center",
    letterSpacing: 0.5,
  },
  badgeTierChip: {
    backgroundColor: "rgba(0,0,0,0.35)",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
  },
  badgeTierTxt: {
    color: "#fff",
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1.2,
  },
  badgeUnlockedTxt: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 3,
    marginTop: spacing.xs,
  },
  commentBox: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  commentTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    lineHeight: 20,
  },
  replyRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
    paddingTop: spacing.sm,
    gap: spacing.sm,
  },
  replyInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.5)",
    borderRadius: 999,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    color: "#fff",
    fontSize: font.sizes.sm,
  },
  replySend: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.brandSecondary,
    alignItems: "center",
    justifyContent: "center",
  },
  // Delete-confirm modal — cross-platform alternative to
  // Alert.alert(multiple buttons) which doesn't work on RN Web.
  confirmBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
  },
  confirmSheet: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: spacing.lg,
    maxWidth: 380,
    width: "100%",
  },
  confirmTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    fontWeight: "700",
    letterSpacing: 1,
    marginBottom: spacing.sm,
  },
  confirmMsg: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    lineHeight: 20,
    marginBottom: spacing.lg,
  },
  confirmBtnRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  confirmBtn: {
    flex: 1,
    paddingVertical: spacing.md,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 6,
  },
  confirmBtnCancel: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  confirmBtnDelete: {
    backgroundColor: colors.brandPrimary,
  },
  confirmBtnCancelTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  confirmBtnDeleteTxt: {
    color: colors.onBrandPrimary,
    fontSize: font.sizes.xs,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
});
