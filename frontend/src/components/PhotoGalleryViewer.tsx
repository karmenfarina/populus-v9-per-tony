import { useEffect, useMemo, useState } from "react";
import {
  Modal, View, Image, Pressable, StyleSheet, Text,
  useWindowDimensions, StatusBar, Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { spacing } from "@/src/theme";

/**
 * Full-screen photo viewer.
 *
 * Renders a SINGLE photo at a time. Navigation is exclusively via the
 * on-screen ← / → buttons. No ScrollView, no gesture handlers — this
 * eliminates all touch-competition edge cases (swipe overshooting multiple
 * photos, first-tap-of-arrow being eaten by a phantom scroll gesture, etc.).
 *
 * - ← / → buttons on the sides advance one photo at a time.
 * - Counter (top-right) + dot indicator (bottom) track the active photo.
 * - ✕ button in the top-left closes the viewer.
 * - Modal itself has no tap-to-close backdrop (would conflict with the
 *   arrow buttons that live near the edges).
 */

export type GalleryPhoto = {
  photo_id?: string;
  data?: string;
  uri?: string;
};

type Props = {
  visible: boolean;
  photos: GalleryPhoto[];
  initialIndex?: number;
  onClose: () => void;
};

function resolveUri(p: GalleryPhoto): string {
  if (p.uri) return p.uri;
  if (!p.data) return "";
  if (p.data.startsWith("data:") || p.data.startsWith("file:") || p.data.startsWith("http")) return p.data;
  return `data:image/jpeg;base64,${p.data}`;
}

export function PhotoGalleryViewer({ visible, photos, initialIndex = 0, onClose }: Props) {
  const { width, height } = useWindowDimensions();
  const [activeIdx, setActiveIdx] = useState<number>(initialIndex);

  const data = useMemo(() => photos.filter((p) => resolveUri(p)), [photos]);
  const total = data.length;

  useEffect(() => {
    if (visible) setActiveIdx(initialIndex);
  }, [visible, initialIndex]);

  const goPrev = () => {
    setActiveIdx((i) => Math.max(0, i - 1));
  };
  const goNext = () => {
    setActiveIdx((i) => Math.min(total - 1, i + 1));
  };

  if (!visible || total === 0) return null;

  const currentUri = resolveUri(data[Math.max(0, Math.min(activeIdx, total - 1))]);

  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <StatusBar barStyle="light-content" backgroundColor="#000" />
      <View style={styles.container} testID="gallery-viewer-root">
        {/* Single image, swapped by activeIdx. No scroll container = no
            touch/pointer conflict with the arrow buttons. */}
        <View
          style={{ width, height, justifyContent: "center", alignItems: "center" }}
          testID={`gallery-page-${activeIdx}`}
        >
          <Image
            source={{ uri: currentUri }}
            style={{ width, height }}
            resizeMode="contain"
            testID="gallery-viewer-image"
          />
        </View>

        {/* Arrow buttons — always allocated when there's more than one
            photo, but INVISIBLE at the ends. Keeping the Pressable mounted
            (rather than conditionally removing it) means the touch target
            doesn't flicker in/out and the first tap on the opposite arrow
            always registers immediately. */}
        {total > 1 && (
          <>
            <Pressable
              onPress={goPrev}
              disabled={activeIdx === 0}
              hitSlop={12}
              style={[styles.arrowLeft, activeIdx === 0 && styles.arrowDisabled]}
              testID="gallery-viewer-prev"
            >
              <Ionicons name="chevron-back" size={28} color="#fff" />
            </Pressable>
            <Pressable
              onPress={goNext}
              disabled={activeIdx >= total - 1}
              hitSlop={12}
              style={[styles.arrowRight, activeIdx >= total - 1 && styles.arrowDisabled]}
              testID="gallery-viewer-next"
            >
              <Ionicons name="chevron-forward" size={28} color="#fff" />
            </Pressable>
          </>
        )}

        <View style={styles.topBar} pointerEvents="box-none">
          <Pressable onPress={onClose} hitSlop={12} style={styles.closeBtn} testID="gallery-viewer-close">
            <Ionicons name="close" size={28} color="#fff" />
          </Pressable>
          {total > 1 && (
            <View style={styles.counterPill}>
              <Text style={styles.counterTxt} testID="gallery-viewer-counter">
                {activeIdx + 1} / {total}
              </Text>
            </View>
          )}
        </View>

        {total > 1 && (
          <View style={styles.dotsRow} pointerEvents="none">
            {data.map((_, i) => (
              <View key={i} style={[styles.dot, i === activeIdx && styles.dotOn]} />
            ))}
          </View>
        )}
      </View>
    </Modal>
  );
}

export default PhotoGalleryViewer;

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#000" },
  topBar: {
    position: "absolute",
    top: 0, left: 0, right: 0,
    paddingTop: Platform.select({ ios: 54, android: 20, default: 20 }),
    paddingHorizontal: spacing.md,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    zIndex: 10,
  },
  closeBtn: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "center", alignItems: "center",
  },
  counterPill: {
    backgroundColor: "rgba(0,0,0,0.55)",
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999,
  },
  counterTxt: { color: "#fff", fontSize: 13, letterSpacing: 1, fontWeight: "500" },
  dotsRow: {
    position: "absolute",
    left: 0, right: 0,
    bottom: Platform.select({ ios: 36, default: 20 }),
    flexDirection: "row",
    justifyContent: "center",
    gap: 6,
  },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: "rgba(255,255,255,0.4)" },
  dotOn: { backgroundColor: "#fff", width: 20 },
  arrowLeft: {
    position: "absolute",
    left: 8,
    top: "50%",
    marginTop: -22,
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "center", alignItems: "center",
    zIndex: 5,
  },
  arrowRight: {
    position: "absolute",
    right: 8,
    top: "50%",
    marginTop: -22,
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "center", alignItems: "center",
    zIndex: 5,
  },
  arrowDisabled: { opacity: 0 },
});
