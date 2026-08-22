import { NavLink, Outlet } from 'react-router-dom'
import { useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'
import { ThemePicker } from './ThemePicker'

export function Layout() {
  const { dark, toggleDark } = useTheme()
  const { lang, setLang, t } = useI18n()
  const floatActionsRef = useRef<HTMLDivElement>(null)
  const pressTimerRef = useRef<number | null>(null)
  const pressRef = useRef<{ pointerId: number; startX: number; startY: number } | null>(null)
  const suppressClickRef = useRef(false)
  const dragRef = useRef<{
    pointerId: number
    offsetX: number
    offsetY: number
    position: { left: number; top: number }
  } | null>(null)
  const [floatActionsDragging, setFloatActionsDragging] = useState(false)
  const [floatActionsCollapsed, setFloatActionsCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem('yamibo.float-actions-collapsed') === 'true'
    } catch {
      return false
    }
  })
  const [floatPosition, setFloatPosition] = useState<{ left: number; top: number } | null>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem('yamibo.float-actions-position') || 'null')
      if (stored && Number.isFinite(stored.left) && Number.isFinite(stored.top)) {
        return { left: stored.left, top: stored.top }
      }
    } catch {
      // Preferences are optional when storage is unavailable or malformed.
    }
    return null
  })

  const toggleFloatActions = () => {
    setFloatActionsCollapsed(collapsed => {
      const next = !collapsed
      try {
        window.localStorage.setItem('yamibo.float-actions-collapsed', String(next))
      } catch {
        // Preferences are optional when storage is unavailable.
      }
      return next
    })
  }

  const clearPressTimer = () => {
    if (pressTimerRef.current !== null) {
      window.clearTimeout(pressTimerRef.current)
      pressTimerRef.current = null
    }
  }

  const clampFloatPosition = (left: number, top: number) => {
    const rect = floatActionsRef.current?.getBoundingClientRect()
    const width = rect?.width ?? 36
    const height = rect?.height ?? 36
    return {
      left: Math.min(Math.max(0, left), Math.max(0, window.innerWidth - width)),
      top: Math.min(Math.max(0, top), Math.max(0, window.innerHeight - height)),
    }
  }

  const startFloatDrag = (pointerId: number, clientX: number, clientY: number) => {
    const element = floatActionsRef.current
    if (!element) return
    const rect = element.getBoundingClientRect()
    const position = clampFloatPosition(floatPosition?.left ?? rect.left, floatPosition?.top ?? rect.top)
    dragRef.current = {
      pointerId,
      offsetX: clientX - rect.left,
      offsetY: clientY - rect.top,
      position,
    }
    element.setPointerCapture(pointerId)
    setFloatPosition(position)
    setFloatActionsDragging(true)
    suppressClickRef.current = true
  }

  const handleFloatPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current) return
    clearPressTimer()
    pressRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY }
    pressTimerRef.current = window.setTimeout(() => {
      pressTimerRef.current = null
      const press = pressRef.current
      if (press) startFloatDrag(press.pointerId, press.startX, press.startY)
    }, 350)
  }

  const handleFloatPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) {
      const press = pressRef.current
      if (press && Math.hypot(event.clientX - press.startX, event.clientY - press.startY) > 8) {
        clearPressTimer()
        pressRef.current = null
      }
      return
    }
    const next = clampFloatPosition(event.clientX - drag.offsetX, event.clientY - drag.offsetY)
    drag.position = next
    setFloatPosition(next)
  }

  const finishFloatPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    clearPressTimer()
    pressRef.current = null
    const drag = dragRef.current
    if (!drag) return
    const element = floatActionsRef.current
    if (element?.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId)
    dragRef.current = null
    const savedPosition = floatActionsCollapsed
      ? { ...drag.position, left: drag.position.left <= window.innerWidth / 2 ? 0 : Math.max(0, window.innerWidth - 14) }
      : drag.position
    setFloatPosition(savedPosition)
    try {
      window.localStorage.setItem('yamibo.float-actions-position', JSON.stringify(savedPosition))
    } catch {
      // Preferences are optional when storage is unavailable.
    }
    window.setTimeout(() => { suppressClickRef.current = false }, 0)
    setFloatActionsDragging(false)
  }

  const handleFloatPointerLeave = () => {
    if (!dragRef.current) clearPressTimer()
  }

  const floatStyle: CSSProperties | undefined = floatPosition
    ? floatActionsCollapsed
      ? floatActionsDragging
        ? {
            top: `${Math.min(floatPosition.top, Math.max(0, window.innerHeight - 112))}px`,
            left: `${Math.min(floatPosition.left, Math.max(0, window.innerWidth - 14))}px`,
            right: 'auto', bottom: 'auto',
          }
        : {
          top: `${Math.min(floatPosition.top, Math.max(0, window.innerHeight - 112))}px`,
          ...(floatPosition.left <= window.innerWidth / 2
            ? { left: 0, right: 'auto' }
            : { right: 0, left: 'auto' }),
          bottom: 'auto',
        }
      : {
          top: `${Math.min(floatPosition.top, Math.max(0, window.innerHeight - 146))}px`,
          left: `${Math.min(floatPosition.left, Math.max(0, window.innerWidth - 38))}px`,
          right: 'auto', bottom: 'auto',
        }
    : undefined
  const floatActionsOnLeft = floatPosition !== null && floatPosition.left <= window.innerWidth / 2

  const NAV_ITEMS = [
    { to: '/', key: 'dashboard' },
    { to: '/jobs', key: 'jobs' },
    { to: '/threads', key: 'threads' },
    { to: '/series', key: 'series' },
    { to: '/review', key: 'review' },
    { to: '/exports', key: 'exports' },
    { to: '/forum', key: 'remoteForum' },
    { to: '/forums', key: 'forums' },
    { to: '/rag', key: 'rag' },
    { to: '/chat', key: 'chat' },
    { to: '/settings', key: 'settings' },
    { to: '/logs', key: 'logs' },
  ]

  return (
    <>
      <div id="top" />
      <div className="page">
        <div className="topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <img src="/logo.png" alt="" style={{ width: 48, height: 48, borderRadius: 4, display: 'block', flexShrink: 0 }} />
              <span style={{ lineHeight: 1 }}>{t('brand')}</span>
            </div>
            <nav className="subnav">
              {NAV_ITEMS.map(item => (
                <NavLink key={item.to} to={item.to} end={item.to === '/'}
                  className={({ isActive }) => isActive ? 'active' : ''}>
                  {t(item.key)}
                </NavLink>
              ))}
            </nav>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ display: 'flex', gap: 4, fontSize: 12 }}>
              <button className={`lang-btn ${lang === 'zh' ? 'lang-active' : ''}`} onClick={() => setLang('zh')}>中</button>
              <button className={`lang-btn ${lang === 'en' ? 'lang-active' : ''}`} onClick={() => setLang('en')}>En</button>
            </div>
            <div className="theme-switcher">
              <ThemePicker />
            </div>
          </div>
        </div>
        <Outlet />
        <div id="bottom" />
      </div>
      <div ref={floatActionsRef} style={floatStyle}
        className={`float-actions${floatActionsCollapsed ? ' float-actions-collapsed' : ''}${floatActionsCollapsed && floatActionsOnLeft ? ' float-actions-collapsed-left' : ''}${floatActionsDragging ? ' float-actions-dragging' : ''}`}
        onPointerDown={handleFloatPointerDown} onPointerMove={handleFloatPointerMove}
        onPointerUp={finishFloatPointer} onPointerCancel={finishFloatPointer} onPointerLeave={handleFloatPointerLeave}
        onClickCapture={event => {
          if (suppressClickRef.current) {
            event.preventDefault()
            event.stopPropagation()
            suppressClickRef.current = false
          }
        }}>
        {floatActionsCollapsed ? (
          <button className="float-btn float-actions-expand" onClick={toggleFloatActions}
            title={t('expand_float_actions')} aria-label={t('expand_float_actions')} aria-expanded="false">
            ≡
          </button>
        ) : (
          <>
            <button className="float-btn" onClick={toggleDark} title={dark ? 'Light' : 'Dark'}>
              {dark ? '☀' : '☾'}
            </button>
            <a className="float-btn float-btn-nav" href="#top" title={t('back_to_top')} aria-label={t('back_to_top')}>⬆︎</a>
            <a className="float-btn float-btn-nav" href="#bottom" title={t('back_to_bottom')} aria-label={t('back_to_bottom')}>⬇︎</a>
            <button className="float-btn float-actions-collapse" onClick={toggleFloatActions}
              title={t('collapse_float_actions')} aria-label={t('collapse_float_actions')} aria-expanded="true">
              −
            </button>
          </>
        )}
      </div>
    </>
  )
}
