import type { Theme } from './index'

export const retroFuturism: Theme = {
  name: 'retro-futurism',
  label: '复古未来主义',
  labelEn: 'Retro-Futurism',
  fonts: {
    sans: '"Orbitron", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Orbitron", Georgia, serif',
    mono: '"Share Tech Mono", "JetBrains Mono", monospace',
  },
  colors: {
    bgPage: '#0a0a1a',
    bgSurface: '#12122a',
    bgMuted: '#1a1a3a',
    bgHeader: '#101028',
    border: '#1a1a4a',
    borderLight: '#15153a',
    textPrimary: '#e0e8ff',
    textSecondary: '#8090c0',
    textTertiary: '#5060a0',
    textHeader: '#8090c0',
    accent: '#0080ff',
    accentLight: '#0a1a40',
    accentText: '#40a0ff',
    statusOk: '#00ff88',
    statusWarn: '#ffaa00',
    statusError: '#ff006e',
    statusMuted: '#5060a0',
    badgeOkBg: '#0a2a1a',
    badgeWarnBg: '#2a1a0a',
    badgeErrorBg: '#2a0a1a',
    badgeAccentBg: '#0a1a40',
    badgeMutedBg: '#1a1a3a',
    badgeOkText: '#00ff88',
    badgeWarnText: '#ffaa00',
    badgeErrorText: '#ff006e',
    badgeAccentText: '#40a0ff',
    badgeComicBg: '#0a1a40',
    badgeComicText: '#40a0ff',
    badgeNovelBg: '#2a0a30',
    badgeNovelText: '#d080ff',
    badgeDiscussionBg: '#2a1a0a',
    badgeDiscussionText: '#ffaa00',
    badgeMixedBg: '#1a0a30',
    badgeMixedText: '#c080ff',
  },
  radius: { sm: '4px', md: '8px', lg: '12px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 0 20px rgba(0,128,255,0.1)' },
  effects: {
    textGlow: '0 0 8px rgba(0,128,255,0.5), 0 0 20px rgba(0,128,255,0.2)',
    extraCSS: `
      body::before {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.5;
        background-image:
          linear-gradient(rgba(0,128,255,0.10) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0,128,255,0.10) 1px, transparent 1px);
        background-size: 50px 50px;
      }
      body::after {
        content: '';
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.25;
        background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.3) 2px, rgba(0,0,0,0.3) 4px);
      }
    `,
  },
}
