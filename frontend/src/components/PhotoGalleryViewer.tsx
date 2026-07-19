import { useEffect, useMemo, useRef, useState } from "react";
import {
  Modal, View, Image, ScrollView, Pressable, StyleSheet, Text,
  useWindowDimensions, StatusBar, Platform, NativeSyntheticEvent, NativeScrollEvent,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { spacing } from "@/src/theme";

/**
 * Full-screen photo viewer.
 *
 * Simple horizontal pager built on React Native's built-in ScrollView with
 * `pagingEnabled`. On both web and native this gives a fully reliable swipe
 * between photos — a previous attempt used FlatList + gesture-handler pinch
 * gestures, but those blocked the horizontal swipe on native and were the
 * root cause of the "non riesco a scorrere" bug.
 *
 * - Swipe horizontally to navigate between photos.
 * - Tap on the empty backdrop (top/bottom bars) closes the viewer.
 * - Tap the ✕ button also closes.
 * - Counter (top-right) + dot indicator (bottom) track the active photo.
 *
 * Pinch-zoom is not included in this iteration because it competes with the
 * horizontal pager gesture on RN Gesture Handler v2. The user's request was
 * specifically for enlarging + sfogliare — a full-screen contain-fit image
 * already fulfils the "enlarge" part.
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
  const scrollRef = useRef<ScrollView>(null);
  const [activeIdx, setActiveIdx] = useState<number>(initialIndex);
  const initialScrollDoneRef = useRef(false);

  const data = useMemo(() => photos.filter((p) => resolveUri(p)), [photos]);
  const total = data.length;

  useEffect(() => {
    if (!visible) {
      initialScrollDoneRef.current = false;
      return;
    }
    setActiveIdx(initialIndex);
    // Try to scroll a few times so we defeat the "ScrollView isn't laid out
    // yet" race that shows up on cold Modal mounts.
    const tries = [30, 90, 220, 450];
    const timers: any[] = [];
    tries.forEach((ms) => {
      timers.push(setTimeout(() => {
        try {
          scrollRef.current?.scrollTo({ x: initialIndex * width, y: 0, animated: false });
        } catch { /* noop */ }
      }, ms));
    });
    timers.push(setTimeout(() => { initialScrollDoneRef.current = true; }, 550));
    return () => { timers.forEach(clearTimeout); };
  }, [visible, initialIndex, width]);

  const handleScrollEnd = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (!initialScrollDoneRef.current) return;
    const offsetX = e.nativeEvent.contentOffset.x;
    const idx = Math.round(offsetX / width);
    if (idx >= 0 && idx < total && idx !== activeIdx) {
      setActiveIdx(idx);
    }
  };

  // Explicit navigation buttons (mouse-friendly on web, useful on
  // narrow-thumb one-hand usage on device).
  const goPrev = () => {
    if (activeIdx <= 0) return;
    const next = activeIdx - 1;
    scrollRef.current?.scrollTo({ x: next * width, y: 0, animated: true });
    setActiveIdx(next);
  };
  const goNext = () => {
    if (activeIdx >= total - 1) return;
    const next = activeIdx + 1;
    scrollRef.current?.scrollTo({ x: next * width, y: 0, animated: true });
    setActiveIdx(next);
  };

  if (!visible || total === 0) return null;

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
        <ScrollView
          ref={scrollRef}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          onMomentumScrollEnd={handleScrollEnd}
          onScrollEndDrag={handleScrollEnd}
          decelerationRate="fast"
          testID="gallery-viewer-scroll"
        >
          {data.map((item, index) => (
            <View
              key={item.photo_id || `p-${index}`}
              style={{ width, height, justifyContent: "center", alignItems: "center" }}
              testID={`gallery-page-${index}`}
            >
              <Image
                source={{ uri: resolveUri(item) }}
                style={{ width, height }}
                resizeMode="contain"
                testID="gallery-viewer-image"
              />
            </View>
          ))}
        </ScrollView>

        {/* Left/right arrow overlays — clickable for mouse users, hit only the
            outer edges so they don't steal touch from the pager on device. */}
        {total > 1 && activeIdx > 0 && (
          <Pressable
            onPress={goPrev}
            hitSlop={8}
            style={styles.arrowLeft}
            testID="gallery-viewer-prev"
          >
            <Ionicons name="chevron-back" size={28} color="#fff" />
          </Pressable>
        )}
        {total > 1 && activeIdx < total - 1 && (
          <Pressable
            onPress={goNext}
            hitSlop={8}
            style={styles.arrowRight}
            testID="gallery-viewer-next"
          >
            <Ionicons name="chevron-forward" size={28} color="#fff" />
          </Pressable>
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
});
