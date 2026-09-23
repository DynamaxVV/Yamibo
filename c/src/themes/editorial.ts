import type { Theme } from './index'

export const editorial: Theme = {
  name: 'editorial',
  label: '纸墨典藏',
  labelEn: 'Editorial',
  fonts: {
    sans: '"Geist", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans", sans-serif',
    serif: '"Baskerville", "Iowan Old Style", "Songti SC", "STSong", "Source Han Serif SC", "Hiragino Mincho ProN", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", Menlo, monospace',
  },
  colors: {
    bgPage: '#fffdf4', bgSurface: '#fffdf8', bgMuted: '#f6efdf', bgHeader: '#f1e8d8',
    border: '#d1bd98', borderLight: '#e6d9c3',
    textPrimary: '#12100e', textSecondary: '#5c5248', textTertiary: '#6b5d51', textHeader: '#49372c',
    accent: '#682516', accentLight: '#f4e6cf', accentText: '#682516',
    statusOk: '#176b46', statusWarn: '#8a5100', statusError: '#a42d23', statusMuted: '#6b5d51',
    badgeOkBg: '#e6f2e9', badgeWarnBg: '#fff1d4', badgeErrorBg: '#fbe6e1',
    badgeAccentBg: '#f4e6cf', badgeMutedBg: '#f2e9da',
  },
  radius: { sm: '4px', md: '4px', lg: '6px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 1px 3px rgba(58, 31, 18, .08)' },
}
