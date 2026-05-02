import { useQuery } from '@tanstack/react-query'
import { gamesApi } from '../lib/api'
import { formatNumber, formatRevenue } from '../lib/utils'
import { TrendingUp, Download, DollarSign, Trophy, RefreshCw } from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts'
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

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">仪表盘</h1>
          <p className="text-gray-500 text-sm mt-0.5">海外 SLG 市场实时概览</p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
        >
          <RefreshCw size={14} />
          刷新数据
        </button>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <StatCard icon={Trophy} label="监控游戏数" value={rankings.length} sub="SLG 品类" color="bg-brand-600" />
        <StatCard icon={Download} label="今日总下载量" value={formatNumber(totalDownloads)} sub="全球 WW" color="bg-emerald-600" />
        <StatCard icon={DollarSign} label="今日总收入估算" value={formatRevenue(totalRevenue)} sub="全球 WW" color="bg-purple-600" />
        <StatCard icon={TrendingUp} label="TOP 1 游戏" value={top5[0]?.name || '—'} sub={`排名 #${top5[0]?.rank || '—'}`} color="bg-yellow-600" />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">收入 Top 8（千美元）</h2>
          {isLoading ? (
            <div className="h-48 flex items-center justify-center text-gray-600 text-sm">加载中...</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={revenueChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="name" tick={{ fill: '#6b7280', fontSize: 11 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
                  labelStyle={{ color: '#f9fafb' }}
                  formatter={(v: any) => [`$${v}K`, '收入']}
                />
                <Bar dataKey="revenue" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">下载量 Top 8（千次）</h2>
          {isLoading ? (
            <div className="h-48 flex items-center justify-center text-gray-600 text-sm">加载中...</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={revenueChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="name" tick={{ fill: '#6b7280', fontSize: 11 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
                  labelStyle={{ color: '#f9fafb' }}
                  formatter={(v: any) => [`${v}K`, '下载量']}
                />
                <Bar dataKey="downloads" fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl">
        <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300">今日排行榜</h2>
          <button onClick={() => navigate('/rankings')} className="text-xs text-brand-500 hover:text-brand-400">查看全部 →</button>
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
                    <div className="text-xs text-gray-500">{formatNumber(g.downloads)} 下载</div>
                  </div>
                </div>
              ))
          }
        </div>
      </div>
    </div>
  )
}
