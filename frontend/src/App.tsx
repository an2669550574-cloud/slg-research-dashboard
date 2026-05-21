import { lazy, Suspense, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { LayoutDashboard, Trophy, Gamepad2, BookImage, Sun, Moon, Languages, Settings, GitCompareArrows, Menu, X } from 'lucide-react'
import { cn } from './lib/utils'
import { useTheme } from './lib/theme'
import { useLocale, setLocale, useT } from './i18n'
import { GlobalQuotaAlert } from './components/GlobalQuotaAlert'

// 路由级拆包：每页（含 recharts 等重依赖）单独 chunk，首屏只下当前页。
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Rankings = lazy(() => import('./pages/Rankings'))
const GameDetail = lazy(() => import('./pages/GameDetail'))
const Materials = lazy(() => import('./pages/Materials'))
const GamesManage = lazy(() => import('./pages/GamesManage'))
const Compare = lazy(() => import('./pages/Compare'))

function PageFallback() {
  const t = useT()
  return <div className="p-6 text-sm text-muted">{t.common.loading}</div>
}

function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { theme, toggle } = useTheme()
  const locale = useLocale()
  const t = useT()

  const NAV = [
    { to: '/', icon: LayoutDashboard, label: t.nav.dashboard },
    { to: '/rankings', icon: Trophy, label: t.nav.rankings },
    { to: '/compare', icon: GitCompareArrows, label: t.nav.compare },
    { to: '/materials', icon: BookImage, label: t.nav.materials },
    { to: '/games', icon: Settings, label: t.nav.games },
  ]

  return (
    <aside
      className={cn(
        'fixed md:static inset-y-0 left-0 z-40 w-56 shrink-0 bg-surface border-r border-default flex flex-col',
        'transform transition-transform duration-200 md:translate-x-0',
        open ? 'translate-x-0' : '-translate-x-full',
      )}
    >
      <div className="px-5 py-5 border-b border-default flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Gamepad2 className="text-accent" size={22} />
            <span className="font-display font-extrabold text-primary text-lg leading-tight">{t.app.title}</span>
          </div>
          <p className="eyebrow text-muted mt-1.5">{t.app.subtitle}</p>
        </div>
        <button onClick={onNavigate} className="md:hidden text-muted hover:text-primary" aria-label="close menu">
          <X size={18} />
        </button>
      </div>
      <nav className="flex-1 p-3 space-y-1">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn('relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                isActive
                  ? 'bg-accent/12 text-accent font-medium before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-accent'
                  : 'text-secondary hover:text-primary hover:bg-elevated')
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-default space-y-2">
        <button
          onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs text-secondary hover:text-primary hover:bg-elevated transition-colors"
        >
          <Languages size={14} />
          {locale === 'zh' ? 'English' : '中文'}
        </button>
        <button
          onClick={toggle}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs text-secondary hover:text-primary hover:bg-elevated transition-colors"
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
          {theme === 'dark' ? t.app.themeLight : t.app.themeDark}
        </button>
        <p className="font-data text-[10px] text-muted/70 text-center tracking-wide">{t.app.powered}</p>
      </div>
    </aside>
  )
}

function MobileTopBar({ onMenu }: { onMenu: () => void }) {
  const t = useT()
  return (
    <header className="md:hidden flex items-center gap-3 h-14 px-4 border-b border-default bg-surface shrink-0">
      <button onClick={onMenu} className="text-secondary hover:text-primary" aria-label="open menu">
        <Menu size={20} />
      </button>
      <Gamepad2 className="text-accent" size={18} />
      <span className="font-display font-extrabold text-primary text-base">{t.app.title}</span>
    </header>
  )
}

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden">
        {menuOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/50 md:hidden"
            onClick={() => setMenuOpen(false)}
            aria-hidden
          />
        )}
        <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
        <div className="flex-1 flex flex-col min-w-0">
          <MobileTopBar onMenu={() => setMenuOpen(true)} />
          {/* 全站警示条：公司 ST 池处于 low/reserved 时出现；shrink-0 不被 main 滚动吞掉 */}
          <GlobalQuotaAlert />
          <main className="flex-1 overflow-y-auto">
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/rankings" element={<Rankings />} />
                <Route path="/compare" element={<Compare />} />
                <Route path="/materials" element={<Materials />} />
                <Route path="/games" element={<GamesManage />} />
                <Route path="/game/:appId" element={<GameDetail />} />
              </Routes>
            </Suspense>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}
