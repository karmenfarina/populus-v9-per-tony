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
  // Backgrounds
  surface: '#000000',           // main app canvas — pure black
  surfaceSecondary: '#0F0F0F',  // elevated cards / sheet body
  surfaceTertiary: '#1A1A1A',   // inputs, subtle chips background
  surfaceInverse: '#000000',    // headers / tab bar (kept dark to match)
  onSurface: '#FFFFFF',
  onSurfaceInverse: '#FFFFFF',

  // Brand & team colours
  brand: '#FFC700',             // Populus yellow — global accent
  brandPrimary: '#FF453A',      // Team A (warm red)
  onBrandPrimary: '#FFFFFF',
  brandSecondary: '#FFC700',    // Team B / yellow accent
  onBrandSecondary: '#000000',
  brandTertiary: '#2A2A2A',     // dark neutral (was gray on light bg)

  // Feedback
  success: '#30D158',
  error: '#FF453A',

  // Structure
  border: '#1F1F1F',            // hairline separator on dark bg
  borderStrong: '#2A2A2A',      // stronger outline where needed
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
