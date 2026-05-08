import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { LayoutDashboard, Trophy, Gamepad2, BookImage, Sun, Moon, Languages, Settings, GitCompareArrows } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Rankings from './pages/Rankings'
import GameDetail from './pages/GameDetail'
import Materials from './pages/Materials'
import GamesManage from './pages/GamesManage'
import Compare from './pages/Compare'
import { cn } from './lib/utils'
import { useTheme } from './lib/theme'
import { useLocale, setLocale, useT } from './i18n'

function Sidebar() {
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
    <aside className="w-56 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Gamepad2 className="text-brand-500" size={22} />
          <span className="font-bold text-white text-base leading-tight">{t.app.title}</span>
        </div>
        <p className="text-xs text-gray-500 mt-1">{t.app.subtitle}</p>
      </div>
      <nav className="flex-1 p-3 space-y-1">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn('flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                isActive
                  ? 'bg-brand-600 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800')
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-gray-800 space-y-2">
        <button
          onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
        >
          <Languages size={14} />
          {locale === 'zh' ? 'English' : '中文'}
        </button>
        <button
          onClick={toggle}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
          {theme === 'dark' ? t.app.themeLight : t.app.themeDark}
        </button>
        <p className="text-xs text-gray-600 text-center">{t.app.powered}</p>
      </div>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/rankings" element={<Rankings />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/games" element={<GamesManage />} />
            <Route path="/game/:appId" element={<GameDetail />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
