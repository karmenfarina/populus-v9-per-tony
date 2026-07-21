import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Modal,
  Pressable,
  ScrollView,
  Platform,
  Linking,
} from "react-native";
import * as Clipboard from "expo-clipboard";
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

type OptionKey =
  | "whatsapp"
  | "telegram"
  | "twitter"
  | "facebook"
  | "messenger"
  | "instagram"
  | "email";

type Option = {
  key: OptionKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
};

const OPTIONS: Option[] = [
  { key: "whatsapp",  label: "WhatsApp",   icon: "logo-whatsapp",  color: "#25D366" },
  { key: "telegram",  label: "Telegram",   icon: "paper-plane",    color: "#26A5E4" },
  { key: "twitter",   label: "X (Twitter)",icon: "logo-twitter",   color: "#000000" },
  { key: "facebook",  label: "Facebook",   icon: "logo-facebook",  color: "#1877F2" },
  { key: "messenger", label: "Messenger",  icon: "chatbubbles",    color: "#0084FF" },
  { key: "instagram", label: "Instagram",  icon: "logo-instagram", color: "#E1306C" },
  { key: "email",     label: "Email",      icon: "mail",           color: "#6B7280" },
];

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (Platform.OS === "web" && typeof navigator !== "undefined" && (navigator as any).clipboard) {
      await (navigator as any).clipboard.writeText(text);
      return true;
    }
    await Clipboard.setStringAsync(text);
    return true;
  } catch {
    return false;
  }
}

async function openUrl(target: string): Promise<boolean> {
  try {
    if (Platform.OS === "web" && typeof window !== "undefined" && window.open) {
      const w = window.open(target, "_blank", "noopener,noreferrer");
      // Some browsers block popups — if we get back null, fall back to same-tab redirect
      if (!w) {
        window.location.href = target;
      }
      return true;
    }
    await Linking.openURL(target);
    return true;
  } catch {
    return false;
  }
}

// Copy-and-paste guidance shown when the destination has no URL share API.
type PasteGuide = {
  key: "instagram" | "messenger";
  appLabel: string;
  color: string;
  icon: keyof typeof Ionicons.glyphMap;
  mobileScheme: string;
  webUrl: string;
  instructions: string;
};

const PASTE_GUIDES: Record<"instagram" | "messenger", PasteGuide> = {
  instagram: {
    key: "instagram",
    appLabel: "Instagram",
    color: "#E1306C",
    icon: "logo-instagram",
    mobileScheme: "instagram://app",
    webUrl: "https://www.instagram.com/",
    instructions:
      "Instagram non permette di aprire una chat con un link già pronto. Il link è stato copiato negli appunti: aprilo in un DM, una Storia o un post e incolla (tieni premuto → Incolla).",
  },
  messenger: {
    key: "messenger",
    appLabel: "Messenger",
    color: "#0084FF",
    icon: "chatbubbles",
    mobileScheme: "fb-messenger://",
    webUrl: "https://www.messenger.com/",
    instructions:
      "Messenger non permette la condivisione diretta da web. Il link è stato copiato negli appunti: apri una chat e incolla il link nel messaggio.",
  },
};

