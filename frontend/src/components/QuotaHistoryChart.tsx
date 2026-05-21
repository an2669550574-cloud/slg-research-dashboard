import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { quotaApi } from '../lib/api'
import { useT } from '../i18n'

const WINDOW_OPTIONS = [7, 14, 30] as const

/**
 * 仪表盘"配额历史曲线"：近 7/14/30 个 UTC 日本项目每日 ST 调用次数。
 * 数据源 api_quota_daily（上线 2026-05-21 后前向记录）；上线前的日子永远是 0,
 * 不是 bug——本注释 + 标题副标已告知用户「自 <首记录日> 起」。
 *
 * 信号读法：斜率 = 本项目每日烧得快不快；峰值通常对应同步任务密集时段
 * （SYNC_RANKING_COMBOS 各组合 + 手动 refresh）。零本身不消耗配额（纯本地读）。
 */
export function QuotaHistoryChart() {
  const t = useT()
  const [days, setDays] = useState<number>(14)

  const { data, isLoading } = useQuery({
    queryKey: ['quotaHistory', days],
    queryFn: () => quotaApi.history(days),
    // 5 min — 仪表盘可见时偶尔刷一下;比 quota 状态 1min 长是因为历史是低频信号
    refetchInterval: 5 * 60_000,
  })

  // 求"首记录日":第一个 count>0 的日期。早于该日的零点条带不该被误读为
  // "项目当天没烧任何配额",其实是 daily 表那时还没上线。
  const firstRecorded = useMemo(() => {
    if (!data) return null
    const hit = data.points.find(p => p.count > 0)
    return hit?.date ?? null
  }, [data])

  const total = useMemo(() => (data?.points ?? []).reduce((s, p) => s + p.count, 0), [data])
  const peak = useMemo(() => (data?.points ?? []).reduce((m, p) => Math.max(m, p.count), 0), [data])

  return (
    <div className="hud bg-surface border border-default rounded-xl p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold text-primary">{t.dashboard.quotaHistoryTitle}</h2>
          <span className="text-xs text-muted">
            {firstRecorded
              ? t.dashboard.quotaHistorySince(firstRecorded)
              : t.dashboard.quotaHistoryEmpty}
          </span>
        </div>
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
        <div className="h-32 flex items-center justify-center text-muted text-xs">{t.common.loading}</div>
      ) : (
        <>
          <div className="flex items-baseline gap-4 mb-2 font-data tabular-nums text-xs text-secondary">
            <span>{t.dashboard.quotaHistoryTotal(total)}</span>
            <span>{t.dashboard.quotaHistoryPeak(peak)}</span>
          </div>
          <ResponsiveContainer width="100%" height={120}>
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
                tickFormatter={d => d.slice(5)}  // "MM-DD"
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
