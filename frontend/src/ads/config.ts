// ─────────────────────────────────────────────────────────────────────
// Centralised AdMob configuration.
//
// TO SWITCH FROM TEST ADS TO YOUR REAL ADS:
//   1. Open your AdMob console (https://apps.admob.com)
//   2. Copy the **App IDs** for Android and iOS → paste them into
//      `/app/frontend/app.json` under the "react-native-google-mobile-ads"
//      plugin block (`androidAppId` / `iosAppId`).
//   3. Copy the **Banner Ad Unit IDs** for Android and iOS → paste them
//      below, replacing the current test IDs.
//   4. Rebuild via Emergent Publish → Deploy → Generate iOS/Android
//      builds. AdMob does not work in Expo Go / web preview.
//
// Until you replace them, the app will show Google's official TEST
// banner (safe, no risk of policy strike, generates zero revenue).
// ─────────────────────────────────────────────────────────────────────

// Google's PUBLIC test banner unit IDs. Always safe to use in dev
// builds — Google explicitly requires test IDs during integration to
// avoid invalid-traffic strikes.
//   https://developers.google.com/admob/android/test-ads
//   https://developers.google.com/admob/ios/test-ads
export const TEST_BANNER_UNIT_ID_ANDROID = "ca-app-pub-3940256099942544/6300978111";
export const TEST_BANNER_UNIT_ID_IOS = "ca-app-pub-3940256099942544/2934735716";

// ↓ REPLACE THESE WITH YOUR REAL AD UNIT IDS ONCE YOU HAVE AN ADMOB
// ACCOUNT APPROVED. Keep the format ca-app-pub-XXXXXXXXX/XXXXXXXXX.
export const PROD_BANNER_UNIT_ID_ANDROID = ""; // e.g. "ca-app-pub-XXXXXXXXXXXXXXXX/YYYYYYYYYY"
export const PROD_BANNER_UNIT_ID_IOS = ""; // e.g. "ca-app-pub-XXXXXXXXXXXXXXXX/YYYYYYYYYY"

/**
 * Resolves the correct banner unit ID for the current platform.
 * - In development / when no prod ID is set → returns test unit ID
 * - In production (release build) with prod IDs set → returns prod ID
 * Returns `null` on web (no banners on web).
 */
export function resolveBannerUnitId(platform: string, isDev: boolean): string | null {
  if (platform === "web") return null;
  const prod = platform === "ios" ? PROD_BANNER_UNIT_ID_IOS : PROD_BANNER_UNIT_ID_ANDROID;
  const test = platform === "ios" ? TEST_BANNER_UNIT_ID_IOS : TEST_BANNER_UNIT_ID_ANDROID;
  // Use production ID only when we're NOT in dev AND a real ID was set.
  if (!isDev && prod && prod.length > 0) return prod;
  return test;
}
