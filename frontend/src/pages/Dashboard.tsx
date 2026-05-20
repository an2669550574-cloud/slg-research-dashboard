import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { gamesApi, quotaApi } from '../lib/api'
import { formatNumber, formatRevenue } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { TrendingUp, Download, DollarSign, Trophy, RefreshCw, Download as DownloadIcon, Loader2 } from 'lucide-react'
import { CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis } from 'recharts'
import { useNavigate } from 'react-router-dom'
import { QuotaBanner } from '../components/QuotaBanner'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { useLocalStorageState } from '../lib/hooks'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'

function StatCard({ icon: Icon, label, value, sub, color }: any) {
  return (
    <div className="group hud relative bg-surface/80 border border-default rounded-xl p-5 transition-colors hover:border-strong">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-secondary">{label}</span>
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon size={16} className="text-white" />
        </div>
      </div>
      <div className="font-display text-[30px] leading-none font-extrabold text-primary tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted mt-2">{sub}</div>}
    </div>
  )
}

const AGG_DAYS_OPTIONS = [7, 30, 90] as const

// 仪表盘有两套口径，必须给用户切换：
//   today —— 单 (country,platform) 当日快照（ST 来源、可 force-refresh）
//   total —— 每款 SLG 在窗口内跨全部已监测市场合计（本地 game_rankings 聚合,
//            零 ST 配额；与详情页头部"已监测市场合计"同口径，数字直接对账）
// 之前只有 today，导致：仪表盘外显 ≠ 详情页头部那行，安卓尤其明显（详情页
// 30 天累计自然比仪表盘当日大一个数量级）。加 view 切换 + 周期标签后两端对齐。
type View = 'today' | 'total'

