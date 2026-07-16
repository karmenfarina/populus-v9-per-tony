import React from "react";
import { View, Text, StyleSheet, Modal, Pressable, ScrollView, Platform } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";

type Props = {
  visible: boolean;
  onClose: () => void;
  url: string;
  title: string;
  message: string;
  onCopy: () => void;
};

type Option = {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  buildUrl: (u: string, t: string, m: string) => string;
};

// Standard social-share URL schemes. All work in any modern browser AND deep-
// link to the installed native app on mobile when present. No SDK/key needed.
const OPTIONS: Option[] = [
  {
    key: "whatsapp", label: "WhatsApp", icon: "logo-whatsapp", color: "#25D366",
    buildUrl: (u, _t, m) => `https://wa.me/?text=${encodeURIComponent(`${m}\n${u}`)}`,
  },
  {
    key: "telegram", label: "Telegram", icon: "paper-plane", color: "#26A5E4",
    buildUrl: (u, _t, m) => `https://t.me/share/url?url=${encodeURIComponent(u)}&text=${encodeURIComponent(m)}`,
  },
  {
    key: "twitter", label: "X (Twitter)", icon: "logo-twitter", color: "#000000",
    buildUrl: (u, _t, m) => `https://twitter.com/intent/tweet?text=${encodeURIComponent(m)}&url=${encodeURIComponent(u)}`,
  },
  {
    key: "facebook", label: "Facebook", icon: "logo-facebook", color: "#1877F2",
    buildUrl: (u, _t, _m) => `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(u)}`,
  },
  {
    key: "email", label: "Email", icon: "mail", color: "#6B7280",
    buildUrl: (u, t, m) => `mailto:?subject=${encodeURIComponent(t)}&body=${encodeURIComponent(`${m}\n${u}`)}`,
  },
];

export default function ShareSheet({ visible, onClose, url, title, message, onCopy }: Props) {
  const openExternal = (target: string) => {
    if (typeof window !== "undefined" && window.open) {
      window.open(target, "_blank", "noopener,noreferrer");
    } else {
      // React Native web fallback: use Linking (imported lazily to avoid RN deps here)
      import("react-native").then(({ Linking }) => Linking.openURL(target).catch(() => {}));
    }
    onClose();
  };

  return (
    <Modal animationType="slide" transparent visible={visible} onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={() => { /* consume */ }}>
          <View style={styles.handleWrap}><View style={styles.handle} /></View>
          <View style={styles.header}>
            <Text style={styles.title}>CONDIVIDI</Text>
            <Pressable onPress={onClose} testID="share-sheet-close" hitSlop={8}>
              <Ionicons name="close" size={22} color={colors.onSurface} />
            </Pressable>
          </View>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.optionsRow}
          >
            {OPTIONS.map((o) => (
              <Pressable
                key={o.key}
                onPress={() => openExternal(o.buildUrl(url, title, message))}
                testID={`share-${o.key}`}
                style={styles.optionItem}
              >
                <View style={[styles.optionIcon, { backgroundColor: o.color }]}>
                  <Ionicons name={o.icon} size={26} color="#FFFFFF" />
                </View>
                <Text style={styles.optionLabel}>{o.label}</Text>
              </Pressable>
            ))}
            <Pressable
              onPress={() => { onCopy(); onClose(); }}
              testID="share-copy"
              style={styles.optionItem}
            >
              <View style={[styles.optionIcon, { backgroundColor: colors.brandSecondary }]}>
                <Ionicons name="copy" size={26} color="#FFFFFF" />
              </View>
              <Text style={styles.optionLabel}>Copia link</Text>
            </Pressable>
          </ScrollView>
          <View style={styles.urlBox}>
            <Text style={styles.urlLabel} numberOfLines={1}>{url}</Text>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.xs,
    paddingBottom: spacing.xl,
  },
  handleWrap: { alignItems: "center", paddingVertical: spacing.xs },
  handle: { width: 44, height: 4, borderRadius: 2, backgroundColor: colors.border },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingVertical: spacing.sm, paddingHorizontal: spacing.sm,
    borderBottomWidth: 2, borderColor: colors.border,
    marginBottom: spacing.md,
  },
  title: { color: colors.onSurface, fontSize: font.sizes.xl, letterSpacing: 2, fontWeight: "500" },
  optionsRow: { paddingHorizontal: spacing.sm, gap: spacing.md, paddingBottom: spacing.sm },
  optionItem: { alignItems: "center", justifyContent: "center", width: 72, marginRight: spacing.md },
  optionIcon: {
    width: 56, height: 56, borderRadius: 28,
    alignItems: "center", justifyContent: "center",
    borderWidth: 2, borderColor: colors.onSurface,
    marginBottom: spacing.xs,
  },
  optionLabel: { color: colors.onSurface, fontSize: font.sizes.xs, textAlign: "center" },
  urlBox: {
    marginTop: spacing.md,
    padding: spacing.sm,
    borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  urlLabel: { color: colors.muted, fontSize: font.sizes.xs, fontFamily: Platform.OS === "web" ? "monospace" : undefined },
});
