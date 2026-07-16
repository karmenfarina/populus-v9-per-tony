import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View,
  Text,
  Image,
  Modal,
  Pressable,
  StyleSheet,
  Dimensions,
  PanResponder,
  ActivityIndicator,
  Animated,
  LayoutChangeEvent,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as ImageManipulator from "expo-image-manipulator";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";

/**
 * Instagram-style square photo cropper with pan + zoom.
 *
 * Interaction model:
 * - Two independent gesture surfaces (pan on the crop box, drag on the zoom
 *   slider) so both can be used without interfering with the other.
 * - The image transformations (translate + scale) are driven by
 *   `Animated.Value`s so the finger tracks the image at 60 fps without
 *   triggering React re-renders on every move.
 * - When the gesture ends we snapshot the current animated values into refs
 *   and apply clamping (image edges cannot expose empty space inside the
 *   crop window). This clamp also runs whenever zoom changes.
 * - On confirm, we translate the current viewport back into ORIGINAL image
 *   pixel coordinates and use expo-image-manipulator to physically crop.
 */
export type PhotoCropperProps = {
  visible: boolean;
  uri: string | null;
  originalWidth?: number;
  originalHeight?: number;
  onCancel: () => void;
  onConfirm: (base64: string) => void | Promise<void>;
};

const MIN_ZOOM = 1;
const MAX_ZOOM = 4;

