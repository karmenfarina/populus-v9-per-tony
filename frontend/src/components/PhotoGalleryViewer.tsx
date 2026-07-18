import { useEffect, useMemo, useRef, useState } from "react";
import {
  Modal, View, Image, FlatList, Pressable, StyleSheet, Text,
  useWindowDimensions, StatusBar, Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  useAnimatedStyle, useSharedValue, withTiming, runOnJS,
} from "react-native-reanimated";
import { spacing } from "@/src/theme";

/**
 * Full-screen photo viewer.
 *
 * - Horizontal swipe (FlatList `pagingEnabled`) to navigate between photos.
 * - Pinch-to-zoom on the currently focused photo (up to 4x).
 * - Pan while zoomed to move around the image.
 * - Double-tap to toggle between fit and 2.5x zoom.
 * - Tap on backdrop (when not zoomed) closes the viewer.
 *
 * Accepts base64 strings OR pre-formatted `data:image/...;base64,...` URIs
 * OR `file://` / `http(s)://` URIs — anything the RN `<Image>` component can
 * consume.
 *
 * Implementation notes:
 * - Uses the modern `Gesture.Pinch()` / `Gesture.Pan()` / `Gesture.Tap()`
 *   API (compatible with react-native-reanimated v4, which dropped
 *   `useAnimatedGestureHandler`).
 * - We build a `Gesture.Race(pan, pinch)` so the two zoom gestures don't
 *   fight the parent FlatList horizontal swipe when at rest.
 */

export type GalleryPhoto = {
  photo_id?: string;
  /** Base64 payload (without `data:` prefix) OR a full URI. */
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

function ZoomablePage({
  uri,
  width,
  height,
  isActive,
  onCloseTap,
}: {
  uri: string;
  width: number;
  height: number;
  isActive: boolean;
  onCloseTap: () => void;
}) {
  const scale = useSharedValue(1);
  const savedScale = useSharedValue(1);
  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const savedTx = useSharedValue(0);
  const savedTy = useSharedValue(0);

  useEffect(() => {
    if (!isActive) {
      // Reset zoom when scrolled off-screen so re-entry always begins at fit.
      scale.value = withTiming(1, { duration: 150 });
      translateX.value = withTiming(0, { duration: 150 });
      translateY.value = withTiming(0, { duration: 150 });
      savedScale.value = 1;
      savedTx.value = 0;
      savedTy.value = 0;
    }
  }, [isActive, scale, translateX, translateY, savedScale, savedTx, savedTy]);

  const pinch = Gesture.Pinch()
    .onStart(() => {
      savedScale.value = scale.value;
    })
    .onUpdate((e) => {
      const next = savedScale.value * e.scale;
      scale.value = Math.max(1, Math.min(next, 4));
    })
    .onEnd(() => {
      if (scale.value < 1.05) {
        scale.value = withTiming(1, { duration: 180 });
        translateX.value = withTiming(0, { duration: 180 });
        translateY.value = withTiming(0, { duration: 180 });
        savedScale.value = 1;
        savedTx.value = 0;
        savedTy.value = 0;
      } else {
        savedScale.value = scale.value;
      }
    });

  const pan = Gesture.Pan()
    .maxPointers(1)
    .onStart(() => {
      savedTx.value = translateX.value;
      savedTy.value = translateY.value;
    })
    .onUpdate((e) => {
      // Panning only makes sense while zoomed in — otherwise the parent
      // FlatList horizontal swipe should own the gesture. We enforce this
      // in the worklet so RN's gesture system can bail early.
      if (scale.value <= 1) return;
      const maxX = (width * (scale.value - 1)) / 2;
      const maxY = (height * (scale.value - 1)) / 2;
      translateX.value = Math.max(-maxX, Math.min(maxX, savedTx.value + e.translationX));
      translateY.value = Math.max(-maxY, Math.min(maxY, savedTy.value + e.translationY));
    });

  const doubleTap = Gesture.Tap()
    .numberOfTaps(2)
    .maxDelay(280)
    .onEnd(() => {
      if (scale.value > 1) {
        scale.value = withTiming(1, { duration: 180 });
        translateX.value = withTiming(0, { duration: 180 });
        translateY.value = withTiming(0, { duration: 180 });
        savedScale.value = 1;
        savedTx.value = 0;
        savedTy.value = 0;
      } else {
        scale.value = withTiming(2.5, { duration: 180 });
        savedScale.value = 2.5;
      }
    });

  const singleTap = Gesture.Tap()
    .numberOfTaps(1)
    .maxDelay(280)
    .onEnd(() => {
      // Only close on tap when at rest — a tap during zoom would be
      // unpredictable and often unintended.
      if (scale.value <= 1) {
        runOnJS(onCloseTap)();
      }
    });

  // Recognize the double-tap BEFORE the single-tap so a fast twin-tap
  // triggers zoom rather than close.
  const tapCombo = Gesture.Exclusive(doubleTap, singleTap);
  const composed = Gesture.Simultaneous(pinch, Gesture.Race(pan, tapCombo));

  const style = useAnimatedStyle(() => ({
    transform: [
      { translateX: translateX.value },
      { translateY: translateY.value },
      { scale: scale.value },
    ],
  }));

  return (
    <GestureDetector gesture={composed}>
      <Animated.View style={[{ width, height, justifyContent: "center", alignItems: "center" }, style]}>
        <Image
          source={{ uri }}
          style={{ width, height }}
          resizeMode="contain"
          testID="gallery-viewer-image"
        />
      </Animated.View>
    </GestureDetector>
  );
}

export function PhotoGalleryViewer({ visible, photos, initialIndex = 0, onClose }: Props) {
  const { width, height } = useWindowDimensions();
  const listRef = useRef<FlatList<GalleryPhoto>>(null);
  const [activeIdx, setActiveIdx] = useState<number>(initialIndex);

  useEffect(() => {
    if (visible) setActiveIdx(initialIndex);
  }, [visible, initialIndex]);

  const data = useMemo(() => photos.filter((p) => resolveUri(p)), [photos]);
  const total = data.length;

  const onViewableItemsChanged = useRef(({ viewableItems }: any) => {
    if (viewableItems && viewableItems.length > 0) {
      const idx = viewableItems[0].index ?? 0;
      setActiveIdx(idx);
    }
  }).current;

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
        <FlatList
          ref={listRef}
          data={data}
          keyExtractor={(item, i) => item.photo_id || `p-${i}`}
          horizontal
          pagingEnabled
          initialScrollIndex={initialIndex < total ? initialIndex : 0}
          getItemLayout={(_, i) => ({ length: width, offset: width * i, index: i })}
          showsHorizontalScrollIndicator={false}
          onViewableItemsChanged={onViewableItemsChanged}
          viewabilityConfig={{ itemVisiblePercentThreshold: 70 }}
          renderItem={({ item, index }) => (
            <ZoomablePage
              uri={resolveUri(item)}
              width={width}
              height={height}
              isActive={index === activeIdx}
              onCloseTap={onClose}
            />
          )}
          removeClippedSubviews={Platform.OS !== "web"}
        />

        {/* Top overlay: close button + counter */}
        <View style={styles.topBar} pointerEvents="box-none">
          <Pressable onPress={onClose} hitSlop={12} style={styles.closeBtn} testID="gallery-viewer-close">
            <Ionicons name="close" size={28} color="#fff" />
          </Pressable>
          {total > 1 && (
            <View style={styles.counterPill}>
              <Text style={styles.counterTxt}>{activeIdx + 1} / {total}</Text>
            </View>
          )}
        </View>

        {/* Bottom dots */}
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
});
