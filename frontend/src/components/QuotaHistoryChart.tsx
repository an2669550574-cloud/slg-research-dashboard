import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { quotaApi } from '../lib/api'
import { useT } from '../i18n'
import { useLocalStorageState } from '../lib/hooks'

const WINDOW_OPTIONS = [7, 14, 30] as const

/**
 * 仪表盘"配额历史"：默认折叠为一行摘要,点开才展示曲线。
 *
 * 这是个**诊断/规划工具**(给 6/1 池重置评估预算用),不是日常一眼指标——
 * 大块占首屏会和"今日大事"/"排行榜"等真主视抢戏。折叠态保留 1 行情报密度
 * (累计/峰值/首记录日),点击 chevron 展开 7/14/30 切窗的小折线图。
 * 展开状态用 localStorage 记忆,跨刷新保持。
 *
 * 数据源 api_quota_daily（上线 2026-05-22 后前向记录）；上线前的日子永远是 0,
 * 不是 bug——折叠态副标 + 首记录日已告知用户。零本身不消耗 ST 配额。
 */
export function QuotaHistoryChart() {
  const t = useT()
  const [expanded, setExpanded] = useLocalStorageState<boolean>('slg.quotaHistoryExpanded', false)
  const [days, setDays] = useState<number>(14)

  // 折叠态默认拉 14 天(够算累计/峰值);展开后用户切窗时再走对应 query。
  // useQuery 缓存 + 5 min refetch 即可,不需要折叠时停拉(数据量极小)。
  const { data, isLoading } = useQuery({
    queryKey: ['quotaHistory', days],
    queryFn: () => quotaApi.history(days),
    refetchInterval: 5 * 60_000,
  })

  const firstRecorded = useMemo(() => {
    if (!data) return null
    const hit = data.points.find(p => p.count > 0)
    return hit?.date ?? null
  }, [data])

  const total = useMemo(() => (data?.points ?? []).reduce((s, p) => s + p.count, 0), [data])
  const peak = useMemo(() => (data?.points ?? []).reduce((m, p) => Math.max(m, p.count), 0), [data])

  // 折叠态摘要:把"自 X 起 + 累计 + 峰值"挤成一行,信息密度高但视觉权重低
  const summaryLine = (
    <>
      <span className="text-xs text-muted">
        {firstRecorded
          ? t.dashboard.quotaHistorySince(firstRecorded)
          : t.dashboard.quotaHistoryEmpty}
      </span>
      {data && (
        <>
          <span className="text-muted text-xs">·</span>
          <span className="font-data tabular-nums text-xs text-secondary">
            {t.dashboard.quotaHistoryTotal(total)}
          </span>
          <span className="text-muted text-xs">·</span>
          <span className="font-data tabular-nums text-xs text-secondary">
            {t.dashboard.quotaHistoryPeak(peak)}
          </span>
        </>
      )}
    </>
  )

  if (!expanded) {
    // 折叠态:整行可点,padding 减半,无 chart,无 hud 角标(降视觉权重)
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="w-full bg-surface border border-default rounded-xl px-5 py-2.5 flex items-center gap-2 text-left hover:border-strong transition-colors"
        aria-expanded="false"
      >
        <ChevronRight size={14} className="text-muted shrink-0" />
        <h2 className="text-sm font-semibold text-primary shrink-0">{t.dashboard.quotaHistoryTitle}</h2>
        <div className="flex flex-wrap items-baseline gap-2 min-w-0">{summaryLine}</div>
      </button>
    )
  }

  return (
    <div className="bg-surface border border-default rounded-xl p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-3">
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="flex items-baseline gap-2 text-left hover:opacity-80 transition-opacity"
          aria-expanded="true"
        >
          <ChevronDown size={14} className="text-muted shrink-0 translate-y-px" />
          <h2 className="text-sm font-semibold text-primary">{t.dashboard.quotaHistoryTitle}</h2>
          <span className="text-xs text-muted">
            {firstRecorded
              ? t.dashboard.quotaHistorySince(firstRecorded)
              : t.dashboard.quotaHistoryEmpty}
          </span>
        </button>
        <div className="flex gap-1 bg-elevated rounded-lg p-1">
          {WINDOW_OPTIONS.map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${days === d ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
            >
              {t.common.days(d)}
            </button>
          ))}
        </div>
      </div>

      {isLoading || !data ? (
        <div className="h-24 flex items-center justify-center text-muted text-xs">{t.common.loading}</div>
      ) : (
        <>
          <div className="flex items-baseline gap-4 mb-2 font-data tabular-nums text-xs text-secondary">
            <span>{t.dashboard.quotaHistoryTotal(total)}</span>
            <span>{t.dashboard.quotaHistoryPeak(peak)}</span>
          </div>
          <ResponsiveContainer width="100%" height={96}>
            <AreaChart data={data.points} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
              <defs>
                <linearGradient id="quotaHist" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#6366f1" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border-default))" />
              <XAxis
                dataKey="date"
                tick={{ fill: 'rgb(var(--text-muted))', fontSize: 10 }}
                tickFormatter={d => d.slice(5)}
                minTickGap={20}
              />
              <YAxis
                tick={{ fill: 'rgb(var(--text-muted))', fontSize: 10 }}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{ background: 'rgb(var(--bg-elevated))', border: '1px solid rgb(var(--border-default))', borderRadius: 8 }}
                labelStyle={{ color: 'rgb(var(--text-primary))' }}
                formatter={(v: any) => [v, t.dashboard.quotaHistoryCalls]}
              />
              <Area type="monotone" dataKey="count" stroke="#6366f1" strokeWidth={2}
                fill="url(#quotaHist)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  )
}
