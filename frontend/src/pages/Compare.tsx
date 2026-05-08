import { useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { gamesApi } from '../lib/api'
import { useT } from '../i18n'
import { formatNumber, formatRevenue } from '../lib/utils'
import { X, Plus } from 'lucide-react'
import { CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line, XAxis, YAxis, Legend } from 'recharts'

type Metric = 'revenue' | 'downloads' | 'rank'

const COLORS = ['#6366f1', '#10b981', '#f59e0b']

export default function Compare() {
  const t = useT()
  const [selected, setSelected] = useState<string[]>([])
  const [metric, setMetric] = useState<Metric>('revenue')
  const [days, setDays] = useState(30)

  const { data: games = [] } = useQuery({
    queryKey: ['games', 'compare-options'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  const metricsQueries = useQueries({
    queries: selected.map(appId => ({
      queryKey: ['metrics', appId, { days }],
      queryFn: () => gamesApi.metrics(appId, { days }),
      enabled: !!appId,
    })),
  })

  // 把多款游戏的同一指标 join 成 [{date, [name1]: v1, [name2]: v2, ...}]
  const chartData = (() => {
    if (selected.length === 0) return []
    const series = selected.map((appId, idx) => {
      const m = metricsQueries[idx]?.data
      if (!m) return null
      const arr = metric === 'rank' ? m.rankings : (metric === 'revenue' ? m.revenue : m.downloads)
      const name = games.find((g: any) => g.app_id === appId)?.name || appId
      return { name, points: arr || [] }
    }).filter((s): s is { name: string; points: any[] } => s !== null)

    if (series.length === 0) return []

    const allDates = Array.from(new Set(series.flatMap(s => s.points.map(p => p.date)))).sort()
    return allDates.map(date => {
      const row: Record<string, any> = { date }
      for (const s of series) {
        const point = s.points.find(p => p.date === date)
        row[s.name] = metric === 'rank' ? point?.rank : point?.value
      }
      return row
    })
  })()

  const seriesNames = selected.map(appId => games.find((g: any) => g.app_id === appId)?.name || appId)
  const formatter = (v: any) => {
    if (v === null || v === undefined) return '—'
    if (metric === 'revenue') return formatRevenue(v)
    if (metric === 'downloads') return formatNumber(v)
    return `#${v}`
  }

  const tooltipStyle = {
    contentStyle: { background: 'rgb(var(--bg-elevated))', border: '1px solid rgb(var(--border-default))', borderRadius: 8 },
    labelStyle: { color: 'rgb(var(--text-primary))' },
  }

  const canAddMore = selected.length < 3
  const availableGames = games.filter((g: any) => !selected.includes(g.app_id))

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-xl font-bold text-white">{t.compare.title}</h1>
        <p className="text-gray-500 text-sm mt-0.5">{t.compare.subtitle}</p>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((appId, idx) => {
            const game = games.find((g: any) => g.app_id === appId)
            return (
              <div key={appId} className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg pl-2 pr-1 py-1">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[idx] }} />
                {game?.icon_url && <img src={game.icon_url} alt="" className="w-5 h-5 rounded" />}
                <span className="text-sm text-white">{game?.name || appId}</span>
                <button
                  onClick={() => setSelected(s => s.filter(a => a !== appId))}
                  className="p-1 text-gray-500 hover:text-red-400"
                >
                  <X size={12} />
                </button>
              </div>
            )
          })}

          {canAddMore && (
            <select
              value=""
              onChange={e => { if (e.target.value) setSelected(s => [...s, e.target.value]) }}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-brand-500"
            >
              <option value="">{selected.length === 0 ? t.compare.selectGame : t.compare.addAnother}</option>
              {availableGames.map((g: any) => (
                <option key={g.app_id} value={g.app_id}>{g.name}</option>
              ))}
            </select>
          )}
          {!canAddMore && (
            <span className="text-xs text-gray-500">{t.compare.selectMore}</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-gray-800">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{t.compare.metric}:</span>
            <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
              {([
                ['revenue', t.compare.revenue],
                ['downloads', t.compare.downloads],
                ['rank', t.compare.rank],
              ] as const).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setMetric(key)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${metric === key ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{t.compare.days}:</span>
            <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
              {[
                [7, t.compare.days7],
                [30, t.compare.days30],
                [90, t.compare.days90],
              ].map(([d, label]) => (
                <button
                  key={d as number}
                  onClick={() => setDays(d as number)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${days === d ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        {selected.length < 2 ? (
          <div className="py-24 text-center text-gray-600 text-sm">
            <Plus className="mx-auto mb-2 text-gray-700" size={24} />
            {t.compare.pickGames}
          </div>
        ) : metricsQueries.some(q => q.isLoading) ? (
          <div className="h-80 flex items-center justify-center text-gray-600 text-sm">{t.common.loading}</div>
        ) : (
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={chartData} margin={{ top: 16, right: 24, left: 8, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 11 }} minTickGap={32} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} reversed={metric === 'rank'} tickFormatter={formatter} />
              <Tooltip {...tooltipStyle} formatter={formatter} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {seriesNames.map((name, idx) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  stroke={COLORS[idx]}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
