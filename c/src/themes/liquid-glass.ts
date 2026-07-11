import type { Theme } from './index'

export const liquidGlass: Theme = {
  name: 'liquid-glass',
  label: '液态玻璃',
  labelEn: 'Liquid Glass',
  fonts: {
    sans: '"Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Inter", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#2d1b4e',
    bgSurface: 'rgba(255,255,255,0.10)',
    bgMuted: 'rgba(255,255,255,0.05)',
    bgHeader: 'rgba(255,255,255,0.06)',
    border: 'rgba(255,255,255,0.18)',
    borderLight: 'rgba(255,255,255,0.08)',
    textPrimary: '#ffffff',
    textSecondary: '#d0c0e8',
    textTertiary: '#9080b0',
    textHeader: '#d0c0e8',
    accent: '#c084fc',
    accentLight: 'rgba(192,132,252,0.15)',
    accentText: '#d8b4fe',
    statusOk: '#6ee7b7',
    statusWarn: '#fcd34d',
    statusError: '#fca5a5',
    statusMuted: '#9080b0',
    badgeOkBg: 'rgba(110,231,183,0.15)',
    badgeWarnBg: 'rgba(252,211,77,0.15)',
    badgeErrorBg: 'rgba(252,165,165,0.15)',
    badgeAccentBg: 'rgba(192,132,252,0.15)',
    badgeMutedBg: 'rgba(255,255,255,0.05)',
    badgeOkText: '#6ee7b7',
    badgeWarnText: '#fcd34d',
    badgeErrorText: '#fca5a5',
    badgeAccentText: '#d8b4fe',
    badgeComicBg: 'rgba(192,132,252,0.15)',
    badgeComicText: '#d8b4fe',
    badgeNovelBg: 'rgba(192,132,252,0.15)',
    badgeNovelText: '#c084fc',
    badgeDiscussionBg: 'rgba(252,211,77,0.15)',
    badgeDiscussionText: '#fcd34d',
    badgeMixedBg: 'rgba(216,180,254,0.15)',
    badgeMixedText: '#d8b4fe',
  },
  radius: { sm: '12px', md: '20px', lg: '30px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 8px 40px rgba(0,0,0,0.3)' },
  effects: {
    gradientPage: 'linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%)',
    backdropBlur: 'blur(30px)',
    extraCSS: `
      @keyframes liquid-morph {
        0%, 100% { border-radius: 60% 40% 30% 70% / 60% 30% 70% 40%; }
        50% { border-radius: 30% 60% 70% 40% / 50% 60% 30% 60%; }
      }
      @keyframes liquid-float {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-24px); }
      }
      body::before {
        content: '';
        position: fixed;
        top: 10%;
        left: 5%;
        width: 280px;
        height: 280px;
        pointer-events: none;
        z-index: 0;
        background: rgba(244,114,182,0.30);
        filter: blur(60px);
        border-radius: 60% 40% 30% 70% / 60% 30% 70% 40%;
        animation: liquid-morph 8s ease-in-out infinite, liquid-float 6s ease-in-out infinite;
      }
      body::after {
        content: '';
        position: fixed;
        bottom: 8%;
        right: 6%;
        width: 320px;
        height: 320px;
        pointer-events: none;
        z-index: 0;
        background: rgba(103,232,249,0.25);
        filter: blur(70px);
        border-radius: 30% 60% 70% 40% / 50% 60% 30% 60%;
        animation: liquid-morph 10s ease-in-out infinite, liquid-float 7s ease-in-out infinite reverse;
      }
    `,
  },
}
