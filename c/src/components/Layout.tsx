import { useState, useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  BookOpen,
  LayoutDashboard,
  Activity,
  FileText,
  Library,
  Globe,
  MessageSquare,
  CheckSquare,
  Settings,
  BarChart3,
  PanelLeftClose,
  PanelLeft,
  Sun,
  Moon,
  Terminal,
  Menu,
  X,
} from 'lucide-react'
import { useTheme } from '../context/ThemeContext'
import { useI18n } from '../context/I18nContext'
import { cn } from '../lib/utils'

interface NavItem {
  to: string
  key: string
  labelZh: string
  labelEn: string
  icon: typeof BookOpen
  exact?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', key: 'dashboard', labelZh: '控制台', labelEn: 'DASHBOARD', icon: LayoutDashboard, exact: true },
  { to: '/threads', key: 'threads', labelZh: '帖子归档', labelEn: 'ARCHIVE', icon: BookOpen },
  { to: '/jobs', key: 'jobs', labelZh: '任务中心', labelEn: 'TASKS', icon: Activity },
  { to: '/series', key: 'series', labelZh: '作品系列', labelEn: 'SERIES', icon: Library },
  { to: '/forum', key: 'remoteForum', labelZh: '论坛漫游', labelEn: 'FORUM', icon: Globe },
  { to: '/rag', key: 'rag', labelZh: '知识库', labelEn: 'KNOWLEDGE', icon: FileText },
  { to: '/chat', key: 'chat', labelZh: '讨论助手', labelEn: 'ASSISTANT', icon: MessageSquare },
  { to: '/review', key: 'review', labelZh: '审核工作', labelEn: 'REVIEW', icon: CheckSquare },
  { to: '/forums', key: 'forums', labelZh: '归档统计', labelEn: 'ARCHIVE STATISTICS', icon: BarChart3 },
  { to: '/logs', key: 'logs', labelZh: '系统日志', labelEn: 'LOGS', icon: Terminal },
  { to: '/settings', key: 'settings', labelZh: '配置中心', labelEn: 'SETTINGS', icon: Settings },
]

