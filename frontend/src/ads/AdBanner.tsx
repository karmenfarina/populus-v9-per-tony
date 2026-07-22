// ─────────────────────────────────────────────────────────────────────
// AdBanner — thin, platform-aware wrapper around react-native-google-
// mobile-ads' <BannerAd>.
//
// Behaviour:
//   * Web         → renders NOTHING (returns null). AdMob has no web SDK.
//   * Expo Go     → renders a small "AD PLACEHOLDER" pill so the layout
//                   doesn't collapse during development; real ads need
//                   a native/EAS build (Emergent Publish → Generate
//                   iOS/Android builds).
//   * Native      → renders a live BannerAd using the unit ID resolved
//                   from `src/ads/config.ts`. Ads are TEST until you
//                   replace the prod IDs there and rebuild.
//
// The module import happens INSIDE a `Platform.OS !== 'web'` branch
// so Metro's web bundler doesn't try to resolve the native AdMob
// module (which has no web build and would break `expo start --web`).
// ─────────────────────────────────────────────────────────────────────

import React from "react";
import { View, Text, Platform, StyleSheet } from "react-native";
import Constants from "expo-constants";
import { resolveBannerUnitId } from "@/src/ads/config";
// Platform-specific import: Metro resolves to `mobileAds.native.ts`
// on iOS/Android and to `mobileAds.web.ts` (a harmless stub) on web.
// This is what allows us to `import ... from` unconditionally at
// the top of the file without breaking the web bundler.
// eslint-disable-next-line import/no-unresolved -- resolved via .native.ts / .web.ts platform extensions
import { BannerAd, BannerAdSize } from "@/src/ads/mobileAds";

// Detect Expo Go vs a real native build. In Expo Go the AdMob native
// module isn't linked, so trying to render <BannerAd> throws. We fall
// back to a lightweight placeholder there.
const isExpoGo = Constants.appOwnership === "expo";

type Props = {
  /**
   * Marker for A/B tests / analytics. Not sent anywhere; just a hint
   * for future placement tracking.
   */
  placement?: string;
  style?: any;
};

export default function AdBanner({ placement, style }: Props) {
  // ── Web ─────────────────────────────────────────────
  if (Platform.OS === "web") {
    return null;
  }

  // ── Expo Go (JS-only, no native module) ────────────
  if (isExpoGo) {
    return (
      <View style={[styles.placeholder, style]} testID={`ad-placeholder-${placement || "default"}`}>
        <Text style={styles.placeholderLabel}>SPAZIO PUBBLICITARIO</Text>
        <Text style={styles.placeholderSub}>Attivo dopo il build nativo</Text>
      </View>
    );
  }

  // ── Native (iOS / Android with AdMob SDK linked) ───
  const unitId = resolveBannerUnitId(Platform.OS, __DEV__);
  if (!unitId) return null;

  return (
    <View style={[styles.wrapper, style]} testID={`ad-banner-${placement || "default"}`}>
      <BannerAd
        unitId={unitId}
        size={BannerAdSize.ANCHORED_ADAPTIVE_BANNER}
        requestOptions={{
          // Conservative default: no personalised targeting until the
          // user has explicitly opted-in via ATT (iOS) or the EU
          // consent SDK (Android). Keeps us GDPR/EEA-safe out of the
          // box; toggle to `false` once you wire a proper consent
          // flow to lift eCPM.
          requestNonPersonalizedAdsOnly: true,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 8,
  },
  placeholder: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 50,
    borderRadius: 6,
    backgroundColor: "rgba(0,0,0,0.04)",
    borderWidth: 1,
    borderColor: "rgba(0,0,0,0.08)",
    borderStyle: "dashed",
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginVertical: 8,
  },
  placeholderLabel: {
    fontSize: 10,
    fontWeight: "700",
    color: "rgba(0,0,0,0.55)",
    letterSpacing: 1.2,
  },
  placeholderSub: {
    fontSize: 9,
    color: "rgba(0,0,0,0.35)",
    marginTop: 2,
  },
});
