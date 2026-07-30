import React from "react";
import { View, Text, StyleSheet, Modal, Pressable, ScrollView, ActivityIndicator } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font, radius } from "@/src/theme";

/**
 * "MODIFICA ARGOMENTI" (Edit favourite topics) modal — presented on the
 * Profile screen when the user wants to change which categories the
 * feed prioritises.
 *
 * Extracted from profile.tsx. Purely presentational — the selection
 * state, the "all/none" toggle logic and the save action stay in the
 * parent so we can keep prefs in sync with the rest of profile UI
 * without introducing extra state duplication.
 */
export type PrefsModalProps = {
  visible: boolean;
  onClose: () => void;
  categories: { id: string; label: string }[];
  selected: Set<string>;
  onToggleOne: (id: string) => void;
  onToggleAll: () => void;
  allSelected: boolean;
  onSave: () => void;
  saving: boolean;
  error: string | null;
};

export default function PrefsModal({
  visible,
  onClose,
  categories,
  selected,
  onToggleOne,
  onToggleAll,
  allSelected,
  onSave,
  saving,
  error,
}: PrefsModalProps) {
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={styles.modalSheet} testID="prefs-modal">
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>MODIFICA ARGOMENTI</Text>
            <Pressable onPress={onClose} testID="prefs-modal-close">
              <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalBody}>
            <Pressable onPress={onToggleAll} testID="prefs-select-all" style={styles.prefsSelectAllRow}>
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
              {categories.map((c) => {
                const on = selected.has(c.id);
                return (
                  <Pressable
                    key={c.id}
                    onPress={() => onToggleOne(c.id)}
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
            {error && <Text style={styles.prefsErr} testID="prefs-error">{error}</Text>}
          </ScrollView>
          <Pressable onPress={onSave} disabled={saving} testID="prefs-save" style={styles.prefsSaveBtn}>
            {saving ? (
              <ActivityIndicator color={colors.onBrandPrimary} />
            ) : (
              <Text style={styles.prefsSaveTxt}>SALVA</Text>
            )}
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  modalTitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  modalBody: { padding: spacing.lg, gap: spacing.md },
  prefsSelectAllRow: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    backgroundColor: colors.surfaceSecondary,
  },
  prefsSelectAllTxt: { fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "700", color: colors.onSurface },
  prefsCatsGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  // Selectable chip in the edit-preferences sheet.
  //   OFF → transparent-on-dark with a subtle grey outline (surfaceSecondary
  //         + border), fully rounded (pill).
  //   ON  → red fill (brandPrimary), no outline. Consistent with the visual
  //         language used for the read-only chips in the profile summary.
  prefsCatChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
  },
  prefsCatChipOn: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  prefsCatTxt: { fontSize: font.sizes.base, color: colors.onSurface, fontWeight: "500" },
  prefsCatTxtOn: { color: colors.onBrandPrimary, fontWeight: "700" },
  prefsErr: { color: colors.error, borderWidth: 2, borderColor: colors.error, padding: spacing.sm, fontSize: font.sizes.base },
  prefsSaveBtn: { backgroundColor: colors.brandPrimary, borderTopWidth: 2, borderColor: colors.border, paddingVertical: spacing.lg, alignItems: "center" },
  prefsSaveTxt: { color: colors.onBrandPrimary, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
});