export function Layout() {
  const location = useLocation()
  const { dark, toggleDark } = useTheme()
  const { lang, setLang, tx } = useI18n()
  const [mobileOpen, setMobileOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  const mobileNavigation = useRef<HTMLElement>(null)

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('yamibo.sidebar-collapsed') === 'true'
    } catch {
      return false
    }
  })

  const toggleSidebar = () => {
    setCollapsed(prev => {
      const next = !prev
      try {
        localStorage.setItem('yamibo.sidebar-collapsed', String(next))
      } catch {}
      return next
    })
  }

  // Keyboard shortcut: `[` toggles sidebar
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && mobileOpen) {
        setMobileOpen(false)
        menuButton.current?.focus()
      }
      if (e.key === 'Tab' && mobileOpen) {
        const controls = Array.from(mobileNavigation.current?.querySelectorAll<HTMLElement>('a, button') || []).filter(item => item.getClientRects().length > 0)
        if (controls.length) {
          const first = controls[0]
          const last = controls[controls.length - 1]
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
          if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
        }
      }
      if (
        e.key === '[' &&
        !mobileOpen &&
        !['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)
      ) {
        e.preventDefault()
        toggleSidebar()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [mobileOpen])

  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!mobileOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    mobileNavigation.current?.querySelector<HTMLElement>('a')?.focus()
    return () => { document.body.style.overflow = previous }
  }, [mobileOpen])

  return (
    <div className="min-h-screen flex bg-background text-foreground selection:bg-yamibo-burgundy selection:text-white transition-colors duration-200">
      <button
        type="button"
        ref={menuButton}
        className="fixed left-3 top-3 z-30 flex h-11 w-11 items-center justify-center rounded border border-border bg-card text-foreground md:hidden"
        onClick={() => setMobileOpen(true)}
        aria-label={tx('打开导航菜单', 'Open navigation menu')}
        aria-expanded={mobileOpen}
        aria-controls="site-navigation"
      ><Menu className="h-5 w-5" /></button>
      {mobileOpen && <button type="button" className="fixed inset-0 z-40 bg-black/55 md:hidden" aria-label={tx('关闭导航菜单', 'Close navigation menu')} onClick={() => setMobileOpen(false)} />}
        {/* The sidebar shares the archive's paper, ink and rule system. */}
      <aside
        ref={mobileNavigation}
        id="site-navigation"
        aria-label={tx('主导航', 'Main navigation')}
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-sidebar border-r border-sidebar-border',
          'transition-transform duration-200 select-none md:z-40 md:translate-x-0 md:transition-[width]',
          mobileOpen ? 'translate-x-0 visible' : '-translate-x-full invisible md:visible',
          collapsed ? 'md:w-16' : 'md:w-52'
        )}
      >
        <div className={cn('p-4 border-b border-sidebar-border flex items-start justify-between', collapsed && !mobileOpen && 'md:px-2 md:justify-center')}>
          <NavLink to="/" aria-label={tx('YAMIBO ARCHIVE 首页', 'Yamibo Archive home')} className="flex flex-col group focus:outline-none">
            <span className={cn('font-display text-2xl font-bold tracking-tight text-primary leading-none', collapsed && !mobileOpen && 'md:hidden')}>
              YAMIBO
            </span>
            <span className={cn('font-display text-[11px] tracking-[0.28em] text-foreground uppercase mt-1', collapsed && !mobileOpen && 'md:hidden')}>
              ARCHIVE
            </span>
            {collapsed && !mobileOpen && <span className="hidden md:block font-display text-2xl font-bold text-primary leading-none">Y</span>}
          </NavLink>

          {mobileOpen && <button type="button" onClick={() => setMobileOpen(false)} className="p-2 md:hidden" aria-label={tx('关闭导航菜单', 'Close navigation menu')}><X className="h-5 w-5" /></button>}
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1 custom-scrollbar">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.exact}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-3 px-3 py-2 rounded-none text-[13px] font-display font-semibold tracking-[0.02em] transition-colors press-feedback',
                    isActive
                      ? 'bg-yamibo-burgundy !text-white font-bold shadow-xs'
                      : 'text-foreground/80 hover:text-foreground hover:bg-muted/50 font-medium',
                    collapsed && 'md:justify-center md:px-0'
                  )
                }
                title={collapsed ? (lang === 'en' ? item.labelEn : item.labelZh) : undefined}
              >
                {({ isActive }) => (
                  <>
                    <Icon
                      className={cn(
                        'w-4 h-4 shrink-0 transition-transform duration-200',
                        isActive ? 'text-white' : 'text-muted-foreground'
                      )}
                    />
                    {(!collapsed || mobileOpen) && (
                      <span className={cn('truncate flex-1', isActive && '!text-white')}>
                        {lang === 'en' ? item.labelEn : item.labelZh}
                      </span>
                    )}

                  </>
                )}
              </NavLink>
            )
          })}
        </div>

        <div className={cn('p-3 border-t border-sidebar-border space-y-3', collapsed && !mobileOpen && 'md:p-2 md:space-y-2')}>
          {/* Quick Lang & Theme buttons */}
          {collapsed && !mobileOpen ? (
            <div className="hidden md:flex flex-col items-center gap-1 border-t border-sidebar-border/40 pt-2">
              <button type="button" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}
                className="w-8 h-8 rounded border border-border/50 bg-muted/60 text-[11px] font-mono"
                title={lang === 'zh' ? 'Switch to English' : '切换为中文'} aria-label={lang === 'zh' ? 'Switch to English' : '切换为中文'}>
                {lang === 'zh' ? '中' : 'EN'}
              </button>
              <button type="button" onClick={toggleDark}
                className="flex w-8 h-8 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                title={dark ? tx('浅色纸墨', 'Light paper and ink') : tx('深茶黑夜', 'Dark tea at night')} aria-label={tx('切换主题', 'Toggle theme')}>
                {dark ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-stone-600" />}
              </button>
            </div>
          ) : <div className="flex items-center justify-between pt-1 border-t border-sidebar-border/40 text-xs">
            <div className="flex items-center bg-muted/60 p-0.5 rounded border border-border/50 text-[11px] font-mono">
              <button
                onClick={() => setLang('zh')}
                className={cn(
                  'px-1.5 py-0.5 rounded transition-colors',
                  lang === 'zh' ? 'bg-card text-foreground font-semibold shadow-2xs' : 'text-muted-foreground'
                )}
              >
                中
              </button>
              <button
                onClick={() => setLang('en')}
                className={cn(
                  'px-1.5 py-0.5 rounded transition-colors',
                  lang === 'en' ? 'bg-card text-foreground font-semibold shadow-2xs' : 'text-muted-foreground'
                )}
              >
                EN
              </button>
            </div>

            <button
              onClick={toggleDark}
              className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title={dark ? tx('浅色纸墨', 'Light paper and ink') : tx('深茶黑夜', 'Dark tea at night')}
              aria-label={tx('切换主题', 'Toggle theme')}
            >
              {dark ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-stone-600" />}
            </button>
          </div>}

          <div className={cn('hidden md:flex border-t border-sidebar-border/40 pt-2', collapsed ? 'justify-center' : 'justify-end')}>
            <button
              type="button"
              onClick={toggleSidebar}
              className="flex h-8 w-8 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              title={collapsed ? tx('展开侧边栏 ([)', 'Expand sidebar ([)') : tx('收起侧边栏 ([)', 'Collapse sidebar ([)')}
              aria-label={collapsed ? tx('展开侧边栏', 'Expand sidebar') : tx('收起侧边栏', 'Collapse sidebar')}
              aria-expanded={!collapsed}
              aria-controls="site-navigation"
            >
              {collapsed ? <PanelLeft className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </aside>

      {/* ─── Main Content Canvas (No Horizontal Scrollbars) ─── */}
      <div
        className={cn(
          'flex-1 flex flex-col min-w-0 max-w-full',
          'transition-[margin-left] duration-200',
          collapsed ? 'md:ml-16' : 'md:ml-52'
        )}
      >
        <main className="flex-1 min-w-0 px-4 pb-6 pt-20 md:px-6 md:py-5 max-w-[1500px] w-full mx-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
