import { useQueries, useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { gamesApi, quotaApi } from '../lib/api'
import { useT } from '../i18n'
import { formatNumber, formatRevenue } from '../lib/utils'
import { downloadCsv, type CsvColumn } from '../lib/csv'
import { Download as DownloadIcon, X, Plus } from 'lucide-react'
import { CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line, XAxis, YAxis, Legend } from 'recharts'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { useLocalStorageState } from '../lib/hooks'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import type { TrendPoint } from '../lib/types'

type Metric = 'revenue' | 'downloads' | 'rank'

const COLORS = ['#6366f1', '#10b981', '#f59e0b']

export default function Compare() {
  const t = useT()
  // 全部跨刷新持久化；country/platform 复用全站统一 key,与 Dashboard/Rankings 同步
  const [selected, setSelected] = useLocalStorageState<string[]>('slg.compareSelected', [])
  const [metric, setMetric] = useLocalStorageState<Metric>('slg.compareMetric', 'revenue')
  const [days, setDays] = useLocalStorageState<number>('slg.compareDays', 30)
  const [country, setCountry] = useLocalStorageState<Country>('slg.country', 'US')
  const [platform, setPlatform] = useLocalStorageState<Platform>('slg.platform', 'ios')

  // 选择器源换成 aggregateLeaderboard(slg_only=true)：
  //   1) 天然 SLG-only，与全站默认口径一致（之前用 gamesApi.list 会混入非 SLG）
  //   2) 按收入降序排好，下拉里头部就是最强竞品，省得用户自己找
  //   3) 零 ST 配额（本地聚合），跟 picker 不该掏配额的常识一致
  // 拿 30 天窗口确保过去 1 个月有数据的游戏都能出现；更久窗口意义不大
  const { data: pickerGames = [] } = useQuery({
    queryKey: ['aggregateLeaderboard', 30, 'slg-only-picker'],
    queryFn: () => gamesApi.aggregateLeaderboard({ days: 30, slg_only: true }),
  })

  const { data: quota } = useQuery({
    queryKey: ['quota'],
    queryFn: () => quotaApi.get(),
    refetchInterval: 60_000,
  })

  const metricsQueries = useQueries({
    queries: selected.map(appId => ({
      queryKey: ['metrics', appId, { days, country, platform }],
      queryFn: () => gamesApi.metrics(appId, { days, country, platform }),
      enabled: !!appId,
    })),
  })

  // 每个游戏 × 3 个 metric (rank/dl/rev)；切换 days 会产生新 cache key,
  // L2 snapshot 没命中时每个 metric 都消耗 1 次配额
  const worstCaseCalls = selected.length * 3

  const chartData = (() => {
    if (selected.length === 0) return []
    const series = selected.map((appId, idx) => {
      const m = metricsQueries[idx]?.data
      if (!m) return null
      const arr = metric === 'rank' ? m.rankings : (metric === 'revenue' ? m.revenue : m.downloads)
      const name = pickerGames.find(g => g.app_id === appId)?.name || appId
      return { name, points: arr || [] }
    }).filter((s): s is { name: string; points: TrendPoint[] } => s !== null)

    if (series.length === 0) return []

    const allDates = Array.from(new Set(series.flatMap(s => s.points.map(p => p.date)))).sort()
    return allDates.map(date => {
      const row: Record<string, string | number | null | undefined> = { date }
      for (const s of series) {
        const point = s.points.find(p => p.date === date)
        row[s.name] = metric === 'rank' ? point?.rank : point?.value
      }
      return row
    })
  })()

  const seriesNames = selected.map(appId => pickerGames.find(g => g.app_id === appId)?.name || appId)
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
  const availableGames = pickerGames.filter(g => !selected.includes(g.app_id))

  // CSV 列动态生成:第一列日期,其余每选中游戏一列。metric/days/市场都写进文件名,
  // 行内 unit 与 metric 一致(revenue=美元、downloads=次、rank=整数),不再二次注明。
  // chartData 已经按日期 pivot 好,直接喂给 downloadCsv 即可。
  type ChartRow = (typeof chartData)[number]
  const handleExport = () => {
    if (selected.length < 2 || chartData.length === 0) {
      toast.error(t.common.noExportData)
      return
    }
    const exportDate = new Date().toISOString().slice(0, 10)
    const columns: CsvColumn<ChartRow>[] = [
      { header: t.csv.date, get: r => r.date },
      ...seriesNames.map(name => ({
        header: name,
        get: (r: ChartRow) => r[name],
      })),
    ]
    downloadCsv(
      `compare-${metric}-${days}d-${country}-${platform}-${exportDate}.csv`,
      chartData,
      columns,
    )
    toast.success(t.common.exported(chartData.length))
  }

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="Diff Engine" title={t.compare.title} subtitle={t.compare.subtitle}>
        <button
          onClick={handleExport}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
        >
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
      </PageHeader>

      {/* 配额详细 Banner 只放仪表盘；本页用顶部全局警示条 + 控件旁的 quotaCostHint 已足够 */}

      <div className="bg-surface border border-default rounded-xl p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((appId, idx) => {
            const game = pickerGames.find(g => g.app_id === appId)
            const displayName = game?.name || appId
            return (
              <div key={appId} className="flex items-center gap-2 bg-elevated border border-default rounded-lg pl-2 pr-1 py-1">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[idx] }} />
                {game && <GameIcon src={game.icon_url} name={displayName} className="w-5 h-5 rounded" />}
                <span className="text-sm text-primary">{displayName}</span>
                <button
                  onClick={() => setSelected(selected.filter(a => a !== appId))}
                  className="p-1 text-muted hover:text-red-400"
                >
                  <X size={12} />
                </button>
              </div>
            )
          })}

          {canAddMore && (
            <select
              value=""
              onChange={e => { if (e.target.value) setSelected([...selected, e.target.value]) }}
              className="bg-elevated border border-default rounded-lg px-3 py-1.5 text-sm text-primary focus:outline-none focus:border-brand-500"
            >
              <option value="">{selected.length === 0 ? t.compare.selectGame : t.compare.addAnother}</option>
              {availableGames.map(g => (
                <option key={g.app_id} value={g.app_id}>{g.name || g.app_id}</option>
              ))}
            </select>
          )}
          {!canAddMore && (
            <span className="text-xs text-muted">{t.compare.selectMore}</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-default">
          <div className="flex items-center gap-2">
            <span className="text-xs text-secondary">{t.compare.metric}:</span>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {([
                ['revenue', t.compare.revenue],
                ['downloads', t.compare.downloads],
                ['rank', t.compare.rank],
              ] as const).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setMetric(key)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${metric === key ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-secondary">{t.compare.days}:</span>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {[
                [7, t.compare.days7],
                [30, t.compare.days30],
                [90, t.compare.days90],
                [365, t.compare.days365],
              ].map(([d, label]) => (
                <button
                  key={d as number}
                  onClick={() => setDays(d as number)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${days === d ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 市场选择：之前硬编码 US/iOS,日韩/安卓竞品全错位;改为可选,localStorage
              与 Dashboard/Rankings 共享 key 以保持跨页一致。rank 是单市场指标,本就
              必须有市场上下文;revenue/downloads 也按市场拉(后端会按 country+platform
              过滤 game_rankings 行)。 */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-secondary">{t.compare.market}:</span>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {PLATFORMS.map(p => (
                <button
                  key={p}
                  onClick={() => setPlatform(p)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${platform === p ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {platformLabel(p)}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {COUNTRIES.map(c => (
                <button
                  key={c}
                  onClick={() => setCountry(c)}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${country === c ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>

          {selected.length > 0 && (
            quota?.exhausted ? (
              <span className="text-xs text-yellow-400">{t.compare.quotaExhaustedHint}</span>
            ) : worstCaseCalls > 0 ? (
              <span className="text-xs text-muted">{t.compare.quotaCostHint(worstCaseCalls)}</span>
            ) : null
          )}
        </div>
      </div>

      <div className="bg-surface border border-default rounded-xl p-5">
        {selected.length < 2 ? (
          <div className="py-24 text-center text-muted text-sm">
            <Plus className="mx-auto mb-2 text-muted" size={24} />
            {t.compare.pickGames}
          </div>
        ) : metricsQueries.some(q => q.isError) ? (
          <QueryError compact onRetry={() => metricsQueries.forEach(q => q.refetch())} />
        ) : metricsQueries.some(q => q.isLoading) ? (
          <div className="h-80 flex items-center justify-center text-muted text-sm">{t.common.loading}</div>
        ) : (
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={chartData} margin={{ top: 16, right: 24, left: 8, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border-default))" />
              <XAxis dataKey="date" tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} minTickGap={32} />
              <YAxis tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} reversed={metric === 'rank'} tickFormatter={formatter} />
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
