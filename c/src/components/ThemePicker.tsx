import { useState, useRef, useEffect } from 'react'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'

export function ThemePicker() {
  const { theme, themes, setTheme } = useTheme()
  const { lang } = useI18n()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const label = lang === 'en' ? theme.labelEn : theme.label

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="btn-subtle theme-picker-trigger"
        onClick={() => setOpen(o => !o)}
        title={label}
      >
        {label}
      </button>
      {open && (
        <div className="theme-picker-dropdown">
          {themes.map(th => {
            const thLabel = lang === 'en' ? th.labelEn : th.label
            return (
              <button
                key={th.name}
                className={`theme-picker-option${th.name === theme.name ? ' active' : ''}`}
                onClick={() => { setTheme(th.name); setOpen(false) }}
                title={thLabel}
              >
                <span className="theme-picker-swatch" style={{ background: th.colors.bgPage, borderColor: th.colors.border }} />
                <span className="theme-picker-name">{thLabel}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
