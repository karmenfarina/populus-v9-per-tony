// Web stub — react-native-google-mobile-ads has no web build and its
// TypeScript sources import react-native internals (codegenNative*)
// that Metro refuses to bundle for the web target.
//
// This file is picked by Metro on web (via the `.web.ts` platform
// extension) so any `import '@/src/ads/mobileAds'` in shared code
// resolves to a harmless no-op instead of the native module.
//
// The runtime values here are only ever imported by <AdBanner>,
// which guards them behind `Platform.OS === 'web' → return null`
// before they'd ever be invoked. But we still return sane fallbacks
// so a stray import doesn't crash at eval-time.

const noop = () => Promise.resolve({});

export const mobileAds = () => ({
  initialize: noop,
  setRequestConfiguration: noop,
});

// Placeholder React component that renders nothing on web.
export const BannerAd = () => null;

export const BannerAdSize = {
  BANNER: "BANNER",
  LARGE_BANNER: "LARGE_BANNER",
  MEDIUM_RECTANGLE: "MEDIUM_RECTANGLE",
  FULL_BANNER: "FULL_BANNER",
  LEADERBOARD: "LEADERBOARD",
  ANCHORED_ADAPTIVE_BANNER: "ANCHORED_ADAPTIVE_BANNER",
};

export const TestIds = {
  BANNER: "",
};

export default mobileAds;
