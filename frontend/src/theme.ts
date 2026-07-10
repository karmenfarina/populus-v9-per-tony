export const colors = {
  surface: '#F4F4F0',
  onSurface: '#0F0F0F',
  surfaceSecondary: '#FFFFFF',
  surfaceTertiary: '#EAEAEA',
  surfaceInverse: '#0F0F0F',
  onSurfaceInverse: '#F4F4F0',
  brand: '#0F0F0F',
  brandPrimary: '#FF3B30', // Team A (red)
  onBrandPrimary: '#FFFFFF',
  brandSecondary: '#FFE600', // Team B (yellow)
  onBrandSecondary: '#0F0F0F',
  brandTertiary: '#C2C2C2',
  success: '#00E676',
  error: '#FF3B30',
  border: '#0F0F0F',
  muted: '#6E6E6E',
} as const;

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48 };
export const radius = { sm: 0, md: 0, lg: 0, pill: 0 };
export const font = {
  displayFamily: undefined as string | undefined, // fallback system
  textFamily: undefined as string | undefined,
  sizes: { xs: 11, sm: 12, base: 14, lg: 16, xl: 20, xxl: 24, xxxl: 32, giant: 42 },
};

export const sideColor = (side: 'A' | 'B' | null | undefined) =>
  side === 'A' ? colors.brandPrimary : side === 'B' ? colors.brandSecondary : colors.brandTertiary;

export const onSideColor = (side: 'A' | 'B' | null | undefined) =>
  side === 'A' ? colors.onBrandPrimary : side === 'B' ? colors.onBrandSecondary : colors.onSurface;
