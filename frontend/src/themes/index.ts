export interface ThemeEffects {
  gradientPage?: string
  backdropBlur?: string
  textGlow?: string
  borderWidth?: string
  extraCSS?: string
}

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
    // 可选：徽章文字色与内容类型徽章配色。缺省则不注入对应 CSS 变量，
    // 由 styles.css 的硬编码兜底色生效（4 套基础主题保持现状）。
    badgeOkText?: string
    badgeWarnText?: string
    badgeErrorText?: string
    badgeAccentText?: string
    badgeMutedText?: string
    badgeComicBg?: string
    badgeComicText?: string
    badgeNovelBg?: string
    badgeNovelText?: string
    badgeDiscussionBg?: string
    badgeDiscussionText?: string
    badgeMixedBg?: string
    badgeMixedText?: string
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
    glow?: string
  }
  effects?: ThemeEffects
}

export function themeToCssVars(t: Theme): Record<string, string> {
  const vars: Record<string, string> = {
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
    ...(t.colors.badgeOkText ? { '--badge-ok-text': t.colors.badgeOkText } : {}),
    ...(t.colors.badgeWarnText ? { '--badge-warn-text': t.colors.badgeWarnText } : {}),
    ...(t.colors.badgeErrorText ? { '--badge-error-text': t.colors.badgeErrorText } : {}),
    ...(t.colors.badgeAccentText ? { '--badge-accent-text': t.colors.badgeAccentText } : {}),
    ...(t.colors.badgeMutedText ? { '--badge-muted-text': t.colors.badgeMutedText } : {}),
    ...(t.colors.badgeComicBg ? { '--badge-comic-bg': t.colors.badgeComicBg } : {}),
    ...(t.colors.badgeComicText ? { '--badge-comic-text': t.colors.badgeComicText } : {}),
    ...(t.colors.badgeNovelBg ? { '--badge-novel-bg': t.colors.badgeNovelBg } : {}),
    ...(t.colors.badgeNovelText ? { '--badge-novel-text': t.colors.badgeNovelText } : {}),
    ...(t.colors.badgeDiscussionBg ? { '--badge-discussion-bg': t.colors.badgeDiscussionBg } : {}),
    ...(t.colors.badgeDiscussionText ? { '--badge-discussion-text': t.colors.badgeDiscussionText } : {}),
    ...(t.colors.badgeMixedBg ? { '--badge-mixed-bg': t.colors.badgeMixedBg } : {}),
    ...(t.colors.badgeMixedText ? { '--badge-mixed-text': t.colors.badgeMixedText } : {}),
    '--radius-sm': t.radius.sm,
    '--radius-md': t.radius.md,
    '--radius-lg': t.radius.lg,
    '--radius-full': t.radius.full,
    '--shadow-card': t.shadow.card,
    '--shadow-elevated': t.shadow.elevated,
    '--shadow-glow': t.shadow.glow || 'none',
    '--gradient-page': t.effects?.gradientPage || '',
    '--backdrop-blur': t.effects?.backdropBlur || 'none',
    '--text-glow': t.effects?.textGlow || 'none',
    '--border-width': t.effects?.borderWidth || '1px',
  }
  return vars
}