export default function PhotoCropper({
  visible,
  uri,
  originalWidth,
  originalHeight,
  onCancel,
  onConfirm,
}: PhotoCropperProps) {
  const screen = Dimensions.get("window");
  const WINDOW = Math.min(screen.width, screen.height) - 32;

  const [imgW, setImgW] = useState<number>(0);
  const [imgH, setImgH] = useState<number>(0);
  const [busy, setBusy] = useState(false);

  // Live animated values (drive the image transform + slider every frame).
  const txAnim = useRef(new Animated.Value(0)).current;
  const tyAnim = useRef(new Animated.Value(0)).current;
  const sliderProgressAnim = useRef(new Animated.Value(0)).current;

  // Display scale is a React state (updates only on zoom, at most a few
  // times per frame). Using state guarantees the JSX transform passes a new
  // scalar to React Native's transform prop, which reliably diffs and
  // updates the native view — Animated.Value.setValue is unreliable here
  // because the RN-Web transform layer does not always subscribe to changes.
  const [scale, setScale] = useState<number>(1);

  // Refs mirroring the animated values (source of truth for gesture math &
  // crop calculation).
  const tx = useRef(0);
  const ty = useRef(0);
  const zoom = useRef(MIN_ZOOM);
  const displayScale = useRef(1);
  const sliderProgress = useRef(0);
  const gestureStart = useRef({ tx: 0, ty: 0 });
  const [sliderWidth, setSliderWidth] = useState(0);
  const sliderStart = useRef(0);

  // Sync refs from animated values.
  useEffect(() => {
    const sx = txAnim.addListener(({ value }) => { tx.current = value; });
    const sy = tyAnim.addListener(({ value }) => { ty.current = value; });
    const sp = sliderProgressAnim.addListener(({ value }) => { sliderProgress.current = value; });
    return () => {
      txAnim.removeListener(sx);
      tyAnim.removeListener(sy);
      sliderProgressAnim.removeListener(sp);
    };
  }, [txAnim, tyAnim, sliderProgressAnim]);

  // Read intrinsic image size when needed. Reset dims when the URI changes
  // so a re-crop with a different photo doesn't inherit the previous one's
  // dimensions (which would produce a wrong crop rectangle).
  useEffect(() => {
    if (!visible || !uri) return;
    if (originalWidth && originalHeight) {
      setImgW(originalWidth);
      setImgH(originalHeight);
      return;
    }
    // Force re-read for the new URI.
    setImgW(0);
    setImgH(0);
    let cancelled = false;
    Image.getSize(
      uri,
      (w, h) => { if (!cancelled) { setImgW(w); setImgH(h); } },
      () => { if (!cancelled) { setImgW(WINDOW); setImgH(WINDOW); } },
    );
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uri, visible, originalWidth, originalHeight]);

  // Reset when a new picture loads or the modal opens.
  useEffect(() => {
    if (!visible) return;
    txAnim.setValue(0);
    tyAnim.setValue(0);
    sliderProgressAnim.setValue(0);
    tx.current = 0;
    ty.current = 0;
    zoom.current = MIN_ZOOM;
    sliderProgress.current = 0;
  }, [visible, uri, txAnim, tyAnim, sliderProgressAnim]);

  // Cover-scale so the image always fills the crop window before user zoom.
  const baseCover = useMemo(() => {
    if (!imgW || !imgH) return 1;
    return Math.max(WINDOW / imgW, WINDOW / imgH);
  }, [imgW, imgH, WINDOW]);

  // Keep the display scale in sync whenever the base cover changes (image
  // dimensions loaded) or when the user zooms via slider.
  useEffect(() => {
    const v = baseCover * zoom.current;
    setScale(v);
    displayScale.current = v;
  }, [baseCover]);

  // Current display size / clamp bounds are derived helpers.
  const clamp = useCallback((v: number, max: number) => Math.max(-max, Math.min(max, v)), []);
  const currentDisp = useCallback(() => {
    const S = displayScale.current;
    return { S, dispW: imgW * S, dispH: imgH * S };
  }, [imgW, imgH]);
  const currentBounds = useCallback(() => {
    const { dispW, dispH } = currentDisp();
    return {
      maxTx: Math.max(0, (dispW - WINDOW) / 2),
      maxTy: Math.max(0, (dispH - WINDOW) / 2),
    };
  }, [currentDisp, WINDOW]);

  // After the zoom finishes we may need to re-clamp translation so the image
  // still covers the crop window (a zoom-out could otherwise leave empty
  // margins). This runs also during zoom drag via the slider listener.
  const reclampTranslation = useCallback(() => {
    const { maxTx, maxTy } = currentBounds();
    const nx = clamp(tx.current, maxTx);
    const ny = clamp(ty.current, maxTy);
    if (nx !== tx.current) txAnim.setValue(nx);
    if (ny !== ty.current) tyAnim.setValue(ny);
  }, [currentBounds, clamp, txAnim, tyAnim]);

  // Pan gesture on the crop box — updates translation in real time.
  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onStartShouldSetPanResponderCapture: () => true,
        onMoveShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponderCapture: () => true,
        onPanResponderTerminationRequest: () => false,
        onShouldBlockNativeResponder: () => true,
        onPanResponderGrant: () => {
          gestureStart.current = { tx: tx.current, ty: ty.current };
        },
        onPanResponderMove: (_, g) => {
          const { maxTx, maxTy } = currentBounds();
          const nx = clamp(gestureStart.current.tx + g.dx, maxTx);
          const ny = clamp(gestureStart.current.ty + g.dy, maxTy);
          txAnim.setValue(nx);
          tyAnim.setValue(ny);
        },
        onPanResponderRelease: () => reclampTranslation(),
      }),
    [currentBounds, clamp, txAnim, tyAnim, reclampTranslation],
  );

  // Slider drag gesture — continuous zoom.
  const applyZoomFromProgress = useCallback(
    (progress: number) => {
      const p = Math.max(0, Math.min(1, progress));
      const userZoom = MIN_ZOOM + p * (MAX_ZOOM - MIN_ZOOM);
      zoom.current = userZoom;
      const nextScale = baseCover * userZoom;
      setScale(nextScale);
      displayScale.current = nextScale;
      sliderProgress.current = p;
      sliderProgressAnim.setValue(p);
      // Prevent empty margins after a zoom out.
      reclampTranslation();
    },
    [sliderProgressAnim, reclampTranslation, baseCover],
  );

  const sliderResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onStartShouldSetPanResponderCapture: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderTerminationRequest: () => false,
        onShouldBlockNativeResponder: () => true,
        onPanResponderGrant: (evt) => {
          if (sliderWidth <= 0) {
            sliderStart.current = sliderProgress.current;
            return;
          }
          // Tap-to-jump ONLY when the finger clearly lands away from the
          // current thumb. Otherwise (dragging the thumb from where it
          // already is), we don't move on grant — this preserves the very
          // first pixel of the drag and eliminates the "initial snap" the
          // user complained about.
          const x = evt.nativeEvent.locationX;
          const currentThumbX = sliderProgress.current * sliderWidth;
          const THUMB_HITBOX = 22; // half of the visual thumb + a little slack
          if (Math.abs(x - currentThumbX) > THUMB_HITBOX) {
            applyZoomFromProgress(x / sliderWidth);
          }
          // Capture the base AFTER the (possibly skipped) jump so subsequent
          // `dx` values are measured from the finger's current position.
          sliderStart.current = sliderProgress.current;
        },
        onPanResponderMove: (_, g) => {
          if (sliderWidth <= 0) return;
          const nextProgress = sliderStart.current + g.dx / sliderWidth;
          applyZoomFromProgress(nextProgress);
        },
      }),
    [sliderWidth, applyZoomFromProgress],
  );

  const onSliderLayout = useCallback((e: LayoutChangeEvent) => {
    setSliderWidth(e.nativeEvent.layout.width);
  }, []);

  const confirm = useCallback(async () => {
    if (!uri || !imgW || !imgH || busy) return;
    setBusy(true);
    try {
      // Use the SAME scale that the transform is currently rendering with.
      // Reading `displayScale.current` (populated by the scaleAnim listener)
      // guarantees the visual and the crop math stay in sync.
      const S = displayScale.current;
      const dispW = imgW * S;
      const dispH = imgH * S;
      const cx = WINDOW / 2 + tx.current;
      const cy = WINDOW / 2 + ty.current;
      const imgOriginX = cx - dispW / 2;
      const imgOriginY = cy - dispH / 2;
      let originX = (0 - imgOriginX) / S;
      let originY = (0 - imgOriginY) / S;
      let width = WINDOW / S;
      let height = WINDOW / S;
      originX = Math.max(0, Math.min(imgW - 1, originX));
      originY = Math.max(0, Math.min(imgH - 1, originY));
      width = Math.max(1, Math.min(imgW - originX, width));
      height = Math.max(1, Math.min(imgH - originY, height));
      const actions: ImageManipulator.Action[] = [
        {
          crop: {
            originX: Math.round(originX),
            originY: Math.round(originY),
            width: Math.round(width),
            height: Math.round(height),
          },
        },
      ];
      if (width > 720) actions.push({ resize: { width: 720 } });
      const out = await ImageManipulator.manipulateAsync(uri, actions, {
        compress: 0.78,
        format: ImageManipulator.SaveFormat.JPEG,
        base64: true,
      });
      if (!out.base64) throw new Error("crop-empty");
      await onConfirm(out.base64);
    } catch (e) {
      console.warn("cropper: manipulate failed", e);
    } finally {
      setBusy(false);
    }
  }, [uri, imgW, imgH, WINDOW, busy, onConfirm]);

  const ready = !!uri && imgW > 0 && imgH > 0;

  return (
    <Modal visible={visible} animationType="fade" onRequestClose={onCancel} transparent>
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable onPress={onCancel} style={styles.headerBtn} testID="cropper-cancel">
            <Ionicons name="close" size={24} color={colors.onSurfaceInverse} />
          </Pressable>
          <Text style={styles.title}>RITAGLIA FOTO</Text>
          <Pressable
            onPress={confirm}
            style={[styles.headerBtn, !ready && { opacity: 0.5 }]}
            disabled={!ready || busy}
            testID="cropper-confirm"
          >
            {busy ? (
              <ActivityIndicator color={colors.brandSecondary} size="small" />
            ) : (
              <Ionicons name="checkmark" size={26} color={colors.brandSecondary} />
            )}
          </Pressable>
        </View>

        <View style={styles.stage}>
          <View style={[styles.cropBox, { width: WINDOW, height: WINDOW }]} {...panResponder.panHandlers}>
            {ready && uri ? (
              <Animated.Image
                source={{ uri }}
                style={{
                  position: "absolute",
                  width: imgW,
                  height: imgH,
                  left: (WINDOW - imgW) / 2,
                  top: (WINDOW - imgH) / 2,
                  transform: [
                    { translateX: txAnim },
                    { translateY: tyAnim },
                    { scale },
                  ],
                }}
                resizeMode="cover"
              />
            ) : (
              <ActivityIndicator color={colors.brandPrimary} />
            )}
            {/* Circular avatar preview (visual guide) */}
            <View style={[styles.circleGuide, { width: WINDOW, height: WINDOW, pointerEvents: "none" }]} />
            {/* Rule-of-thirds grid */}
            <View style={[styles.gridLine, styles.gridV, { left: WINDOW / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridV, { left: (2 * WINDOW) / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridH, { top: WINDOW / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridH, { top: (2 * WINDOW) / 3, pointerEvents: "none" }]} />
          </View>

          {/* Continuous zoom slider */}
          <View style={styles.zoomRow}>
            <Ionicons name="scan-outline" size={18} color="rgba(255,255,255,0.6)" />
            <View
              style={styles.sliderTrackWrap}
              onLayout={onSliderLayout}
              {...sliderResponder.panHandlers}
              testID="cropper-slider"
            >
              <View style={styles.sliderTrack}>
                <Animated.View
                  style={[
                    styles.sliderFill,
                    {
                      width: sliderProgressAnim.interpolate({
                        inputRange: [0, 1],
                        outputRange: [0, Math.max(sliderWidth, 1)],
                        extrapolate: "clamp",
                      }),
                    },
                  ]}
                />
              </View>
              <Animated.View
                style={[
                  styles.sliderThumb,
                  {
                    transform: [
                      {
                        translateX: sliderProgressAnim.interpolate({
                          inputRange: [0, 1],
                          outputRange: [0, Math.max(sliderWidth - 24, 1)],
                          extrapolate: "clamp",
                        }),
                      },
                    ],
                  },
                ]}
                testID="cropper-slider-thumb"
              />
            </View>
            <Ionicons name="expand-outline" size={20} color="rgba(255,255,255,0.85)" />
          </View>

          <Text style={styles.hint}>Trascina la foto per scegliere la porzione visibile</Text>
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "rgba(0,0,0,0.95)" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderColor: "rgba(255,255,255,0.15)",
  },
  headerBtn: { padding: spacing.sm, minWidth: 40, alignItems: "center" },
  title: { color: colors.brandSecondary, fontSize: font.sizes.sm, letterSpacing: 3, fontWeight: "500" },
  stage: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md, gap: spacing.lg },
  cropBox: {
    overflow: "hidden",
    backgroundColor: "#000",
    borderRadius: 8,
    position: "relative",
  },
  circleGuide: {
    position: "absolute",
    top: 0,
    left: 0,
    borderRadius: 999,
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.85)",
  },
  gridLine: { position: "absolute", backgroundColor: "rgba(255,255,255,0.25)" },
  gridV: { top: 0, bottom: 0, width: 1 },
  gridH: { left: 0, right: 0, height: 1 },
  zoomRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    width: "85%",
  },
  sliderTrackWrap: {
    flex: 1,
    height: 44, // Wide touch target for the finger.
    justifyContent: "center",
    position: "relative",
  },
  sliderTrack: {
    height: 6,
    backgroundColor: "rgba(255,255,255,0.15)",
    borderRadius: 3,
    overflow: "hidden",
  },
  sliderFill: { height: "100%", backgroundColor: colors.brandPrimary },
  sliderThumb: {
    position: "absolute",
    top: 10,
    left: 0,
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.brandSecondary,
    borderWidth: 2,
    borderColor: "#fff",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.4,
    shadowRadius: 3,
    elevation: 3,
  },
  hint: { color: "rgba(255,255,255,0.7)", fontSize: font.sizes.sm, textAlign: "center" },
});
