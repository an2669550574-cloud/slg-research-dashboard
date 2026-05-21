import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, TrendingDown, Sparkles, DollarSign, Activity } from 'lucide-react'
import { movementsApi } from '../lib/api'
import { useT } from '../i18n'
import { formatRevenue } from '../lib/utils'
import { GameIcon } from './GameIcon'
import type { MovementEvent, MovementKind } from '../lib/types'

/**
 * 今日大事：从 `/api/movements/` 拉取并展示今日 SLG 竞品异动。
 *
 * 设计：紧凑横向卡片列表，按重要性已在后端排好序。每张卡片含图标、游戏名、
 * 市场标签、变化描述；点击跳详情。空态/冷库态分别有提示。零 ST 配额。
 */
const KIND_META: Record<MovementKind, { icon: typeof TrendingUp; cls: string; ring: string }> = {
  new_entrant:    { icon: Sparkles,      cls: 'text-emerald-300', ring: 'border-emerald-900/60 bg-emerald-950/30' },
  surge:          { icon: TrendingUp,    cls: 'text-emerald-300', ring: 'border-emerald-900/40 bg-emerald-950/20' },
  drop:           { icon: TrendingDown,  cls: 'text-red-300',     ring: 'border-red-900/50 bg-red-950/30' },
  revenue_spike:  { icon: DollarSign,    cls: 'text-yellow-300',  ring: 'border-yellow-900/50 bg-yellow-950/25' },
}

function marketLabel(country: string, platform: string): string {
  return `${country} · ${platform === 'android' ? 'Android' : 'iOS'}`
}

export function TodayMovements() {
  const t = useT()
  const navigate = useNavigate()
  // 不传 country/platform → 后端按 SYNC_RANKING_COMBOS 全集汇总。仪表盘想看的就是
  // 跨市场的全景，不跟随单市场视图的 country/platform 切换（那是别的口径）。
  const { data, isLoading } = useQuery({
    queryKey: ['movements'],
    queryFn: () => movementsApi.get(),
    refetchInterval: 5 * 60_000,
  })

  if (isLoading || !data) return null

  const events = data.events
  const noBaseline = data.combos_without_baseline

  return (
    <div className="hud bg-surface border border-default rounded-xl p-5">
      <div className="flex items-baseline justify-between mb-3 gap-3">
        <div className="flex items-baseline gap-2">
          <Activity size={14} className="text-accent translate-y-px" />
          <h2 className="text-sm font-semibold text-primary">{t.dashboard.movementsTitle}</h2>
          <span className="text-xs text-muted">{t.dashboard.movementsSubtitle}</span>
        </div>
        {events.length > 0 && (
          <span className="text-xs text-muted font-data tabular-nums">{events.length}</span>
        )}
      </div>

      {events.length === 0 ? (
        <p className="text-xs text-muted py-2">{t.dashboard.movementsEmpty}</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {events.map((e, i) => (
            <MovementCard key={`${e.kind}-${e.app_id}-${e.country}-${e.platform}-${i}`}
              event={e} onClick={() => navigate(`/game/${e.app_id}`)} />
          ))}
        </div>
      )}

      {noBaseline.length > 0 && (
        <p className="mt-3 text-[11px] text-muted">
          {t.dashboard.movementsNoBaseline(noBaseline.join('、'))}
        </p>
      )}
    </div>
  )
}

function MovementCard({ event, onClick }: { event: MovementEvent; onClick: () => void }) {
  const t = useT()
  const meta = KIND_META[event.kind]
  const Icon = meta.icon

  const kindLabel = {
    new_entrant:    t.dashboard.movementKindNewEntrant,
    surge:          t.dashboard.movementKindSurge,
    drop:           t.dashboard.movementKindDrop,
    revenue_spike:  t.dashboard.movementKindRevenueSpike,
  }[event.kind]

  // 主信号：排名类用 from→to；收入类用百分比 + 数值
  let signal: string
  if (event.kind === 'revenue_spike' && event.revenue_pct != null) {
    signal = t.dashboard.movementRevenuePct(event.revenue_pct)
  } else {
    signal = t.dashboard.movementRankFromTo(event.prev_rank, event.cur_rank)
  }

  return (
    <button
      onClick={onClick}
      className={`group text-left rounded-lg border ${meta.ring} p-3 transition-colors hover:border-strong hover:bg-elevated/30`}
    >
      <div className="flex items-start gap-2.5">
        <GameIcon src={event.icon_url} name={event.name} className="w-9 h-9 rounded-lg shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Icon size={11} className={meta.cls} />
            <span className={`text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}>{kindLabel}</span>
            <span className="text-[10px] text-muted ml-auto shrink-0">{marketLabel(event.country, event.platform)}</span>
          </div>
          <div className="mt-1 text-sm text-primary truncate font-medium">{event.name}</div>
          <div className="mt-0.5 font-data text-xs text-secondary tabular-nums flex items-center gap-2">
            <span>{signal}</span>
            {event.kind === 'revenue_spike' && event.cur_revenue != null && (
              <span className="text-muted">· {formatRevenue(event.cur_revenue)}</span>
            )}
          </div>
        </div>
      </div>
    </button>
  )
}
