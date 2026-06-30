import type { Theme } from './index'

export const inclusiveDesign: Theme = {
  name: 'inclusive-design',
  label: '包容性设计',
  labelEn: 'Inclusive Design',
  fonts: {
    sans: '"Atkinson Hyperlegible", "Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    serif: '"Atkinson Hyperlegible", Georgia, serif',
    mono: '"JetBrains Mono", "SF Mono", "Fira Code", monospace',
  },
  colors: {
    bgPage: '#ffffff',
    bgSurface: '#ffffff',
    bgMuted: '#f0f4f8',
    bgHeader: '#003366',
    border: '#000000',
    borderLight: '#c0c8d0',
    textPrimary: '#111827',
    textSecondary: '#374151',
    textTertiary: '#4b5563',
    textHeader: '#ffffff',
    accent: '#2563eb',
    accentLight: '#dbeafe',
    accentText: '#1d4ed8',
    statusOk: '#166534',
    statusWarn: '#f97316',
    statusError: '#991b1b',
    statusMuted: '#4b5563',
    badgeOkBg: '#dcfce7',
    badgeWarnBg: '#fff7ed',
    badgeErrorBg: '#fee2e2',
    badgeAccentBg: '#dbeafe',
    badgeMutedBg: '#f3f4f6',
    badgeOkText: '#166534',
    badgeWarnText: '#9a3412',
    badgeErrorText: '#991b1b',
    badgeAccentText: '#1d4ed8',
    badgeComicBg: '#dbeafe',
    badgeComicText: '#1d4ed8',
    badgeNovelBg: '#ede9fe',
    badgeNovelText: '#6d28d9',
    badgeDiscussionBg: '#fff7ed',
    badgeDiscussionText: '#9a3412',
    badgeMixedBg: '#fce7f3',
    badgeMixedText: '#be185d',
  },
  radius: { sm: '4px', md: '8px', lg: '12px', full: '9999px' },
  shadow: { card: 'none', elevated: '0 2px 8px rgba(0,0,0,0.08)' },
  effects: {
    borderWidth: '3px',
    extraCSS: `
      :focus-visible {
        outline: 4px solid #0066cc;
        outline-offset: 3px;
      }
      @media (prefers-reduced-motion: reduce) {
        * { animation: none !important; transition: none !important; }
      }
    `,
  },
}
