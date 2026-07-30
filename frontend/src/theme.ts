// -----------------------------------------------------------------------------
// Populus design system — dark editorial theme (yellow/red accents).
//
// Everything here is semantic: screens should reference tokens like
// `colors.surface` / `colors.onSurface` instead of hex values, so a future
// palette tweak stays one-edit-only. The palette is intentionally low-
// contrast on backgrounds (borders sit around #1F–#26) so cards feel elevated
// on the pure-black canvas without shouting.
// -----------------------------------------------------------------------------
export const colors = {
  // Backgrounds — the base surface is intentionally NOT pure #000. A tiny
  // amount of luminance (#0A0A0A) blends seamlessly with the logo asset
  // background (icon-dark.png) so the balance icon doesn't sit inside a
  // visible dark square. Elevated surfaces (cards, sheets, tab bar) step
  // up by ~13 units of luminance so they stay clearly distinguishable
  // from the canvas without becoming grey.
  surface: '#0A0A0A',           // main app canvas — very dark, matches logo bg
  surfaceSecondary: '#171717',  // elevated cards / sheet body
  surfaceTertiary: '#212121',   // inputs, subtle chips background
  surfaceInverse: '#0A0A0A',    // headers / tab bar (kept in sync with surface)
  onSurface: '#FFFFFF',
  onSurfaceInverse: '#FFFFFF',

  // Brand & team colours
  brand: '#FFC700',             // Populus yellow — global accent
  brandPrimary: '#FF453A',      // Team A (warm red)
  onBrandPrimary: '#FFFFFF',
  brandSecondary: '#FFC700',    // Team B / yellow accent
  onBrandSecondary: '#000000',
  brandTertiary: '#363636',     // dark neutral (used where a subtle disabled/neutral tint is needed)

  // Feedback
  success: '#30D158',
  error: '#FF453A',

  // Structure — borders are calibrated to sit ~15-20 luminance units above
  // the elevated surfaces so they stay visible on cards without shouting.
  border: '#2A2A2A',            // hairline separator on cards
  borderStrong: '#363636',      // stronger outline where needed
  muted: '#8A8A8E',             // secondary text / disabled
} as const;

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48 };

// Rounded design language. Screens that were previously "brutalist" (radius:0)
// will keep their explicit `borderRadius: 0` overrides; new components should
// reference these tokens to stay consistent.
export const radius = { sm: 8, md: 12, lg: 16, xl: 20, pill: 999 };

export const font = {
  displayFamily: undefined as string | undefined,
  textFamily: undefined as string | undefined,
  sizes: { xs: 11, sm: 12, base: 14, lg: 16, xl: 20, xxl: 24, xxxl: 32, giant: 42 },
};

export const sideColor = (side: 'A' | 'B' | null | undefined) =>
  side === 'A' ? colors.brandPrimary : side === 'B' ? colors.brandSecondary : colors.brandTertiary;

export const onSideColor = (side: 'A' | 'B' | null | undefined) =>
  side === 'A' ? colors.onBrandPrimary : side === 'B' ? colors.onBrandSecondary : colors.onSurface;
