import { NavLink, Outlet } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'
import { ThemePicker } from './ThemePicker'

export function Layout() {
  const { dark, toggleDark } = useTheme()
  const { lang, setLang, t } = useI18n()

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
      <div className="float-actions">
        <button className="float-btn" onClick={toggleDark} title={dark ? 'Light' : 'Dark'}>
          {dark ? '☀' : '☾'}
        </button>
        <a className="float-btn float-btn-nav" href="#top" title={t('back_to_top')} aria-label={t('back_to_top')}>⬆︎</a>
        <a className="float-btn float-btn-nav" href="#bottom" title={t('back_to_bottom')} aria-label={t('back_to_bottom')}>⬇︎</a>
      </div>
    </>
  )
}
