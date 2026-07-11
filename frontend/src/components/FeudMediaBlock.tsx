import { useVideoPlayer, VideoView } from "expo-video";
import { StyleSheet, View, Text, Image, Pressable, Linking, Platform } from "react-native";
import { WebView } from "react-native-webview";
import { Ionicons } from "@expo/vector-icons";
import { FeudMedia } from "@/src/api";
import { colors, spacing, font } from "@/src/theme";

/**
 * Renders a copyright-safe embedded video (YouTube via official iframe or
 * direct MP4/HLS via expo-video) OR a large image (with source attribution).
 *
 * All content is either:
 *  - Streamed from the publisher's CDN (og:image)
 *  - Embedded via YouTube's official iframe (respects uploader's embed setting)
 *  - Embedded via direct MP4/HLS URL declared by the source in `og:video`
 * Nothing is re-hosted on our servers.
 */
export default function FeudMediaBlock({ media, fallbackImage, title }: {
  media: FeudMedia | null | undefined;
  fallbackImage?: string | null;
  title: string;
}) {
  if (!media) return null;

  if (media.type === "youtube" && media.embed_url) {
    return <YouTubeEmbed media={media} title={title} />;
  }
  if (media.type === "video" && media.video_url) {
    return <DirectVideo media={media} fallbackImage={fallbackImage || media.thumbnail} />;
  }
  if (media.type === "image" && (media.image_url || fallbackImage)) {
    return <MediaImage url={(media.image_url || fallbackImage)!} media={media} />;
  }
  return null;
}

function YouTubeEmbed({ media, title }: { media: FeudMedia; title: string }) {
  const html = `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>html,body{margin:0;padding:0;background:#000;height:100%;overflow:hidden}
iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:0}</style></head>
<body><iframe src="${media.embed_url}" allow="accelerometer;clipboard-write;encrypted-media;gyroscope;picture-in-picture;web-share" allowfullscreen loading="lazy"></iframe></body></html>`;
  return (
    <View style={styles.wrap} testID="feud-media-youtube">
      <View style={styles.aspectBox}>
        <WebView
          source={{ html }}
          style={styles.webview}
          javaScriptEnabled
          domStorageEnabled
          allowsInlineMediaPlayback
          mediaPlaybackRequiresUserAction={false}
          scrollEnabled={false}
          originWhitelist={["*"]}
          androidLayerType="hardware"
        />
      </View>
      <MediaFooter
        icon="logo-youtube"
        label={media.channel ? `YouTube · ${media.channel}` : "YouTube"}
        onOpen={media.watch_url ? () => Linking.openURL(media.watch_url!) : undefined}
        provenance={media.provenance}
      />
    </View>
  );
}

function DirectVideo({ media, fallbackImage }: { media: FeudMedia; fallbackImage?: string | null }) {
  const player = useVideoPlayer(media.video_url!, (p) => {
    p.loop = false;
    p.muted = false;
  });
  return (
    <View style={styles.wrap} testID="feud-media-video">
      <View style={styles.aspectBox}>
        <VideoView
          player={player}
          style={styles.webview}
          contentFit="contain"
          nativeControls
          allowsFullscreen
          allowsPictureInPicture
        />
      </View>
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
  aspectBox: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000" },
  webview: { flex: 1, backgroundColor: "#000" },
  imgFull: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000" },
  footer: { flexDirection: "row", alignItems: "center", gap: spacing.xs, paddingHorizontal: spacing.sm, paddingVertical: 6, backgroundColor: colors.surfaceInverse, borderTopWidth: 2, borderColor: colors.border },
  footerLabel: { flex: 1, color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: Platform.OS === "ios" ? "500" : "400" },
  footerHint: { color: colors.muted, fontSize: font.sizes.xs, letterSpacing: 1 },
  openLink: { color: colors.brandSecondary, fontSize: font.sizes.xs, letterSpacing: 1, fontWeight: "500" },
});