export default function Dashboard() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  const [cooldownLeft, setCooldownLeft] = useState(0)
  const cooling = cooldownLeft > 0
  const [country, setCountry] = useLocalStorageState<Country>('slg.country', 'US')
  const [platform, setPlatform] = useLocalStorageState<Platform>('slg.platform', 'ios')
  const [view, setView] = useLocalStorageState<View>('slg.dashView', 'today')
  const [aggDays, setAggDays] = useLocalStorageState<number>('slg.dashAggDays', 30)
  const isTotal = view === 'total'

  const { data: trackedGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  // 今日快照（单市场）：queryKey 必须跟 Rankings.tsx 形态一致 ['rankings',country,
  // platform]，否则同一份数据会被前端当成两个 query 各自 fetch。
  const todayQ = useQuery({
    queryKey: ['rankings', country, platform],
    queryFn: () => gamesApi.rankings(country, platform),
    placeholderData: keepPreviousData,
    enabled: !isTotal,
  })

  // 合计·区间：跨该 app 全部已监测市场窗口内合计，纯本地聚合（零 ST 配额）。
  const totalQ = useQuery({
    queryKey: ['aggregateLeaderboard', aggDays],
    queryFn: () => gamesApi.aggregateLeaderboard({ days: aggDays }),
    enabled: isTotal,
  })

  const { data: quota } = useQuery({
    queryKey: ['quota'],
    queryFn: () => quotaApi.get(),
    refetchInterval: 60_000,
  })

  // 刷新只对 today 视图有意义（合计是本地聚合，没有 ST 调用可刷）
  const REFRESH_COOLDOWN_SEC = 30
  const refreshMut = useMutation({
    mutationFn: () => gamesApi.refreshRankings(country, platform),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rankings', country, platform] })
      qc.invalidateQueries({ queryKey: ['quota'] })
      toast.success(t.common.refreshed)
      setCooldownLeft(REFRESH_COOLDOWN_SEC)
    },
  })
  useEffect(() => {
    if (cooldownLeft <= 0) return
    const id = setTimeout(() => setCooldownLeft(n => n - 1), 1000)
    return () => clearTimeout(id)
  }, [cooldownLeft])

  const isLoading = isTotal ? totalQ.isLoading : todayQ.isLoading
  const isError = isTotal ? totalQ.isError : todayQ.isError
  const refetch = isTotal ? totalQ.refetch : todayQ.refetch

  // 仪表盘是竞品速览：非 SLG 是纯噪声，会污染汇总/图表/Top 榜，故始终只看 SLG
  // （不给开关——「全部策略」属于排行榜页的探查动作）。今日榜需手动过滤;
  // 合计接口默认 slg_only=true，已在后端滤过。
  const board: Array<{
    app_id: string
    name: string | null
    publisher: string | null
    icon_url: string | null
    downloads: number | null
    revenue: number | null
    rank?: number | null
  }> = isTotal
    ? (totalQ.data ?? [])
    : (todayQ.data ?? []).filter(g => g.is_slg)

  const totalDownloads = board.reduce((s, g) => s + (g.downloads || 0), 0)
  const totalRevenue = board.reduce((s, g) => s + (g.revenue || 0), 0)
  const topGame = board[0]
  const topGameSub = isTotal
    ? t.dashboard.statTopGameTotalSub
    : `${t.dashboard.rankBadge} #${topGame?.rank ?? '—'}`
  const statSub = isTotal ? t.dashboard.periodSub(aggDays) : t.dashboard.worldwide

  // 两个图各按自己的指标排 Top 8；[...board] 复制后再 sort —— 别原地排序 React
  // Query 缓存数组。
  const chartLabel = (g: { name: string | null; app_id: string }) => {
    const s = g.name ?? g.app_id
    return s.length > 10 ? s.slice(0, 10) + '…' : s
  }
  const revenueChartData = [...board]
    .sort((a, b) => (b.revenue ?? 0) - (a.revenue ?? 0))
    .slice(0, 8)
    .map(g => ({ name: chartLabel(g), revenue: Math.round((g.revenue ?? 0) / 1000) }))
  const downloadsChartData = [...board]
    .sort((a, b) => (b.downloads ?? 0) - (a.downloads ?? 0))
    .slice(0, 8)
    .map(g => ({ name: chartLabel(g), downloads: Math.round((g.downloads ?? 0) / 1000) }))

  const handleExport = () => {
    if (board.length === 0) { toast.error(t.common.noExportData); return }
    const date = new Date().toISOString().slice(0, 10)
    if (isTotal) {
      downloadCsv(`dashboard-aggregate-${aggDays}d-${date}.csv`, board, [
        { header: t.csv.appId, get: r => r.app_id },
        { header: t.csv.gameName, get: r => r.name ?? '' },
        { header: t.csv.publisher, get: r => r.publisher ?? '' },
        { header: t.csv.revenueUsd, get: r => r.revenue ?? 0 },
        { header: t.csv.downloadsToday, get: r => r.downloads ?? 0 },
      ])
    } else {
      downloadCsv(`dashboard-${country}-${platform}-${date}.csv`, board, [
        { header: t.csv.rank, get: r => r.rank ?? '' },
        { header: t.csv.appId, get: r => r.app_id },
        { header: t.csv.gameName, get: r => r.name ?? '' },
        { header: t.csv.publisher, get: r => r.publisher ?? '' },
        { header: t.csv.revenueUsd, get: r => r.revenue ?? 0 },
        { header: t.csv.downloadsToday, get: r => r.downloads ?? 0 },
      ])
    }
    toast.success(t.common.exported(board.length))
  }

  // 排行榜区块标题：今日 vs 周期合计
  const boardTitle = isTotal ? t.dashboard.aggBoardTitle(aggDays) : t.dashboard.todayRanking
  const revenueChartTitle = isTotal ? t.dashboard.chartRevenueAgg(aggDays) : t.dashboard.chartRevenue
  const downloadsChartTitle = isTotal ? t.dashboard.chartDownloadsAgg(aggDays) : t.dashboard.chartDownloads

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-6">
      <PageHeader eyebrow="Overview" title={t.dashboard.title} subtitle={t.dashboard.subtitle}>
        <button
          onClick={handleExport}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
        >
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
        {!isTotal && (
          <button
            onClick={() => refreshMut.mutate()}
            disabled={refreshMut.isPending || cooling}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed glow-accent transition-all"
          >
            {refreshMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {cooling ? t.common.refreshCooldown(cooldownLeft) : t.common.refresh}
          </button>
        )}
      </PageHeader>

      <QuotaBanner quota={quota} />

      {/* 视图切换 + 当前视图的副选项 */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 bg-elevated rounded-lg p-1">
          {(['today', 'total'] as const).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
            >
              {v === 'today' ? t.dashboard.viewToday : t.dashboard.viewTotal}
            </button>
          ))}
        </div>
        {isTotal ? (
          <div className="flex gap-1 bg-elevated rounded-lg p-1">
            {AGG_DAYS_OPTIONS.map(d => (
              <button
                key={d}
                onClick={() => setAggDays(d)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${aggDays === d ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
              >
                {t.common.days(d)}
              </button>
            ))}
          </div>
        ) : (
          <>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {PLATFORMS.map(p => (
                <button
                  key={p}
                  onClick={() => setPlatform(p)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${platform === p ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
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
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${country === c ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {c}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Trophy} label={t.dashboard.statGames} value={trackedGames.length} sub={t.dashboard.statCategory} color="bg-brand-600" />
        <StatCard icon={Download} label={isTotal ? t.dashboard.statDownloadsTotal(aggDays) : t.dashboard.statDownloads} value={formatNumber(totalDownloads)} sub={statSub} color="bg-emerald-600" />
        <StatCard icon={DollarSign} label={isTotal ? t.dashboard.statRevenueTotal(aggDays) : t.dashboard.statRevenue} value={formatRevenue(totalRevenue)} sub={statSub} color="bg-purple-600" />
        <StatCard icon={TrendingUp} label={t.dashboard.statTopGame} value={topGame?.name || '—'} sub={topGameSub} color="bg-yellow-600" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-surface border border-default rounded-xl p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">{revenueChartTitle}</h2>
          {isError ? (
            <QueryError compact onRetry={() => refetch()} />
          ) : isLoading ? (
            <div className="h-48 flex items-center justify-center text-muted text-sm">{t.common.loading}</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={revenueChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border-default))" />
                <XAxis dataKey="name" tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} />
                <YAxis tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} />
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

        <div className="bg-surface border border-default rounded-xl p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">{downloadsChartTitle}</h2>
          {isError ? (
            <QueryError compact onRetry={() => refetch()} />
          ) : isLoading ? (
            <div className="h-48 flex items-center justify-center text-muted text-sm">{t.common.loading}</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={downloadsChartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border-default))" />
                <XAxis dataKey="name" tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} />
                <YAxis tick={{ fill: 'rgb(var(--text-muted))', fontSize: 11 }} />
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

      <div className="bg-surface border border-default rounded-xl">
        <div className="px-5 py-4 border-b border-default flex items-center justify-between">
          <h2 className="text-sm font-semibold text-primary">{boardTitle}</h2>
          {!isTotal && (
            <button onClick={() => navigate('/rankings')} className="text-xs text-brand-500 hover:text-brand-400">{t.common.viewAll}</button>
          )}
        </div>
        <div className="divide-y divide-default">
          {isError
            ? <QueryError compact onRetry={() => refetch()} />
            : isLoading
            ? Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-4 animate-pulse">
                  <div className="w-8 h-4 bg-elevated rounded" />
                  <div className="w-8 h-8 bg-elevated rounded-lg" />
                  <div className="flex-1 h-4 bg-elevated rounded" />
                  <div className="w-20 h-4 bg-elevated rounded" />
                </div>
              ))
            : board.slice(0, 8).map((g, i) => (
                <div
                  key={g.app_id}
                  className="px-5 py-3 flex items-center gap-4 hover:bg-elevated/50 cursor-pointer transition-colors"
                  onClick={() => navigate(`/game/${g.app_id}`)}
                >
                  {isTotal ? (
                    <span className={`w-7 text-center text-sm font-bold ${i < 3 ? 'text-yellow-400' : 'text-muted'}`}>
                      #{i + 1}
                    </span>
                  ) : (
                    <span className={`w-7 text-center text-sm font-bold ${g.rank != null && g.rank <= 3 ? 'text-yellow-400' : 'text-muted'}`}>
                      #{g.rank ?? '—'}
                    </span>
                  )}
                  <GameIcon src={g.icon_url} name={g.name ?? g.app_id} className="w-9 h-9 rounded-xl" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-primary truncate">{g.name}</div>
                    <div className="text-xs text-muted truncate">{g.publisher}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-medium text-emerald-400">{g.revenue == null ? <span className="text-muted">—</span> : formatRevenue(g.revenue)}</div>
                    <div className="text-xs text-muted">{g.downloads == null ? '—' : `${formatNumber(g.downloads)} ${t.dashboard.downloadsSuffix}`}</div>
                  </div>
                </div>
              ))
          }
        </div>
      </div>
    </div>
  )
}
