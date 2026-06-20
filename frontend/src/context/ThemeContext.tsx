import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { Theme } from '../themes'
import { themeToCssVars } from '../themes'
import { minimalist } from '../themes/minimalist'
import { rational } from '../themes/rational'
import { brutalist } from '../themes/brutalist'
import { retro } from '../themes/retro'
import { DARK_VARIANTS } from '../themes/dark'

const THEMES: Theme[] = [minimalist, rational, brutalist, retro]

interface ThemeContextValue {
  theme: Theme
  themes: Theme[]
  setTheme: (name: string) => void
  dark: boolean
  toggleDark: () => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: minimalist,
  themes: THEMES,
  setTheme: () => {},
  dark: false,
  toggleDark: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem('yamibo-theme')
    return THEMES.find(t => t.name === saved) || minimalist
  })
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
        for (const [k, v] of Object.entries(overrides)) root.style.setProperty(k, v)
      }
    } else {
      const badgeKeys = allDarkKeys.filter(k => k.startsWith('--badge-'))
      for (const k of badgeKeys) root.style.removeProperty(k)
    }
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
