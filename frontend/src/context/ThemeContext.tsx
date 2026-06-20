import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { Theme } from '../themes'
import { themeToCssVars } from '../themes'
import { minimalist } from '../themes/minimalist'
import { rational } from '../themes/rational'
import { brutalist } from '../themes/brutalist'
import { retro } from '../themes/retro'

const THEMES: Theme[] = [minimalist, rational, brutalist, retro]

interface ThemeContextValue {
  theme: Theme
  themes: Theme[]
  setTheme: (name: string) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: minimalist,
  themes: THEMES,
  setTheme: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem('yamibo-theme')
    return THEMES.find(t => t.name === saved) || minimalist
  })

  useEffect(() => {
    const vars = themeToCssVars(theme)
    const root = document.documentElement
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value)
    }
    root.setAttribute('data-theme', theme.name)
    localStorage.setItem('yamibo-theme', theme.name)
  }, [theme])

  const setTheme = (name: string) => {
    const found = THEMES.find(t => t.name === name)
    if (found) setThemeState(found)
  }

  return (
    <ThemeContext.Provider value={{ theme, themes: THEMES, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}
