import type { Theme } from './index'

export const cyberpunk: Theme = {
  name: 'cyberpunk',
  label: '赛博朋克',
  labelEn: 'Cyberpunk',
  fonts: {
    sans: '"Share Tech Mono", "JetBrains Mono", monospace',
    serif: '"Orbitron", "Share Tech Mono", monospace',
    mono: '"Share Tech Mono", "JetBrains Mono", monospace',
  },
  colors: {
    bgPage: '#000000',
    bgSurface: '#0d0d0d',
    bgMuted: '#141414',
    bgHeader: '#0a0a0a',
    border: 'rgba(0,255,255,0.2)',
    borderLight: 'rgba(0,255,255,0.08)',
    textPrimary: '#e0f0f0',
    textSecondary: '#80b0b0',
    textTertiary: '#508080',
    textHeader: '#80b0b0',
    accent: '#ff00ff',
    accentLight: 'rgba(255,0,255,0.12)',
    accentText: '#ff40ff',
    statusOk: '#00ffcc',
    statusWarn: '#ffff00',
    statusError: '#ff0044',
    statusMuted: '#508080',
    badgeOkBg: 'rgba(0,255,204,0.12)',
    badgeWarnBg: 'rgba(255,255,0,0.12)',
    badgeErrorBg: 'rgba(255,0,68,0.12)',
    badgeAccentBg: 'rgba(255,0,255,0.12)',
    badgeMutedBg: '#141414',
    badgeOkText: '#00ffcc',
    badgeWarnText: '#ffff00',
    badgeErrorText: '#ff0044',
    badgeAccentText: '#ff40ff',
    badgeComicBg: 'rgba(0,255,255,0.12)',
    badgeComicText: '#00ffff',
    badgeNovelBg: 'rgba(255,0,255,0.12)',
    badgeNovelText: '#ff40ff',
    badgeDiscussionBg: 'rgba(255,255,0,0.12)',
    badgeDiscussionText: '#ffff00',
    badgeMixedBg: 'rgba(0,255,255,0.08)',
    badgeMixedText: '#00ffff',
  },
  radius: { sm: '0px', md: '0px', lg: '0px', full: '0px' },
  shadow: {
    card: 'none',
    elevated: '0 0 20px rgba(255,0,255,0.1)',
    glow: '0 0 8px rgba(0,255,255,0.3), 0 0 20px rgba(255,0,255,0.15)',
  },
  effects: {
    textGlow: '0 0 5px #ff00ff, 0 0 10px #ff00ff, 0 0 20px #00ffff',
    extraCSS: `
      @keyframes cyber-glitch {
        0%, 100% { transform: translate(0); }
        20% { transform: translate(-2px, 2px); }
        40% { transform: translate(-2px, -2px); }
        60% { transform: translate(2px, 2px); }
        80% { transform: translate(2px, -2px); }
      }
      h1:hover { animation: cyber-glitch 0.3s infinite; }
      body::after {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.06;
        background: repeating-linear-gradient(0deg, rgba(0,0,0,0.1), rgba(0,0,0,0.1) 1px, transparent 1px, transparent 2px);
      }
    `,
  },
}
