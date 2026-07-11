import type { Theme } from './index'

export const hudScifi: Theme = {
  name: 'hud-scifi',
  label: '科幻HUD',
  labelEn: 'HUD Sci-Fi',
  fonts: {
    sans: '"Share Tech Mono", "JetBrains Mono", monospace',
    serif: '"Orbitron", "Share Tech Mono", monospace',
    mono: '"Share Tech Mono", "JetBrains Mono", monospace',
  },
  colors: {
    bgPage: '#020617',
    bgSurface: '#0a1020',
    bgMuted: '#0f1a2e',
    bgHeader: '#080e1a',
    border: 'rgba(0,255,255,0.15)',
    borderLight: 'rgba(0,255,255,0.06)',
    textPrimary: '#c8f0ff',
    textSecondary: '#6098b0',
    textTertiary: '#306080',
    textHeader: '#6098b0',
    accent: '#00e5ff',
    accentLight: 'rgba(0,229,255,0.10)',
    accentText: '#40f0ff',
    statusOk: '#00ff88',
    statusWarn: '#ffcc00',
    statusError: '#ff3355',
    statusMuted: '#306080',
    badgeOkBg: 'rgba(0,255,136,0.10)',
    badgeWarnBg: 'rgba(255,204,0,0.10)',
    badgeErrorBg: 'rgba(255,51,85,0.10)',
    badgeAccentBg: 'rgba(0,229,255,0.10)',
    badgeMutedBg: '#0f1a2e',
    badgeOkText: '#00ff88',
    badgeWarnText: '#ffcc00',
    badgeErrorText: '#ff3355',
    badgeAccentText: '#40f0ff',
    badgeComicBg: 'rgba(0,229,255,0.10)',
    badgeComicText: '#40f0ff',
    badgeNovelBg: 'rgba(128,180,255,0.12)',
    badgeNovelText: '#80b4ff',
    badgeDiscussionBg: 'rgba(255,204,0,0.10)',
    badgeDiscussionText: '#ffcc00',
    badgeMixedBg: 'rgba(0,229,255,0.08)',
    badgeMixedText: '#40f0ff',
  },
  radius: { sm: '0px', md: '2px', lg: '4px', full: '0px' },
  shadow: {
    card: 'none',
    elevated: '0 0 16px rgba(0,229,255,0.08)',
    glow: '0 0 6px rgba(0,255,255,0.25), 0 0 16px rgba(0,229,255,0.10)',
  },
  effects: {
    textGlow: '0 0 6px rgba(0,255,255,0.5), 0 0 16px rgba(0,229,255,0.2)',
    extraCSS: `
      body {
        border-top: 1px solid rgba(0,229,255,0.5);
        border-bottom: 1px solid rgba(0,229,255,0.5);
      }
      body::before {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,255,0.03) 2px, rgba(0,255,255,0.03) 4px);
      }
    `,
  },
}
