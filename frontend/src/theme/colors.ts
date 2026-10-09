/**
 * Light and dark theme color tokens for the Blood Donation app.
 *
 * Design: Red-centric palette (blood donation theme) with modern,
 * premium feel. Dark mode uses deep charcoals with warm accent reds.
 */

export const Colors = {
  light: {
    // Primary — Blood Red
    primary: '#DC2626',
    primaryDark: '#B91C1C',
    primaryLight: '#FEE2E2',
    primarySurface: '#FFF5F5',

    // Secondary — Deep Warm
    secondary: '#9333EA',
    secondaryLight: '#F3E8FF',

    // Accent
    accent: '#F59E0B',
    accentLight: '#FEF3C7',

    // Backgrounds
    background: '#FAFAFA',
    surface: '#FFFFFF',
    surfaceVariant: '#F5F5F5',
    card: '#FFFFFF',

    // Text
    text: '#1A1A1A',
    textSecondary: '#6B7280',
    textTertiary: '#9CA3AF',
    textOnPrimary: '#FFFFFF',

    // Status
    success: '#059669',
    successLight: '#D1FAE5',
    warning: '#D97706',
    warningLight: '#FEF3C7',
    error: '#DC2626',
    errorLight: '#FEE2E2',
    info: '#2563EB',
    infoLight: '#DBEAFE',

    // Blood group badge colors
    bloodBadge: '#DC2626',
    bloodBadgeText: '#FFFFFF',

    // Borders & Dividers
    border: '#E5E7EB',
    divider: '#F3F4F6',

    // Status-specific
    pending: '#F59E0B',
    accepted: '#059669',
    completed: '#2563EB',
    cancelled: '#6B7280',

    // Shadows
    shadow: 'rgba(0, 0, 0, 0.08)',
    shadowStrong: 'rgba(0, 0, 0, 0.15)',

    // Tab bar
    tabBar: '#FFFFFF',
    tabBarActive: '#DC2626',
    tabBarInactive: '#9CA3AF',

    // Notification badge
    notificationBadge: '#DC2626',
  },

  dark: {
    // Primary — Softened Red for dark backgrounds
    primary: '#EF4444',
    primaryDark: '#DC2626',
    primaryLight: '#451A1A',
    primarySurface: '#1F1215',

    // Secondary
    secondary: '#A855F7',
    secondaryLight: '#2D1854',

    // Accent
    accent: '#FBBF24',
    accentLight: '#3D2E0A',

    // Backgrounds
    background: '#0F0F0F',
    surface: '#1A1A1A',
    surfaceVariant: '#262626',
    card: '#1E1E1E',

    // Text
    text: '#F5F5F5',
    textSecondary: '#A3A3A3',
    textTertiary: '#737373',
    textOnPrimary: '#FFFFFF',

    // Status
    success: '#34D399',
    successLight: '#064E3B',
    warning: '#FBBF24',
    warningLight: '#3D2E0A',
    error: '#F87171',
    errorLight: '#451A1A',
    info: '#60A5FA',
    infoLight: '#1E3A5F',

    // Blood group badge colors
    bloodBadge: '#EF4444',
    bloodBadgeText: '#FFFFFF',

    // Borders & Dividers
    border: '#333333',
    divider: '#262626',

    // Status-specific
    pending: '#FBBF24',
    accepted: '#34D399',
    completed: '#60A5FA',
    cancelled: '#737373',

    // Shadows
    shadow: 'rgba(0, 0, 0, 0.3)',
    shadowStrong: 'rgba(0, 0, 0, 0.5)',

    // Tab bar
    tabBar: '#1A1A1A',
    tabBarActive: '#EF4444',
    tabBarInactive: '#737373',

    // Notification badge
    notificationBadge: '#EF4444',
  },
};

export type ThemeColors = typeof Colors.light;
