/**
 * ConfirmModal
 * ─────────────────────────────────────────────────────────────────────
 * Themed confirmation dialog used across the app for destructive actions
 * (delete comment / reply / message / chat / friend / block / ...).
 *
 * Design goals
 *  • Consistent with the dark theme (yellow/red accents, rounded corners).
 *  • Icon + title + body + pair of pill-shaped buttons — matches the visual
 *    language already used in `/circle/[userId].tsx` and `messages/*`.
 *  • Cross-platform: never falls back to `Alert.alert` / `window.confirm`,
 *    which the user described as "brutta e grigia con delle strane scritte
 *    di intestazione".
 *
 * Usage:
 *   const [modal, setModal] = useState<{ ... } | null>(null);
 *   ...
 *   <ConfirmModal
 *     visible={!!modal}
 *     title="Elimina commento"
 *     body="Il commento e tutte le sue risposte verranno eliminati."
 *     confirmLabel="ELIMINA"
 *     cancelLabel="ANNULLA"
 *     danger
 *     iconName="trash-outline"
 *     onCancel={() => setModal(null)}
 *     onConfirm={() => { modal?.action(); setModal(null); }}
 *   />
 */
import React from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font, radius } from "../theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

export type ConfirmModalProps = {
  visible: boolean;
  title: string;
  body?: string;
  /** Text for the confirm (right) button. Uppercase looks best. */
  confirmLabel?: string;
  /** Text for the cancel (left) button. Uppercase looks best. */
  cancelLabel?: string;
  /** When true the confirm button is red — for destructive actions. */
  danger?: boolean;
  /** Ionicon name shown in the circle on top. Defaults to trash-outline
   *  when `danger`, alert-circle-outline otherwise. */
  iconName?: IoniconName;
  onConfirm: () => void;
  onCancel: () => void;
  /** Optional testID prefix. Emits `${testID}-confirm` and `${testID}-cancel`. */
  testID?: string;
};

export default function ConfirmModal({
  visible,
  title,
  body,
  confirmLabel = "CONFERMA",
  cancelLabel = "ANNULLA",
  danger = false,
  iconName,
  onConfirm,
  onCancel,
  testID = "confirm",
}: ConfirmModalProps) {
  const finalIcon: IoniconName =
    iconName || (danger ? "trash-outline" : "alert-circle-outline");
  const accent = danger ? colors.error : colors.brandPrimary;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onCancel}
    >
      <Pressable style={styles.backdrop} onPress={onCancel}>
        <Pressable style={styles.card} onPress={(e) => e.stopPropagation?.()}>
          <View style={[styles.iconWrap, { borderColor: accent }]}>
            <Ionicons name={finalIcon} size={26} color={accent} />
          </View>
          <Text style={styles.title}>{title}</Text>
          {body ? <Text style={styles.body}>{body}</Text> : null}
          <View style={styles.btnRow}>
            <Pressable
              onPress={onCancel}
              style={[styles.btn, styles.btnGhost]}
              testID={`${testID}-cancel`}
            >
              <Text style={styles.btnGhostTxt}>{cancelLabel}</Text>
            </Pressable>
            <Pressable
              onPress={onConfirm}
              style={[
                styles.btn,
                danger ? styles.btnDanger : styles.btnPrimary,
              ]}
              testID={`${testID}-confirm`}
            >
              <Text
                style={
                  danger ? styles.btnDangerTxt : styles.btnPrimaryTxt
                }
              >
                {confirmLabel}
              </Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.65)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
  },
  card: {
    width: "100%",
    maxWidth: 340,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.xl,
    gap: spacing.md,
    alignItems: "center",
  },
  iconWrap: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 1.5,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.xs,
  },
  title: {
    color: colors.onSurface,
    fontSize: font.sizes.xl,
    fontWeight: "800",
    letterSpacing: 0.5,
    textAlign: "center",
  },
  body: {
    color: colors.muted,
    fontSize: font.sizes.base,
    lineHeight: 22,
    textAlign: "center",
  },
  btnRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
    width: "100%",
  },
  btn: {
    flex: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    borderRadius: radius.pill,
  },
  btnGhost: {
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    backgroundColor: "transparent",
  },
  btnGhostTxt: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
  btnDanger: { backgroundColor: colors.error },
  btnDangerTxt: {
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
  btnPrimary: { backgroundColor: colors.brandPrimary },
  btnPrimaryTxt: {
    color: colors.onBrandPrimary,
    fontWeight: "800",
    fontSize: font.sizes.sm,
    letterSpacing: 1,
  },
});
