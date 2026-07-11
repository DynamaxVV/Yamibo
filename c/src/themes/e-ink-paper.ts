import type { Theme } from './index'

export const eInkPaper: Theme = {
  name: 'e-ink-paper',
  label: '电子墨水',
  labelEn: 'E-Ink Paper',
  fonts: {
    sans: '"Newsreader", Georgia, serif',
    serif: '"Newsreader", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#f5f1eb',
    bgSurface: '#faf7f3',
    bgMuted: '#ede7df',
    bgHeader: '#f0ebe4',
    border: '#2a2a2a',
    borderLight: '#d4cfc8',
    textPrimary: '#1a1a1a',
    textSecondary: '#444444',
    textTertiary: '#666666',
    textHeader: '#444444',
    accent: '#1a1a1a',
    accentLight: '#e8e4de',
    accentText: '#000000',
    statusOk: '#333333',
    statusWarn: '#555555',
    statusError: '#1a1a1a',
    statusMuted: '#888888',
    badgeOkBg: '#e6e6e6',
    badgeWarnBg: '#e0e0e0',
    badgeErrorBg: '#1a1a1a',
    badgeAccentBg: '#e8e4de',
    badgeMutedBg: '#f0f0f0',
    badgeOkText: '#333333',
    badgeWarnText: '#444444',
    badgeErrorText: '#ffffff',
    badgeAccentText: '#1a1a1a',
    badgeComicBg: '#e0e0e0',
    badgeComicText: '#1a1a1a',
    badgeNovelBg: '#e8e8e8',
    badgeNovelText: '#333333',
    badgeDiscussionBg: '#dcddcf',
    badgeDiscussionText: '#333333',
    badgeMixedBg: '#e4e4e4',
    badgeMixedText: '#1a1a1a',
  },
  radius: { sm: '2px', md: '4px', lg: '6px', full: '9999px' },
  shadow: { card: 'none', elevated: 'none' },
  effects: {
    extraCSS: `
      body::before {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.04;
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
      }
    `,
  },
}
