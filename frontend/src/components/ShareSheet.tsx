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
  Alert,
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

/**
 * Copy a string to the clipboard using the platform-appropriate API.
 * Falls back to expo-clipboard on native, navigator.clipboard on web.
 */
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

/**
 * Try to open a URL on web (new tab) or via Linking on native.
 * Returns true if we believe the URL opened.
 */
async function openUrl(target: string): Promise<boolean> {
  try {
    if (Platform.OS === "web" && typeof window !== "undefined" && window.open) {
      // Some deep-link schemes (fb-messenger://, instagram://) will only
      // work if the app is installed. window.open on those will fail
      // silently but doesn't throw — we still return true and let the
      // caller show a helpful toast.
      window.open(target, "_blank", "noopener,noreferrer");
      return true;
    }
    await Linking.openURL(target);
    return true;
  } catch {
    return false;
  }
}

/**
 * Cross-platform "notify user" helper — Alert on native, in-sheet banner on web
 * (Alert.alert is unreliable on RN-Web preview).
 */
function notify(msg: string, showBanner: (m: string) => void) {
  if (Platform.OS === "web") {
    showBanner(msg);
  } else {
    Alert.alert("Populus", msg);
  }
}

export default function ShareSheet({ visible, onClose, url, title, message, onCopy }: Props) {
  // Ephemeral banner shown at the top of the sheet on web (Alert is unreliable
  // there). Auto-clears after 3.5s.
  const [banner, setBanner] = useState<string | null>(null);
  const showBanner = (m: string) => {
    setBanner(m);
    setTimeout(() => setBanner((cur) => (cur === m ? null : cur)), 3500);
  };

  const shareVia = async (key: OptionKey) => {
    // Instagram — no URL-based DM/story share exists on web. Best UX:
    // copy the link to the clipboard, open Instagram, and tell the user
    // to paste it. On native we try the instagram:// deep link first so
    // the app opens directly if installed.
    if (key === "instagram") {
      await copyToClipboard(url);
      const opened = Platform.OS !== "web"
        ? await openUrl("instagram://app").catch(() => false) || await openUrl("https://www.instagram.com/")
        : await openUrl("https://www.instagram.com/");
      notify(
        opened
          ? "Link copiato! Incollalo in una Storia, un post o un DM di Instagram."
          : "Link copiato negli appunti. Apri Instagram e incollalo dove vuoi.",
        showBanner,
      );
      return;
    }

    // Messenger — the FB Send Dialog requires a whitelisted App ID and is
    // unreliable in modern browsers. On native we try the fb-messenger://
    // deep link (needs the app). On web we open messenger.com and copy the
    // link so the user can paste it into a conversation.
    if (key === "messenger") {
      await copyToClipboard(url);
      let opened = false;
      if (Platform.OS !== "web") {
        opened = await openUrl(`fb-messenger://share/?link=${encodeURIComponent(url)}`).catch(() => false);
        if (!opened) opened = await openUrl("https://www.messenger.com/");
      } else {
        opened = await openUrl("https://www.messenger.com/");
      }
      notify(
        opened
          ? "Link copiato! Incolla il link nella conversazione Messenger che vuoi."
          : "Link copiato negli appunti. Apri Messenger e incolla il link in una chat.",
        showBanner,
      );
      return;
    }

    // Standard URL-based share intents that always work on any browser.
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

          {banner ? (
            <View style={styles.banner} testID="share-banner">
              <Ionicons name="checkmark-circle" size={18} color="#16A34A" />
              <Text style={styles.bannerTxt}>{banner}</Text>
            </View>
          ) : null}

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

          <Text style={styles.hint}>
            {"Instagram e Messenger non permettono la condivisione diretta di link: il link viene copiato e l'app viene aperta per l'incolla."}
          </Text>
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
  banner: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#DCFCE7",
    borderColor: "#16A34A",
    borderWidth: 1,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.sm,
  },
  bannerTxt: { flex: 1, color: "#166534", fontSize: font.sizes.sm },
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
  hint: {
    color: colors.muted,
    fontSize: font.sizes.xs,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.sm,
    fontStyle: "italic",
  },
});
