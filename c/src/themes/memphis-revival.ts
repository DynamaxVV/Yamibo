import type { Theme } from './index'

export const memphisRevival: Theme = {
  name: 'memphis-revival',
  label: '孟菲斯复兴',
  labelEn: 'Memphis Revival',
  fonts: {
    sans: '"Poppins", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Archivo Black", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#fff5e6',
    bgSurface: '#ffffff',
    bgMuted: '#fff0d6',
    bgHeader: '#ffecd0',
    border: '#000000',
    borderLight: '#e0d0b0',
    textPrimary: '#000000',
    textSecondary: '#4a3a20',
    textTertiary: '#8a7a60',
    textHeader: '#4a3a20',
    accent: '#ff6b6b',
    accentLight: '#ffe0e0',
    accentText: '#cc4040',
    statusOk: '#4ecdc4',
    statusWarn: '#ffe66d',
    statusError: '#ff6b6b',
    statusMuted: '#8a7a60',
    badgeOkBg: '#d0f8f4',
    badgeWarnBg: '#fff8d0',
    badgeErrorBg: '#ffe0e0',
    badgeAccentBg: '#ffe0e0',
    badgeMutedBg: '#fff0d6',
    badgeOkText: '#000000',
    badgeWarnText: '#000000',
    badgeErrorText: '#000000',
    badgeAccentText: '#000000',
    badgeComicBg: '#A8E6CF',
    badgeComicText: '#000000',
    badgeNovelBg: '#DDA0DD',
    badgeNovelText: '#000000',
    badgeDiscussionBg: '#FFE66D',
    badgeDiscussionText: '#000000',
    badgeMixedBg: '#4ECDC4',
    badgeMixedText: '#000000',
  },
  radius: { sm: '4px', md: '8px', lg: '12px', full: '0px' },
  shadow: {
    card: '4px 4px 0 0 #000000',
    elevated: '6px 6px 0 0 #000000',
  },
  effects: {
    borderWidth: '3px',
    extraCSS: `
      body::before {
        content: '';
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 16px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.5;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 20'%3E%3Cpath d='M0 10 Q 25 0 50 10 T 100 10' fill='none' stroke='%23FF6B6B' stroke-width='3'/%3E%3C/svg%3E");
        background-repeat: repeat-x;
      }
    `,
  },
}
