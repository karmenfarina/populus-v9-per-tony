// Base fallback for `import '@/src/ads/mobileAds'`.
//
// Metro's platform-extension resolver always prefers the sibling
// `mobileAds.native.ts` on iOS/Android and `mobileAds.web.ts` on web.
// This file only exists so tooling that doesn't understand platform
// extensions (ESLint's `import/no-unresolved`, TS in non-Metro
// contexts) can still resolve the module.
//
// At runtime this code path is unreachable on our supported targets,
// but we still expose the same shape as the web stub so a stray
// import can't crash at eval time.

const noop = () => Promise.resolve({});

export const mobileAds = () => ({
  initialize: noop,
  setRequestConfiguration: noop,
});

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
