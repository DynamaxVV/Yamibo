import type { Theme } from './index'

export const genZChaos: Theme = {
  name: 'gen-z-chaos',
  label: 'Z世代混乱',
  labelEn: 'Gen Z Chaos',
  fonts: {
    sans: '"Anton", "Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Anton", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#fffb00',
    bgSurface: '#ffffff',
    bgMuted: '#fff8cc',
    bgHeader: '#fff200',
    border: '#000000',
    borderLight: '#cccc00',
    textPrimary: '#000000',
    textSecondary: '#333333',
    textTertiary: '#666666',
    textHeader: '#000000',
    accent: '#ff0055',
    accentLight: '#ffe0ea',
    accentText: '#cc0044',
    statusOk: '#00cc44',
    statusWarn: '#ff6600',
    statusError: '#ff0000',
    statusMuted: '#666666',
    badgeOkBg: '#ccffdd',
    badgeWarnBg: '#ffe0cc',
    badgeErrorBg: '#ffcccc',
    badgeAccentBg: '#ffd0e0',
    badgeMutedBg: '#fff8cc',
    badgeOkText: '#006633',
    badgeWarnText: '#cc4400',
    badgeErrorText: '#cc0000',
    badgeAccentText: '#cc0044',
    badgeComicBg: '#ccffdd',
    badgeComicText: '#006633',
    badgeNovelBg: '#e0ccff',
    badgeNovelText: '#5500cc',
    badgeDiscussionBg: '#ffe0cc',
    badgeDiscussionText: '#cc4400',
    badgeMixedBg: '#ffd0e0',
    badgeMixedText: '#cc0044',
  },
  radius: { sm: '0px', md: '8px', lg: '20px', full: '9999px' },
  shadow: { card: '3px 3px 0 0 #000000', elevated: '6px 6px 0 0 #000000' },
  effects: {
    borderWidth: '2px',
    extraCSS: `
      @keyframes gen-z-glitch {
        0%, 100% { transform: translate(0); }
        20% { transform: translate(-2px, 2px); }
        40% { transform: translate(-2px, -2px); }
        60% { transform: translate(2px, 2px); }
        80% { transform: translate(2px, -2px); }
      }
      @keyframes gen-z-hue {
        0% { filter: hue-rotate(0deg); }
        100% { filter: hue-rotate(360deg); }
      }
      h1 { animation: gen-z-glitch 3s ease-in-out infinite; }
      .panel { filter: drop-shadow(3px 3px 0 rgba(0,0,0,0.25)); }
      .badge { font-weight: 900; text-transform: uppercase; letter-spacing: 1px; }
      .topbar { border-bottom-style: dashed; }
    `,
  },
}
