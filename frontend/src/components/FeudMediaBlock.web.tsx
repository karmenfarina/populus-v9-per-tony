import React, { createElement } from "react";
import { StyleSheet, View, Text, Image, Pressable, Linking, Platform } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { FeudMedia } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

/**
 * Web variant of FeudMediaBlock — uses <iframe> and <video> instead of the
 * native WebView / expo-video (which don't work on react-native-web).
 * Same copyright policy: only official YouTube embed URLs and source-declared
 * MP4/HLS URLs are used.
 */
export default function FeudMediaBlock({ media, fallbackImage, title }: {
  media: FeudMedia | null | undefined;
  fallbackImage?: string | null;
  title: string;
}) {
  if (!media) return null;
  if (media.type === "youtube" && media.embed_url) {
    return <YouTubeIframe media={media} title={title} />;
  }
  if (media.type === "video" && media.video_url) {
    return <HtmlVideo media={media} fallbackImage={fallbackImage || media.thumbnail} />;
  }
  if (media.type === "image" && (media.image_url || fallbackImage)) {
    return <MediaImage url={(media.image_url || fallbackImage)!} media={media} />;
  }
  return null;
}

function YouTubeIframe({ media, title }: { media: FeudMedia; title: string }) {
  // React Native for Web renders <View> as <div>. We inject a raw iframe via a
  // web-only element. Using createElement to keep types happy.
  const iframe: any = createElement("iframe", {
    src: media.embed_url,
    title,
    allow: "accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share",
    allowFullScreen: true,
    loading: "lazy",
    style: { position: "absolute", top: 0, left: 0, width: "100%", height: "100%", border: 0 },
  });
  return (
    <View style={styles.wrap} testID="feud-media-youtube">
      <View style={styles.aspectBox}>{iframe}</View>
      <MediaFooter
        icon="logo-youtube"
        label={media.channel ? `YouTube · ${media.channel}` : "YouTube"}
        onOpen={media.watch_url ? () => Linking.openURL(media.watch_url!) : undefined}
        provenance={media.provenance}
      />
    </View>
  );
}

function HtmlVideo({ media, fallbackImage }: { media: FeudMedia; fallbackImage?: string | null }) {
  const video: any = createElement("video", {
    src: media.video_url,
    controls: true,
    playsInline: true,
    poster: fallbackImage || undefined,
    style: { width: "100%", height: "100%", background: "#000" },
  });
  return (
    <View style={styles.wrap} testID="feud-media-video">
      <View style={styles.aspectBox}>{video}</View>
      <MediaFooter
        icon="videocam-outline"
        label={media.source_domain || "Video"}
        provenance={media.provenance}
      />
    </View>
  );
}

function MediaImage({ url, media }: { url: string; media: FeudMedia }) {
  return (
    <View style={styles.wrap} testID="feud-media-image">
      <Image source={{ uri: url }} style={styles.imgFull} resizeMode="cover" />
      <MediaFooter
        icon="image-outline"
        label={media.source_domain || "Immagine"}
        provenance={media.provenance}
      />
    </View>
  );
}

function MediaFooter({ icon, label, onOpen, provenance }: {
  icon: any;
  label: string;
  onOpen?: () => void;
  provenance?: string;
}) {
  return (
    <View style={styles.footer}>
      <Ionicons name={icon} size={14} color={colors.brandSecondary} />
      <Text style={styles.footerLabel} numberOfLines={1}>{label}</Text>
      {provenance === "youtube_search" && (
        <Text style={styles.footerHint} testID="media-provenance-search">· suggerito</Text>
      )}
      {onOpen && (
        <Pressable onPress={onOpen} hitSlop={8} testID="media-open-source">
          <Text style={styles.openLink}>APRI ›</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderWidth: 2, borderColor: colors.border, backgroundColor: "#000", marginBottom: spacing.md },
  aspectBox: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000", position: "relative" as any, overflow: "hidden" },
  imgFull: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000" },
  footer: { flexDirection: "row", alignItems: "center", gap: spacing.xs, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceInverse, borderTopWidth: 2, borderColor: colors.border },
  footerLabel: { flex: 1, color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: Platform.OS === "ios" ? "500" : "400" },
  footerHint: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 1 },
  openLink: { color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500" },
});
