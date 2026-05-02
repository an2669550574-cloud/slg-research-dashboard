import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { LayoutDashboard, Trophy, Gamepad2, BookImage } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Rankings from './pages/Rankings'
import GameDetail from './pages/GameDetail'
import Materials from './pages/Materials'
import { cn } from './lib/utils'

const NAV = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘' },
  { to: '/rankings', icon: Trophy, label: '排行榜' },
  { to: '/materials', icon: BookImage, label: '素材库' },
]

function Sidebar() {
  return (
    <aside className="w-56 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Gamepad2 className="text-brand-500" size={22} />
          <span className="font-bold text-white text-base leading-tight">SLG 海外调研</span>
        </div>
        <p className="text-xs text-gray-500 mt-1">Strategy Games Intelligence</p>
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
      <div className="p-4 border-t border-gray-800">
        <p className="text-xs text-gray-600">Powered by Sensor Tower</p>
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
            <Route path="/game/:appId" element={<GameDetail />} />
            <Route path="/materials" element={<Materials />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
