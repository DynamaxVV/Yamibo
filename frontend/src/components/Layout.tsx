import { NavLink, Outlet } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'

const NAV_ITEMS = [
  { to: '/', label: '控制台' },
  { to: '/jobs', label: '任务' },
  { to: '/threads', label: '贴子' },
  { to: '/series', label: '系列' },
  { to: '/review', label: '复核' },
  { to: '/exports', label: '导出' },
  { to: '/forums', label: '版块' },
  { to: '/logs', label: '日志' },
]

export function Layout() {
  const { theme, themes, setTheme } = useTheme()

  return (
    <>
      <div id="top" />
      <div className="page">
        <div className="topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <span className="brand">百合会归档助手</span>
            <nav className="subnav">
              {NAV_ITEMS.map(item => (
                <NavLink key={item.to} to={item.to} end={item.to === '/'}
                  className={({ isActive }) => isActive ? 'active' : ''}>
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="theme-switcher">
            <select value={theme.name} onChange={e => setTheme(e.target.value)}>
              {themes.map(t => (
                <option key={t.name} value={t.name}>{t.label}</option>
              ))}
            </select>
          </div>
        </div>
        <Outlet />
      </div>
      <a className="back-to-top" href="#top">顶部</a>
    </>
  )
}
