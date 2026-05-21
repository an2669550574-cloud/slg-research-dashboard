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
// 卡片本体用中性 surface 背景（亮/暗双模式都干净），用左侧细色条 + 图标 + 标签
// 文字传达类别色，避免把整块卡片染色——之前 bg-emerald-950/30 在亮色模式下是
// 显眼的绿色块，与"情报终端"低噪音风格不符。
const KIND_META: Record<MovementKind, { icon: typeof TrendingUp; tone: string; rail: string }> = {
  new_entrant:    { icon: Sparkles,      tone: 'text-emerald-500 dark:text-emerald-300', rail: 'bg-emerald-500' },
  surge:          { icon: TrendingUp,    tone: 'text-emerald-500 dark:text-emerald-300', rail: 'bg-emerald-500' },
  drop:           { icon: TrendingDown,  tone: 'text-red-500 dark:text-red-300',         rail: 'bg-red-500' },
  revenue_spike:  { icon: DollarSign,    tone: 'text-yellow-600 dark:text-yellow-300',   rail: 'bg-yellow-500' },
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
      className="group relative text-left rounded-lg border border-default bg-surface p-3 pl-3.5 transition-colors hover:border-strong hover:bg-elevated/40 overflow-hidden"
    >
      {/* 类别色仅出现在左侧细条 + 图标/标签，卡片主体保持中性以降低视觉噪音 */}
      <span aria-hidden className={`absolute left-0 top-0 bottom-0 w-1 ${meta.rail}`} />
      <div className="flex items-start gap-2.5">
        <GameIcon src={event.icon_url} name={event.name} className="w-9 h-9 rounded-lg shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Icon size={11} className={meta.tone} />
            <span className={`text-[10px] font-semibold uppercase tracking-wide ${meta.tone}`}>{kindLabel}</span>
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
