import React from "react";
import { View, Text, StyleSheet, Modal, Pressable, ActivityIndicator, ScrollView } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";

/**
 * Profession picker modal — presents a scrollable list of predefined
 * professions and lets the user select one. Saving is delegated to the
 * parent through `onSelect` so it can be coordinated with the profile
 * refresh + prefs-onboarding fallback flow.
 *
 * Extracted from profile.tsx as part of the profile refactor. Purely
 * presentational — owns no state of its own.
 */
export type ProfessionModalProps = {
  visible: boolean;
  onClose: () => void;
  professions: string[];
  currentValue?: string | null;
  saving: boolean;
  onSelect: (value: string) => void;
};

export default function ProfessionModal({
  visible,
  onClose,
  professions,
  currentValue,
  saving,
  onSelect,
}: ProfessionModalProps) {
  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <View style={styles.modalBackdrop}>
        <View style={styles.modalSheet} testID="profession-modal">
          <View style={styles.modalHead}>
            <Text style={styles.modalTitle}>PROFESSIONE</Text>
            <Pressable onPress={onClose} testID="profession-modal-close" hitSlop={10}>
              <Ionicons name="close" size={26} color={colors.onSurfaceInverse} />
            </Pressable>
          </View>
          {saving && (
            <View style={styles.savingBar}>
              <ActivityIndicator color={colors.brandPrimary} />
            </View>
          )}
          <ScrollView contentContainerStyle={{ paddingBottom: spacing.lg }}>
            {professions.map((p) => {
              const isSel = currentValue === p;
              return (
                <Pressable
                  key={p}
                  onPress={() => onSelect(p)}
                  disabled={saving}
                  style={[styles.professionItem, isSel && styles.professionItemOn]}
                  testID={`profession-opt-${p.replace(/[^a-zA-Z0-9]/g, "-")}`}
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
  );
}

const styles = StyleSheet.create({
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.surface, borderTopWidth: 2, borderColor: colors.border, maxHeight: "85%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.lg, borderBottomWidth: 2, borderColor: colors.border, backgroundColor: colors.surfaceInverse },
  modalTitle: { color: colors.onSurfaceInverse, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  savingBar: { paddingVertical: spacing.sm, alignItems: "center", backgroundColor: colors.surfaceSecondary },
  professionItem: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  professionItemOn: { backgroundColor: colors.brandPrimary },
  professionItemTxt: { fontSize: font.sizes.lg, color: colors.onSurface },
  professionItemTxtOn: { color: colors.onBrandPrimary, fontWeight: "500" },
});
