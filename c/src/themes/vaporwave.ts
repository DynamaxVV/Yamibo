import type { Theme } from './index'

export const vaporwave: Theme = {
  name: 'vaporwave',
  label: '蒸汽波',
  labelEn: 'Vaporwave',
  fonts: {
    sans: '"VT323", "Courier New", monospace',
    serif: '"Press Start 2P", "Courier New", monospace',
    mono: '"Press Start 2P", "VT323", monospace',
  },
  colors: {
    bgPage: '#1a0a2e',
    bgSurface: 'rgba(255,255,255,0.06)',
    bgMuted: 'rgba(255,255,255,0.03)',
    bgHeader: 'rgba(255,255,255,0.04)',
    border: 'rgba(255,113,206,0.3)',
    borderLight: 'rgba(255,255,255,0.06)',
    textPrimary: '#fff0f8',
    textSecondary: '#f0c0e0',
    textTertiary: '#a080b0',
    textHeader: '#f0c0e0',
    accent: '#ff71ce',
    accentLight: 'rgba(255,113,206,0.15)',
    accentText: '#ff90d8',
    statusOk: '#67e8f9',
    statusWarn: '#fcd34d',
    statusError: '#ff71ce',
    statusMuted: '#a080b0',
    badgeOkBg: 'rgba(103,232,249,0.15)',
    badgeWarnBg: 'rgba(252,211,77,0.15)',
    badgeErrorBg: 'rgba(255,113,206,0.15)',
    badgeAccentBg: 'rgba(255,113,206,0.15)',
    badgeMutedBg: 'rgba(255,255,255,0.03)',
    badgeOkText: '#67e8f9',
    badgeWarnText: '#fcd34d',
    badgeErrorText: '#ff71ce',
    badgeAccentText: '#ff90d8',
    badgeComicBg: 'rgba(103,232,249,0.15)',
    badgeComicText: '#67e8f9',
    badgeNovelBg: 'rgba(200,144,255,0.15)',
    badgeNovelText: '#c890ff',
    badgeDiscussionBg: 'rgba(252,211,77,0.15)',
    badgeDiscussionText: '#fcd34d',
    badgeMixedBg: 'rgba(255,144,216,0.15)',
    badgeMixedText: '#ff90d8',
  },
  radius: { sm: '0px', md: '4px', lg: '8px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 0 24px rgba(255,113,206,0.15)' },
  effects: {
    gradientPage: 'linear-gradient(180deg, #2e1065 0%, #be185d 50%, #06b6d4 100%)',
    textGlow: '0 0 5px #ff71ce, 0 0 10px #ff71ce, 0 0 20px #22d3ee',
    extraCSS: `
      .brand, h1, h2 {
        background: linear-gradient(180deg, #ffffff 0%, #c0c0c0 50%, #ffffff 51%, #c0c0c0 100%);
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
      }
      body::before {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.12;
        background-image: linear-gradient(#FF71CE 1px, transparent 1px), linear-gradient(90deg, #FF71CE 1px, transparent 1px);
        background-size: 40px 40px;
        animation: vaporwave-scroll 20s linear infinite;
      }
      @keyframes vaporwave-scroll {
        0% { background-position: 0 0; }
        100% { background-position: 0 40px; }
      }
    `,
  },
}
