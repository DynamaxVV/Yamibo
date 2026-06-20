export interface Theme {
  name: string
  label: string
  labelEn: string
  fonts: {
    sans: string
    serif: string
    mono: string
  }
  colors: {
    bgPage: string
    bgSurface: string
    bgMuted: string
    bgHeader: string
    border: string
    borderLight: string
    textPrimary: string
    textSecondary: string
    textTertiary: string
    textHeader: string
    accent: string
    accentLight: string
    accentText: string
    statusOk: string
    statusWarn: string
    statusError: string
    statusMuted: string
    badgeOkBg: string
    badgeWarnBg: string
    badgeErrorBg: string
    badgeAccentBg: string
    badgeMutedBg: string
  }
  radius: {
    sm: string
    md: string
    lg: string
    full: string
  }
  shadow: {
    card: string
    elevated: string
  }
}

export function themeToCssVars(t: Theme): Record<string, string> {
  return {
    '--font-sans': t.fonts.sans,
    '--font-serif': t.fonts.serif,
    '--font-mono': t.fonts.mono,
    '--bg-page': t.colors.bgPage,
    '--bg-surface': t.colors.bgSurface,
    '--bg-muted': t.colors.bgMuted,
    '--bg-header': t.colors.bgHeader,
    '--border': t.colors.border,
    '--border-light': t.colors.borderLight,
    '--text-primary': t.colors.textPrimary,
    '--text-secondary': t.colors.textSecondary,
    '--text-tertiary': t.colors.textTertiary,
    '--text-header': t.colors.textHeader,
    '--accent': t.colors.accent,
    '--accent-light': t.colors.accentLight,
    '--accent-text': t.colors.accentText,
    '--status-ok': t.colors.statusOk,
    '--status-warn': t.colors.statusWarn,
    '--status-error': t.colors.statusError,
    '--status-muted': t.colors.statusMuted,
    '--badge-ok-bg': t.colors.badgeOkBg,
    '--badge-warn-bg': t.colors.badgeWarnBg,
    '--badge-error-bg': t.colors.badgeErrorBg,
    '--badge-accent-bg': t.colors.badgeAccentBg,
    '--badge-muted-bg': t.colors.badgeMutedBg,
    '--radius-sm': t.radius.sm,
    '--radius-md': t.radius.md,
    '--radius-lg': t.radius.lg,
    '--radius-full': t.radius.full,
    '--shadow-card': t.shadow.card,
    '--shadow-elevated': t.shadow.elevated,
  }
}