export default function ShareSheet({ visible, onClose, url, title, message, onCopy }: Props) {
  const [pasteGuide, setPasteGuide] = useState<PasteGuide | null>(null);
  const [pasteCopyOk, setPasteCopyOk] = useState<boolean>(false);

  const openPasteGuide = async (key: "instagram" | "messenger") => {
    const ok = await copyToClipboard(url);
    setPasteCopyOk(ok);
    setPasteGuide(PASTE_GUIDES[key]);
  };

  const openTargetAppFromGuide = async () => {
    if (!pasteGuide) return;
    // Try native scheme first, fallback to the web URL (which always opens).
    let opened = false;
    if (Platform.OS !== "web") {
      opened = await openUrl(
        pasteGuide.key === "messenger"
          ? `fb-messenger://share/?link=${encodeURIComponent(url)}`
          : pasteGuide.mobileScheme,
      );
    }
    if (!opened) {
      await openUrl(pasteGuide.webUrl);
    }
    setPasteGuide(null);
    onClose();
  };

  const shareVia = async (key: OptionKey) => {
    if (key === "instagram" || key === "messenger") {
      await openPasteGuide(key);
      return;
    }

    let target = "";
    switch (key) {
      case "whatsapp":
        target = `https://wa.me/?text=${encodeURIComponent(`${message}\n${url}`)}`;
        break;
      case "telegram":
        target = `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(message)}`;
        break;
      case "twitter":
        target = `https://twitter.com/intent/tweet?text=${encodeURIComponent(message)}&url=${encodeURIComponent(url)}`;
        break;
      case "facebook":
        target = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
        break;
      case "email":
        target = `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(`${message}\n${url}`)}`;
        break;
    }
    const opened = await openUrl(target);
    if (opened) onClose();
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
                onPress={() => shareVia(o.key)}
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

      {/* Paste-guide overlay for Instagram / Messenger. Sits above the share
          sheet and clearly explains what to do after we copy the link. */}
      <Modal
        animationType="fade"
        transparent
        visible={!!pasteGuide}
        onRequestClose={() => setPasteGuide(null)}
      >
        <Pressable style={styles.guideBackdrop} onPress={() => setPasteGuide(null)}>
          <Pressable style={styles.guideCard} onPress={() => { /* consume */ }}>
            {pasteGuide ? (
              <>
                <View style={[styles.guideIcon, { backgroundColor: pasteGuide.color }]}>
                  <Ionicons name={pasteGuide.icon} size={34} color="#FFFFFF" />
                </View>
                <Text style={styles.guideTitle}>
                  {pasteCopyOk ? "LINK COPIATO" : "APRI"} {pasteGuide.appLabel.toUpperCase()}
                </Text>
                <Text style={styles.guideBody}>{pasteGuide.instructions}</Text>

                <View style={styles.guideActions}>
                  <Pressable
                    testID="paste-guide-cancel"
                    onPress={() => setPasteGuide(null)}
                    style={styles.guideSecondaryBtn}
                  >
                    <Text style={styles.guideSecondaryTxt}>CHIUDI</Text>
                  </Pressable>
                  <Pressable
                    testID="paste-guide-open"
                    onPress={openTargetAppFromGuide}
                    style={[styles.guidePrimaryBtn, { backgroundColor: pasteGuide.color }]}
                  >
                    <Ionicons name="open-outline" size={16} color="#FFFFFF" />
                    <Text style={styles.guidePrimaryTxt}>APRI {pasteGuide.appLabel.toUpperCase()}</Text>
                  </Pressable>
                </View>
              </>
            ) : null}
          </Pressable>
        </Pressable>
      </Modal>
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

  // ────── paste-guide overlay ──────
  guideBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.lg,
  },
  guideCard: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: spacing.lg,
    alignItems: "center",
    gap: spacing.sm,
  },
  guideIcon: {
    width: 72,
    height: 72,
    borderRadius: 36,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.sm,
  },
  guideTitle: {
    color: colors.onSurface,
    fontSize: font.sizes.lg,
    letterSpacing: 1,
    fontWeight: "700",
    textAlign: "center",
  },
  guideBody: {
    color: colors.muted,
    fontSize: font.sizes.sm,
    textAlign: "center",
    lineHeight: 20,
    marginTop: spacing.xs,
  },
  guideActions: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.md,
    width: "100%",
  },
  guideSecondaryBtn: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  guideSecondaryTxt: {
    color: colors.onSurface,
    fontSize: font.sizes.sm,
    fontWeight: "700",
    letterSpacing: 1,
  },
  guidePrimaryBtn: {
    flex: 1.4,
    paddingVertical: spacing.md,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 6,
  },
  guidePrimaryTxt: {
    color: "#FFFFFF",
    fontSize: font.sizes.sm,
    fontWeight: "800",
    letterSpacing: 1,
  },
});
