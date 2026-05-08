import { useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { gamesApi } from '../lib/api'
import { formatNumber, formatRevenue } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { TrendingUp, Download, DollarSign, Trophy, RefreshCw, Download as DownloadIcon } from 'lucide-react'
import { CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis } from 'recharts'
import { useNavigate } from 'react-router-dom'

function StatCard({ icon: Icon, label, value, sub, color }: any) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-gray-400 text-sm">{label}</span>
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon size={16} className="text-white" />
        </div>
      </div>
      <div className="text-2xl font-bold text-white">{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const t = useT()

  // 已追踪的游戏（来自 DB），用于"监控游戏数"卡片
  const { data: trackedGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  // 今日榜单（来自 Sensor Tower 真实/mock）
  const { data: rankings = [], isLoading, refetch } = useQuery({
    queryKey: ['rankings'],
    queryFn: () => gamesApi.rankings(),
  })

  const top5 = rankings.slice(0, 5)
  const totalDownloads = rankings.reduce((s: number, g: any) => s + (g.downloads || 0), 0)
  const totalRevenue = rankings.reduce((s: number, g: any) => s + (g.revenue || 0), 0)

  const revenueChartData = rankings.slice(0, 8).map((g: any) => ({
    name: g.name.length > 10 ? g.name.slice(0, 10) + '…' : g.name,
    revenue: Math.round(g.revenue / 1000),
    downloads: Math.round(g.downloads / 1000),
  }))

  const handleExport = () => {
    if (rankings.length === 0) { toast.error(t.common.noExportData); return }
    const date = new Date().toISOString().slice(0, 10)
    downloadCsv(`dashboard-${date}.csv`, rankings, [
      { header: t.csv.rank, get: (r: any) => r.rank },
      { header: t.csv.appId, get: (r: any) => r.app_id },
      { header: t.csv.gameName, get: (r: any) => r.name },
      { header: t.csv.publisher, get: (r: any) => r.publisher },
      { header: t.csv.revenueUsd, get: (r: any) => r.revenue },
      { header: t.csv.downloadsToday, get: (r: any) => r.downloads },
      { header: t.csv.date, get: (r: any) => r.date },
    ])
    toast.success(t.common.exported(rankings.length))
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">{t.dashboard.title}</h1>
          <p className="text-gray-500 text-sm mt-0.5">{t.dashboard.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
          >
            <DownloadIcon size={14} />
            {t.common.export}
          </button>
          <button
            onClick={() => { refetch().then(() => toast.success(t.common.refreshed)) }}
            className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
          >
            <RefreshCw size={14} />
            {t.common.refresh}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <StatCard icon={Trophy} label={t.dashboard.statGames} value={trackedGames.length} sub={t.dashboard.statCategory} color="bg-brand-600" />
        <StatCard icon={Download} label={t.dashboard.statDownloads} value={formatNumber(totalDownloads)} sub={t.dashboard.worldwide} color="bg-emerald-600" />
        <StatCard icon={DollarSign} label={t.dashboard.statRevenue} value={formatRevenue(totalRevenue)} sub={t.dashboard.worldwide} color="bg-purple-600" />
        <StatCard icon={TrendingUp} label={t.dashboard.statTopGame} value={top5[0]?.name || '—'} sub={`${t.dashboard.rankBadge} #${top5[0]?.rank || '—'}`} color="bg-yellow-600" />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">{t.dashboard.chartRevenue}</h2>
          {isLoading ? (
            <div className="h-48 flex items-center justify-center text-gray-600 text-sm">{t.common.loading}</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={revenueChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="name" tick={{ fill: '#6b7280', fontSize: 11 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: 'rgb(var(--bg-elevated))', border: '1px solid rgb(var(--border-default))', borderRadius: 8 }}
                  labelStyle={{ color: 'rgb(var(--text-primary))' }}
                  formatter={(v: any) => [`$${v}K`, t.dashboard.revenue]}
                />
                <Bar dataKey="revenue" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">{t.dashboard.chartDownloads}</h2>
          {isLoading ? (
            <div className="h-48 flex items-center justify-center text-gray-600 text-sm">{t.common.loading}</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={revenueChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="name" tick={{ fill: '#6b7280', fontSize: 11 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: 'rgb(var(--bg-elevated))', border: '1px solid rgb(var(--border-default))', borderRadius: 8 }}
                  labelStyle={{ color: 'rgb(var(--text-primary))' }}
                  formatter={(v: any) => [`${v}K`, t.dashboard.downloads]}
                />
                <Bar dataKey="downloads" fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl">
        <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300">{t.dashboard.todayRanking}</h2>
          <button onClick={() => navigate('/rankings')} className="text-xs text-brand-500 hover:text-brand-400">{t.common.viewAll}</button>
        </div>
        <div className="divide-y divide-gray-800">
          {isLoading
            ? Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-4 animate-pulse">
                  <div className="w-8 h-4 bg-gray-800 rounded" />
                  <div className="w-8 h-8 bg-gray-800 rounded-lg" />
                  <div className="flex-1 h-4 bg-gray-800 rounded" />
                  <div className="w-20 h-4 bg-gray-800 rounded" />
                </div>
              ))
            : rankings.slice(0, 8).map((g: any) => (
                <div
                  key={g.app_id}
                  className="px-5 py-3 flex items-center gap-4 hover:bg-gray-800/50 cursor-pointer transition-colors"
                  onClick={() => navigate(`/game/${g.app_id}`)}
                >
                  <span className={`w-7 text-center text-sm font-bold ${g.rank <= 3 ? 'text-yellow-400' : 'text-gray-500'}`}>
                    #{g.rank}
                  </span>
                  {g.icon_url
                    ? <img src={g.icon_url} alt={g.name} className="w-9 h-9 rounded-xl object-cover" />
                    : <div className="w-9 h-9 rounded-xl bg-gray-700 flex items-center justify-center text-gray-400 text-xs">?</div>
                  }
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-white truncate">{g.name}</div>
                    <div className="text-xs text-gray-500 truncate">{g.publisher}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-medium text-emerald-400">{formatRevenue(g.revenue)}</div>
                    <div className="text-xs text-gray-500">{formatNumber(g.downloads)} {t.dashboard.downloadsSuffix}</div>
                  </div>
                </div>
              ))
          }
        </div>
      </div>
    </div>
  )
}
