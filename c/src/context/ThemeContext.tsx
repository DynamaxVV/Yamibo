import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { Theme } from '../themes'
import { themeToCssVars } from '../themes'
import { minimalist } from '../themes/minimalist'
import { editorial } from '../themes/editorial'
import { rational } from '../themes/rational'
import { brutalist } from '../themes/brutalist'
import { retro } from '../themes/retro'
import { flatDesign } from '../themes/flat-design'
import { organicBiophilic } from '../themes/organic-biophilic'
import { eInkPaper } from '../themes/e-ink-paper'
import { neumorphism } from '../themes/neumorphism'
import { glassmorphism } from '../themes/glassmorphism'
import { claymorphism } from '../themes/claymorphism'
import { neubrutalism } from '../themes/neubrutalism'
import { memphisRevival } from '../themes/memphis-revival'
import { retroFuturism } from '../themes/retro-futurism'
import { auroraUi } from '../themes/aurora-ui'
import { neonGlow } from '../themes/neon-glow'
import { y2kRevival } from '../themes/y2k-revival'
import { vaporwave } from '../themes/vaporwave'
import { cyberpunk } from '../themes/cyberpunk'
import { hudScifi } from '../themes/hud-scifi'
import { pixelArt } from '../themes/pixel-art'
import { spatialUi } from '../themes/spatial-ui'
import { genZChaos } from '../themes/gen-z-chaos'
import { liquidGlass } from '../themes/liquid-glass'
import { inclusiveDesign } from '../themes/inclusive-design'
import { DARK_VARIANTS } from '../themes/dark'
import { THEME_FONTS } from '../themes/fonts'

const THEMES: Theme[] = [editorial, minimalist, rational, brutalist, retro, flatDesign, organicBiophilic, eInkPaper, neumorphism, glassmorphism, claymorphism, neubrutalism, memphisRevival, retroFuturism, auroraUi, neonGlow, y2kRevival, vaporwave, cyberpunk, hudScifi, pixelArt, spatialUi, genZChaos, liquidGlass, inclusiveDesign]

interface ThemeContextValue {
  theme: Theme
  themes: Theme[]
  setTheme: (name: string) => void
  dark: boolean
  toggleDark: () => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: editorial,
  themes: THEMES,
  setTheme: () => {},
  dark: false,
  toggleDark: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  // The current interface has one design language; migrate older saved themes
  // so legacy variables cannot silently recolor individual pages.
  const [theme, setThemeState] = useState<Theme>(editorial)
  const [dark, setDark] = useState(() => localStorage.getItem('yamibo-dark') === '1')

  useEffect(() => {
    const vars = themeToCssVars(theme)
    const root = document.documentElement
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value)
    }
    const allDarkKeys = [
      '--bg-page', '--bg-surface', '--bg-muted', '--bg-header',
      '--border', '--border-light',
      '--text-primary', '--text-secondary', '--text-tertiary', '--text-header',
      '--accent', '--accent-light', '--accent-text',
      '--badge-ok-bg', '--badge-ok-text', '--badge-warn-bg', '--badge-warn-text',
      '--badge-error-bg', '--badge-error-text', '--badge-muted-bg', '--badge-muted-text',
      '--badge-accent-bg', '--badge-accent-text',
      '--badge-comic-bg', '--badge-comic-text', '--badge-novel-bg', '--badge-novel-text',
      '--badge-discussion-bg', '--badge-discussion-text', '--badge-mixed-bg', '--badge-mixed-text',
      '--gradient-page', '--text-glow', '--shadow-glow',
    ]
    if (dark) {
      const d = DARK_VARIANTS[theme.name]
      if (d) {
        const overrides: Record<string, string> = {
          '--bg-page': d.bgPage, '--bg-surface': d.bgSurface, '--bg-muted': d.bgMuted,
          '--bg-header': d.bgHeader, '--border': d.border, '--border-light': d.borderLight,
          '--text-primary': d.textPrimary, '--text-secondary': d.textSecondary,
          '--text-tertiary': d.textTertiary, '--text-header': d.textHeader,
          '--accent': d.accent, '--accent-light': d.accentLight, '--accent-text': d.accentText,
          '--badge-ok-bg': d.badgeOkBg, '--badge-ok-text': d.badgeOkText,
          '--badge-warn-bg': d.badgeWarnBg, '--badge-warn-text': d.badgeWarnText,
          '--badge-error-bg': d.badgeErrorBg, '--badge-error-text': d.badgeErrorText,
          '--badge-muted-bg': d.badgeMutedBg, '--badge-muted-text': d.badgeMutedText,
          '--badge-accent-bg': d.badgeAccentBg, '--badge-accent-text': d.badgeAccentText,
          '--badge-comic-bg': d.badgeComicBg, '--badge-comic-text': d.badgeComicText,
          '--badge-novel-bg': d.badgeNovelBg, '--badge-novel-text': d.badgeNovelText,
          '--badge-discussion-bg': d.badgeDiscussionBg, '--badge-discussion-text': d.badgeDiscussionText,
          '--badge-mixed-bg': d.badgeMixedBg, '--badge-mixed-text': d.badgeMixedText,
        }
        if (d.gradientPage) overrides['--gradient-page'] = d.gradientPage
        if (d.textGlow) overrides['--text-glow'] = d.textGlow
        if (d.shadowGlow) overrides['--shadow-glow'] = d.shadowGlow
        for (const [k, v] of Object.entries(overrides)) root.style.setProperty(k, v)
      }
    } else {
      const badgeKeys = allDarkKeys.filter(k => k.startsWith('--badge-'))
      for (const k of badgeKeys) root.style.removeProperty(k)
      root.style.removeProperty('--gradient-page')
      root.style.removeProperty('--text-glow')
      root.style.removeProperty('--shadow-glow')
    }

    // Font injection
    const fontFamilies = THEME_FONTS[theme.name]
    document.querySelectorAll('[id^="yamibo-font-"]').forEach(el => el.remove())
    if (fontFamilies) {
      fontFamilies.forEach((family, i) => {
        const link = document.createElement('link')
        link.id = `yamibo-font-${i}`
        link.rel = 'stylesheet'
        link.href = `https://fonts.googleapis.com/css2?family=${family}&display=swap`
        document.head.appendChild(link)
      })
    }

    // ExtraCSS injection
    const extraId = 'yamibo-theme-extra'
    let extraEl = document.getElementById(extraId) as HTMLStyleElement | null
    const extraCss = (dark && DARK_VARIANTS[theme.name]?.extraCSS) || theme.effects?.extraCSS || ''
    if (extraCss) {
      if (!extraEl) {
        extraEl = document.createElement('style')
        extraEl.id = extraId
        document.head.appendChild(extraEl)
      }
      extraEl.textContent = extraCss
    } else if (extraEl) {
      extraEl.remove()
    }

    root.classList.toggle('dark', dark)
    root.setAttribute('data-theme', theme.name + (dark ? '-dark' : ''))
    localStorage.setItem('yamibo-theme', theme.name)
    localStorage.setItem('yamibo-dark', dark ? '1' : '0')
  }, [theme, dark])

  const setTheme = (name: string) => {
    const found = THEMES.find(t => t.name === name)
    if (found) setThemeState(found)
  }

  const toggleDark = () => setDark(d => !d)

  return (
    <ThemeContext.Provider value={{ theme, themes: THEMES, setTheme, dark, toggleDark }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}
