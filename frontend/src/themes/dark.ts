export interface DarkColors {
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
  badgeOkBg: string
  badgeOkText: string
  badgeWarnBg: string
  badgeWarnText: string
  badgeErrorBg: string
  badgeErrorText: string
  badgeMutedBg: string
  badgeMutedText: string
  badgeAccentBg: string
  badgeAccentText: string
  badgeComicBg: string
  badgeComicText: string
  badgeNovelBg: string
  badgeNovelText: string
  badgeDiscussionBg: string
  badgeDiscussionText: string
  badgeMixedBg: string
  badgeMixedText: string
}

export const DARK_VARIANTS: Record<string, DarkColors> = {
  minimalist: {
    bgPage: '#0d1117', bgSurface: '#161b22', bgMuted: '#21262d', bgHeader: '#161b22',
    border: '#30363d', borderLight: '#21262d',
    textPrimary: '#e6edf3', textSecondary: '#8b949e', textTertiary: '#6e7681', textHeader: '#8b949e',
    accent: '#58a6ff', accentLight: '#1f3a5f', accentText: '#79c0ff',
    badgeOkBg: '#0d2818', badgeOkText: '#3fb950',
    badgeWarnBg: '#2d1e00', badgeWarnText: '#d29922',
    badgeErrorBg: '#3d1418', badgeErrorText: '#f85149',
    badgeMutedBg: '#21262d', badgeMutedText: '#8b949e',
    badgeAccentBg: '#0d2240', badgeAccentText: '#58a6ff',
    badgeComicBg: '#0d2240', badgeComicText: '#58a6ff',
    badgeNovelBg: '#0d2818', badgeNovelText: '#3fb950',
    badgeDiscussionBg: '#2d1e00', badgeDiscussionText: '#d29922',
    badgeMixedBg: '#1e1030', badgeMixedText: '#bc8cff',
  },
  rational: {
    bgPage: '#0f1115', bgSurface: '#1a1d24', bgMuted: '#242830', bgHeader: '#1a1d24',
    border: '#30363d', borderLight: '#242830',
    textPrimary: '#e2e5ea', textSecondary: '#8b919a', textTertiary: '#636973', textHeader: '#8b919a',
    accent: '#4dabf7', accentLight: '#1a3a5c', accentText: '#74c0fc',
    badgeOkBg: '#0d2818', badgeOkText: '#40c057',
    badgeWarnBg: '#2d1e00', badgeWarnText: '#fab005',
    badgeErrorBg: '#3d1418', badgeErrorText: '#fa5252',
    badgeMutedBg: '#242830', badgeMutedText: '#8b919a',
    badgeAccentBg: '#0d2240', badgeAccentText: '#4dabf7',
    badgeComicBg: '#0d2240', badgeComicText: '#4dabf7',
    badgeNovelBg: '#0d2818', badgeNovelText: '#40c057',
    badgeDiscussionBg: '#2d1e00', badgeDiscussionText: '#fab005',
    badgeMixedBg: '#1e1030', badgeMixedText: '#be4bdb',
  },
  brutalist: {
    bgPage: '#0a0a0a', bgSurface: '#141414', bgMuted: '#1e1e1e', bgHeader: '#1a1a1a',
    border: '#444444', borderLight: '#2a2a2a',
    textPrimary: '#e0e0e0', textSecondary: '#aaaaaa', textTertiary: '#777777', textHeader: '#ffffff',
    accent: '#ff4444', accentLight: '#3a1111', accentText: '#ff6666',
    badgeOkBg: '#1a2e1a', badgeOkText: '#66bb6a',
    badgeWarnBg: '#2e2a10', badgeWarnText: '#ffd54f',
    badgeErrorBg: '#3a1515', badgeErrorText: '#ef5350',
    badgeMutedBg: '#1e1e1e', badgeMutedText: '#aaaaaa',
    badgeAccentBg: '#3a1515', badgeAccentText: '#ff6666',
    badgeComicBg: '#1a2a3a', badgeComicText: '#64b5f6',
    badgeNovelBg: '#1a2e1a', badgeNovelText: '#66bb6a',
    badgeDiscussionBg: '#2e2a10', badgeDiscussionText: '#ffd54f',
    badgeMixedBg: '#2a1a3a', badgeMixedText: '#ce93d8',
  },
  retro: {
    bgPage: '#1a1712', bgSurface: '#242018', bgMuted: '#2e2a20', bgHeader: '#242018',
    border: '#4a4235', borderLight: '#3a3528',
    textPrimary: '#e8e0d0', textSecondary: '#b0a890', textTertiary: '#7a7468', textHeader: '#b0a890',
    accent: '#e07050', accentLight: '#3a1a10', accentText: '#f09070',
    badgeOkBg: '#1a2e18', badgeOkText: '#8bc34a',
    badgeWarnBg: '#2e2810', badgeWarnText: '#ffb74d',
    badgeErrorBg: '#3a1815', badgeErrorText: '#ef5350',
    badgeMutedBg: '#2e2a20', badgeMutedText: '#b0a890',
    badgeAccentBg: '#3a1815', badgeAccentText: '#f09070',
    badgeComicBg: '#1a2a30', badgeComicText: '#81d4fa',
    badgeNovelBg: '#1a2e18', badgeNovelText: '#8bc34a',
    badgeDiscussionBg: '#2e2810', badgeDiscussionText: '#ffb74d',
    badgeMixedBg: '#2a1830', badgeMixedText: '#ce93d8',
  },
}
