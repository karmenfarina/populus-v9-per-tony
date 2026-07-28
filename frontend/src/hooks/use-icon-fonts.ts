// Icon font loader for Expo apps. Fonts are loaded from a CDN only under
// Expo Go (StoreClient) — that's where @expo/vector-icons' .ttf files come
// back as 0 bytes from Metro's asset resolver on Android. Native dev/prod
// builds and web pass an empty map, so useFonts resolves to [true, null]
// immediately via react-native-vector-icons autolinking / web stubs.
// ICON_VECTOR_VERSION must match @expo/vector-icons in package.json.
// Usage: const [loaded, error] = useIconFonts();
//
// NOTE: We also block splash on the two icon families that appear in the
// bottom tab bar (Ionicons + MaterialCommunityIcons). On web/slow
// connections the browser lazy-loads webfonts the first time a character
// is rendered — Ionicons warms up first because it's used everywhere,
// while MaterialCommunityIcons was only used by the "scale-balance"
// tab icon and would visibly pop in later. Pre-declaring both here
// makes them both ready before the first render.

import Constants, { ExecutionEnvironment } from "expo-constants";
import { useFonts } from "expo-font";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";

const ICON_VECTOR_VERSION = "15.1.1";

// short internal fontName (what the library queries) -> CDN .ttf file name
const ICON_FAMILIES: Record<string, string> = {
  anticon: "AntDesign",
  entypo: "Entypo",
  evilicons: "EvilIcons",
  feather: "Feather",
  FontAwesome: "FontAwesome",
  Fontisto: "Fontisto",
  foundation: "Foundation",
  ionicons: "Ionicons",
  "material-community": "MaterialCommunityIcons",
  material: "MaterialIcons",
  octicons: "Octicons",
  "simple-line-icons": "SimpleLineIcons",
  zocial: "Zocial",
  // FontAwesome5 style variants (key = `FontAwesome5Free-<style>`)
  "FontAwesome5Free-Regular": "FontAwesome5_Regular",
  "FontAwesome5Free-Solid": "FontAwesome5_Solid",
  "FontAwesome5Free-Brand": "FontAwesome5_Brands",
  // FontAwesome6 style variants (key = `FontAwesome6Free-<style>`)
  "FontAwesome6Free-Regular": "FontAwesome6_Regular",
  "FontAwesome6Free-Solid": "FontAwesome6_Solid",
  "FontAwesome6Free-Brand": "FontAwesome6_Brands",
};

const cdnUrl = (file: string): string =>
  `https://cdn.jsdelivr.net/npm/@expo/vector-icons@${ICON_VECTOR_VERSION}/build/vendor/react-native-vector-icons/Fonts/${file}.ttf`;

const iconFontMap = (): Record<string, string> =>
  Object.fromEntries(
    Object.entries(ICON_FAMILIES).map(([key, file]) => [key, cdnUrl(file)]),
  );

export const useIconFonts = (): readonly [boolean, Error | null] => {
  // Always block on the two tab-bar icon families so the bottom bar
  // paints atomically, no matter the platform or connection speed.
  const tabBarFonts = {
    ...(Ionicons.font as Record<string, any>),
    ...(MaterialCommunityIcons.font as Record<string, any>),
  };
  const cdnFonts =
    Constants.executionEnvironment === ExecutionEnvironment.StoreClient
      ? iconFontMap()
      : {};
  return useFonts({ ...tabBarFonts, ...cdnFonts });
};
