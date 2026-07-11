import type { Theme } from './index'

export const auroraUi: Theme = {
  name: 'aurora-ui',
  label: '极光界面',
  labelEn: 'Aurora UI',
  fonts: {
    sans: '"Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Inter", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#0a0a1a',
    bgSurface: 'rgba(255,255,255,0.05)',
    bgMuted: 'rgba(255,255,255,0.03)',
    bgHeader: 'rgba(255,255,255,0.03)',
    border: 'rgba(255,255,255,0.10)',
    borderLight: 'rgba(255,255,255,0.05)',
    textPrimary: '#f0e8ff',
    textSecondary: '#b0a8d8',
    textTertiary: '#7068a8',
    textHeader: '#b0a8d8',
    accent: '#a78bfa',
    accentLight: 'rgba(167,139,250,0.15)',
    accentText: '#c4b5fd',
    statusOk: '#6ee7b7',
    statusWarn: '#fcd34d',
    statusError: '#fca5a5',
    statusMuted: '#7068a8',
    badgeOkBg: 'rgba(110,231,183,0.15)',
    badgeWarnBg: 'rgba(252,211,77,0.15)',
    badgeErrorBg: 'rgba(252,165,165,0.15)',
    badgeAccentBg: 'rgba(167,139,250,0.15)',
    badgeMutedBg: 'rgba(255,255,255,0.03)',
    badgeOkText: '#6ee7b7',
    badgeWarnText: '#fcd34d',
    badgeErrorText: '#fca5a5',
    badgeAccentText: '#c4b5fd',
    badgeComicBg: 'rgba(167,139,250,0.15)',
    badgeComicText: '#c4b5fd',
    badgeNovelBg: 'rgba(216,180,254,0.15)',
    badgeNovelText: '#d8b4fe',
    badgeDiscussionBg: 'rgba(252,211,77,0.15)',
    badgeDiscussionText: '#fcd34d',
    badgeMixedBg: 'rgba(196,181,253,0.15)',
    badgeMixedText: '#c4b5fd',
  },
  radius: { sm: '8px', md: '14px', lg: '20px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 8px 32px rgba(0,0,0,0.3)' },
  effects: {
    gradientPage: 'linear-gradient(135deg, #0f0c29 0%, #302b63 30%, #4a3070 60%, #1a1030 100%)',
    backdropBlur: 'blur(16px)',
    extraCSS: `
      body::before {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 25%, #f093fb 50%, #f5576c 75%, #4facfe 100%);
        background-size: 400% 400%;
        animation: aurora-flow 15s ease infinite;
        opacity: 0.35;
      }
      @keyframes aurora-flow {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
      }
    `,
  },
}
