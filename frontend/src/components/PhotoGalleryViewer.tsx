import { useEffect, useMemo, useRef, useState } from "react";
import {
  Modal, View, Image, ScrollView, Pressable, StyleSheet, Text,
  useWindowDimensions, StatusBar, Platform, NativeSyntheticEvent, NativeScrollEvent,
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
 * Uses a horizontal, paging `ScrollView` (more reliable than `FlatList` on
 * web/native for this exact scenario) with one zoomable page per photo.
 *
 * - Swipe horizontally to navigate.
 * - Pinch to zoom the CURRENT page (up to 4x).
 * - Pan while zoomed to move around.
 * - Double-tap toggles zoom.
 * - Single tap on backdrop closes the viewer.
 *
 * Critical implementation detail: pan is DISABLED while at fit-scale (1×) so
 * it doesn't fight the parent ScrollView for horizontal touches. This is what
 * makes horizontal swipe reliably work between photos. See `.enabled(zoomed)`
 * on the pan gesture below.
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

function ZoomablePage({
  uri,
  width,
  height,
  isActive,
  onCloseTap,
  onZoomChange,
}: {
  uri: string;
  width: number;
  height: number;
  isActive: boolean;
  onCloseTap: () => void;
  onZoomChange: (zoomed: boolean) => void;
}) {
  const scale = useSharedValue(1);
  const savedScale = useSharedValue(1);
  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const savedTx = useSharedValue(0);
  const savedTy = useSharedValue(0);
  const [zoomed, setZoomed] = useState(false);

  const applyZoomState = (z: boolean) => {
    setZoomed(z);
    onZoomChange(z);
  };

  useEffect(() => {
    if (!isActive) {
      scale.value = withTiming(1, { duration: 150 });
      translateX.value = withTiming(0, { duration: 150 });
      translateY.value = withTiming(0, { duration: 150 });
      savedScale.value = 1;
      savedTx.value = 0;
      savedTy.value = 0;
      if (zoomed) applyZoomState(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

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
        runOnJS(applyZoomState)(false);
      } else {
        savedScale.value = scale.value;
        runOnJS(applyZoomState)(true);
      }
    });

  // Pan is ONLY enabled while zoomed. When at fit-scale, we hand horizontal
  // touches back to the parent ScrollView so page-swipe works reliably.
  const pan = Gesture.Pan()
    .maxPointers(1)
    .enabled(zoomed)
    .onStart(() => {
      savedTx.value = translateX.value;
      savedTy.value = translateY.value;
    })
    .onUpdate((e) => {
      const maxX = (width * (scale.value - 1)) / 2;
      const maxY = (height * (scale.value - 1)) / 2;
      translateX.value = Math.max(-maxX, Math.min(maxX, savedTx.value + e.translationX));
      translateY.value = Math.max(-maxY, Math.min(maxY, savedTy.value + e.translationY));
    });

  const doubleTap = Gesture.Tap()
    .numberOfTaps(2)
    .maxDelay(260)
    .onEnd(() => {
      if (scale.value > 1) {
        scale.value = withTiming(1, { duration: 180 });
        translateX.value = withTiming(0, { duration: 180 });
        translateY.value = withTiming(0, { duration: 180 });
        savedScale.value = 1;
        savedTx.value = 0;
        savedTy.value = 0;
        runOnJS(applyZoomState)(false);
      } else {
        scale.value = withTiming(2.5, { duration: 180 });
        savedScale.value = 2.5;
        runOnJS(applyZoomState)(true);
      }
    });

  const singleTap = Gesture.Tap()
    .numberOfTaps(1)
    .maxDelay(260)
    .onEnd(() => {
      if (scale.value <= 1) {
        runOnJS(onCloseTap)();
      }
    });

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
    <View style={{ width, height, justifyContent: "center", alignItems: "center" }}>
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
    </View>
  );
}

export function PhotoGalleryViewer({ visible, photos, initialIndex = 0, onClose }: Props) {
  const { width, height } = useWindowDimensions();
  const scrollRef = useRef<ScrollView>(null);
  const [activeIdx, setActiveIdx] = useState<number>(initialIndex);
  const [anyPageZoomed, setAnyPageZoomed] = useState(false);
  const initialScrollDoneRef = useRef(false);

  const data = useMemo(() => photos.filter((p) => resolveUri(p)), [photos]);
  const total = data.length;

  // Whenever the viewer becomes visible, jump the ScrollView to the caller's
  // initial index. We queue the scroll for the next frame so the ScrollView
  // has had a chance to render pages. Using `scrollTo` (imperative) is more
  // reliable across web + native than `contentOffset` prop or `initialScrollIndex`.
  useEffect(() => {
    if (!visible) {
      initialScrollDoneRef.current = false;
      return;
    }
    setActiveIdx(initialIndex);
    // Attempt the scroll a couple of times to defeat the race where the
    // ScrollView measures its layout AFTER we tried to scroll.
    const tries = [30, 90, 220, 450];
    const timers: any[] = [];
    tries.forEach((ms) => {
      timers.push(setTimeout(() => {
        try {
          scrollRef.current?.scrollTo({ x: initialIndex * width, y: 0, animated: false });
        } catch { /* noop */ }
      }, ms));
    });
    // Mark initial scroll as done shortly after last attempt so momentum
    // handlers start tracking user swipes only from that point on.
    timers.push(setTimeout(() => { initialScrollDoneRef.current = true; }, 550));
    return () => { timers.forEach(clearTimeout); };
  }, [visible, initialIndex, width]);

  const handleScrollEnd = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    // Only update the counter AFTER the initial scroll settle to avoid the
    // "opened on photo 2 but counter says 1/N" bug that plagued the FlatList
    // implementation. Once the user starts actually swiping we trust the
    // native scroll offset as the source of truth.
    if (!initialScrollDoneRef.current) return;
    const offsetX = e.nativeEvent.contentOffset.x;
    const idx = Math.round(offsetX / width);
    if (idx >= 0 && idx < total && idx !== activeIdx) {
      setActiveIdx(idx);
    }
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
          scrollEnabled={!anyPageZoomed}
          showsHorizontalScrollIndicator={false}
          onMomentumScrollEnd={handleScrollEnd}
          onScrollEndDrag={handleScrollEnd}
          decelerationRate="fast"
          testID="gallery-viewer-scroll"
        >
          {data.map((item, index) => (
            <ZoomablePage
              key={item.photo_id || `p-${index}`}
              uri={resolveUri(item)}
              width={width}
              height={height}
              isActive={index === activeIdx}
              onCloseTap={onClose}
              onZoomChange={setAnyPageZoomed}
            />
          ))}
        </ScrollView>

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
});
