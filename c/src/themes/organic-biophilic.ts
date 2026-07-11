import type { Theme } from './index'

export const organicBiophilic: Theme = {
  name: 'organic-biophilic',
  label: '有机自然',
  labelEn: 'Organic Biophilic',
  fonts: {
    sans: '"Nunito", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Cormorant Garamond", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#f5f1e8',
    bgSurface: '#faf7f2',
    bgMuted: '#e8e0d0',
    bgHeader: '#ede6d8',
    border: '#d4cdc0',
    borderLight: '#e8e0d0',
    textPrimary: '#3e4a32',
    textSecondary: '#5d6b4d',
    textTertiary: '#8a9a7a',
    textHeader: '#5d6b4d',
    accent: '#5d6b4d',
    accentLight: '#d4e4c1',
    accentText: '#3e4a32',
    statusOk: '#6b8c42',
    statusWarn: '#c4a24e',
    statusError: '#b8453a',
    statusMuted: '#8a9a7a',
    badgeOkBg: '#e0ecd0',
    badgeWarnBg: '#f5ecd0',
    badgeErrorBg: '#f0d8d4',
    badgeAccentBg: '#d4e4c1',
    badgeMutedBg: '#e8e0d0',
    badgeOkText: '#3e5a20',
    badgeWarnText: '#8a6a20',
    badgeErrorText: '#8a2820',
    badgeAccentText: '#3e4a32',
    badgeComicBg: '#d4e4c1',
    badgeComicText: '#3e4a32',
    badgeNovelBg: '#e0d8ec',
    badgeNovelText: '#4a3a5d',
    badgeDiscussionBg: '#f0e8c8',
    badgeDiscussionText: '#6a5a20',
    badgeMixedBg: '#e8dcd8',
    badgeMixedText: '#5d3a4a',
  },
  radius: { sm: '8px', md: '14px', lg: '20px', full: '9999px' },
  shadow: { card: '0 2px 12px rgba(62,74,50,0.06)', elevated: '0 4px 20px rgba(62,74,50,0.10)' },
  effects: {
    extraCSS: `
      .panel {
        border-radius: 20px 28px 20px 28px;
      }
      body::before {
        content: '';
        position: fixed;
        top: -120px;
        right: -120px;
        width: 360px;
        height: 360px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.10;
        background: #8fbc8f;
        border-radius: 30% 70% 70% 30% / 30% 30% 70% 70%;
      }
      body::after {
        content: '';
        position: fixed;
        bottom: -140px;
        left: -100px;
        width: 320px;
        height: 320px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.08;
        background: #c8b89e;
        border-radius: 5% 95% 10% 90% / 85% 15% 85% 15%;
      }
    `,
  },
}
