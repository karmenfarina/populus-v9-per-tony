import React, { useCallback, useMemo, useRef, useState, useEffect } from "react";
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
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as ImageManipulator from "expo-image-manipulator";
import { Ionicons } from "@expo/vector-icons";
import { colors, spacing, font } from "@/src/theme";

/**
 * Instagram-style square photo cropper with pan + zoom.
 *
 * How it works:
 * - The image is rendered inside a fixed square crop window.
 * - Initial scale is `cover` — the image fully covers the window.
 * - The user pans the image with a finger and adjusts zoom (1x–4x) via +/- buttons.
 * - On confirm, we translate the current viewport back into ORIGINAL image pixel
 *   coordinates and use `expo-image-manipulator.crop` to cut those pixels.
 * - The final crop is resized to at most 1080px and re-encoded as JPEG base64.
 *
 * Constraints:
 * - Image edges are clamped so the crop window is always fully covered — the
 *   user can never expose empty space around the photo.
 * - The final output aspect ratio is always 1:1 (matches circular avatar).
 */
export type PhotoCropperProps = {
  visible: boolean;
  uri: string | null;
  originalBase64?: string | null;
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
  const WINDOW = Math.min(screen.width, screen.height) - 32; // Square crop window size.

  // Fallback dimensions if not provided by caller.
  const [imgW, setImgW] = useState<number>(originalWidth || 0);
  const [imgH, setImgH] = useState<number>(originalHeight || 0);
  const [busy, setBusy] = useState(false);

  // Pan (in container-pixel units) + user zoom (multiplier ≥ 1).
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [userZoom, setUserZoom] = useState(1);

  const gestureStart = useRef({ tx: 0, ty: 0 });

  // Fetch natural size if not passed in.
  useEffect(() => {
    if (!visible || !uri) return;
    if (imgW > 0 && imgH > 0) return;
    Image.getSize(
      uri,
      (w, h) => {
        setImgW(w);
        setImgH(h);
      },
      () => {
        // Fallback square if we cannot read
        setImgW(WINDOW);
        setImgH(WINDOW);
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uri, visible]);

  // Reset transforms whenever a new picture is loaded.
  useEffect(() => {
    if (visible) {
      setTx(0);
      setTy(0);
      setUserZoom(1);
    }
  }, [visible, uri]);

  // The "cover" scale — the smallest scale that keeps the image fully covering
  // the crop window. All zoom multipliers are applied on top of this.
  const baseCover = useMemo(() => {
    if (!imgW || !imgH) return 1;
    return Math.max(WINDOW / imgW, WINDOW / imgH);
  }, [imgW, imgH, WINDOW]);

  const S = baseCover * userZoom; // effective image-to-container scale
  const dispW = imgW * S;
  const dispH = imgH * S;

  // Maximum allowed offset from center so edges never expose empty space.
  const maxTx = Math.max(0, (dispW - WINDOW) / 2);
  const maxTy = Math.max(0, (dispH - WINDOW) / 2);

  const clamp = useCallback(
    (v: number, max: number) => Math.max(-max, Math.min(max, v)),
    [],
  );

  // Keep translation clamped when zoom changes.
  useEffect(() => {
    setTx((v) => clamp(v, maxTx));
    setTy((v) => clamp(v, maxTy));
  }, [maxTx, maxTy, clamp]);

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          gestureStart.current = { tx, ty };
        },
        onPanResponderMove: (_, g) => {
          setTx(clamp(gestureStart.current.tx + g.dx, maxTx));
          setTy(clamp(gestureStart.current.ty + g.dy, maxTy));
        },
        onPanResponderTerminationRequest: () => false,
      }),
    [maxTx, maxTy, tx, ty, clamp],
  );

  const changeZoom = useCallback(
    (delta: number) => {
      setUserZoom((z) => {
        const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, +(z + delta).toFixed(2)));
        return next;
      });
    },
    [],
  );

  const confirm = useCallback(async () => {
    if (!uri || !imgW || !imgH || busy) return;
    setBusy(true);
    try {
      // Translate crop window corners from container to image pixel space.
      // A container point (X, Y) maps to image pixel ((X - imgOriginX) / S, (Y - imgOriginY) / S).
      const cx = WINDOW / 2 + tx; // image center in container coords
      const cy = WINDOW / 2 + ty;
      const imgOriginX = cx - dispW / 2;
      const imgOriginY = cy - dispH / 2;
      const cropContainerX = 0;
      const cropContainerY = 0;
      // Corners in original image pixels:
      let originX = (cropContainerX - imgOriginX) / S;
      let originY = (cropContainerY - imgOriginY) / S;
      let width = WINDOW / S;
      let height = WINDOW / S;
      // Safety clamp (should never trigger due to clamping above, but rounding).
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
      // Downscale if very large.
      if (width > 1080) {
        actions.push({ resize: { width: 1080 } });
      }
      const out = await ImageManipulator.manipulateAsync(uri, actions, {
        compress: 0.85,
        format: ImageManipulator.SaveFormat.JPEG,
        base64: true,
      });
      if (!out.base64) throw new Error("crop-empty");
      await onConfirm(out.base64);
    } catch (e) {
      // Bubble a minimal alert; keep modal open so user can retry.
      console.warn("cropper: manipulate failed", e);
    } finally {
      setBusy(false);
    }
  }, [uri, imgW, imgH, WINDOW, tx, ty, dispW, dispH, S, busy, onConfirm]);

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
          <View
            style={[styles.cropBox, { width: WINDOW, height: WINDOW }]}
            {...responder.panHandlers}
          >
            {ready && uri ? (
              <Image
                source={{ uri }}
                style={{
                  width: dispW,
                  height: dispH,
                  position: "absolute",
                  left: (WINDOW - dispW) / 2 + tx,
                  top: (WINDOW - dispH) / 2 + ty,
                }}
                resizeMode="cover"
              />
            ) : (
              <ActivityIndicator color={colors.brandPrimary} />
            )}
            {/* Circular mask overlay (visualise how the final avatar will look) */}
            <View style={[styles.circleGuide, { width: WINDOW, height: WINDOW, pointerEvents: "none" }]} />
            {/* Grid rule-of-thirds */}
            <View style={[styles.gridLine, styles.gridV, { left: WINDOW / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridV, { left: (2 * WINDOW) / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridH, { top: WINDOW / 3, pointerEvents: "none" }]} />
            <View style={[styles.gridLine, styles.gridH, { top: (2 * WINDOW) / 3, pointerEvents: "none" }]} />
          </View>

          <View style={styles.zoomRow}>
            <Pressable
              onPress={() => changeZoom(-0.25)}
              style={[styles.zoomBtn, userZoom <= MIN_ZOOM && { opacity: 0.4 }]}
              disabled={userZoom <= MIN_ZOOM}
              testID="cropper-zoom-out"
            >
              <Ionicons name="remove" size={22} color={colors.onSurface} />
            </Pressable>
            <View style={styles.zoomBar}>
              <View
                style={[
                  styles.zoomFill,
                  { width: `${((userZoom - MIN_ZOOM) / (MAX_ZOOM - MIN_ZOOM)) * 100}%` },
                ]}
              />
            </View>
            <Pressable
              onPress={() => changeZoom(0.25)}
              style={[styles.zoomBtn, userZoom >= MAX_ZOOM && { opacity: 0.4 }]}
              disabled={userZoom >= MAX_ZOOM}
              testID="cropper-zoom-in"
            >
              <Ionicons name="add" size={22} color={colors.onSurface} />
            </Pressable>
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
    width: "80%",
  },
  zoomBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surfaceSecondary,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  zoomBar: {
    flex: 1,
    height: 6,
    backgroundColor: "rgba(255,255,255,0.15)",
    borderRadius: 3,
    overflow: "hidden",
  },
  zoomFill: { height: "100%", backgroundColor: colors.brandPrimary },
  hint: { color: "rgba(255,255,255,0.7)", fontSize: font.sizes.sm, textAlign: "center" },
});
