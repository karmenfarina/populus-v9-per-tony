// Native implementation — actually loads the AdMob module.
// Metro picks this file for iOS and Android automatically thanks
// to the `.native.ts` platform extension. The web bundle uses the
// sibling `mobileAds.web.ts` stub instead, which is why we can
// safely import `react-native-google-mobile-ads` here without
// breaking the web build.
import mobileAds, { BannerAd, BannerAdSize, TestIds } from "react-native-google-mobile-ads";

export { mobileAds, BannerAd, BannerAdSize, TestIds };
export default mobileAds;
