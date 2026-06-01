import { useQuery } from '@tanstack/react-query'
import { CalendarClock } from 'lucide-react'
import { gamesApi } from '../lib/api'
import { useT } from '../i18n'

/**
 * 全站顶部窄警示条：当 Sensor Tower 排名数据「停更」过久时显示。
 *
 * 背景：ST 配额是硬约束，US 周更 / JP·KR 月更。正常情况下榜单数据最多滞后约一周；
 * 一旦公司 ST key 被禁用 / 同步长期失败（如 5/24 起冻结），数据会停在某一天不再前进，
 * 但页面不会报错——用户很容易误把历史快照当成最新值。这条提示就是给这种「静默停更」
 * 兜底，让任何页面都能一眼看到数据有多旧。
 *
 * - 数据未拉到 / 仍在新鲜窗口内 → 完全不渲染（无空 DOM）
 * - 滞后 ≥ STALE_THRESHOLD_DAYS（10 天，留出周更正常滞后的缓冲）→ 橙色提示
 *
 * 取 Dashboard 默认市场（US/ios）的最新榜单日期作为新鲜度代理；共享 ['rankings','US','ios']
 * queryKey，与 Dashboard 默认视图自动去重，零额外 ST 配额（rankings 走本地库）。
 */
const STALE_THRESHOLD_DAYS = 10

function daysSince(dateStr: string): number {
  const then = new Date(`${dateStr}T00:00:00`)
  if (Number.isNaN(then.getTime())) return 0
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((today.getTime() - then.getTime()) / 86_400_000)
}

export function StaleDataAlert() {
  const t = useT()
  const { data: rankings } = useQuery({
    queryKey: ['rankings', 'US', 'ios'],
    queryFn: () => gamesApi.rankings('US', 'ios'),
    staleTime: 5 * 60_000,
  })

  const latestDate = rankings?.find(r => r.date)?.date ?? null
  if (!latestDate) return null

  const days = daysSince(latestDate)
  if (days < STALE_THRESHOLD_DAYS) return null

  return (
    <div
      role="status"
      data-testid="stale-data-alert"
      className="shrink-0 border-b border-orange-900/60 bg-orange-950/40 px-4 py-2 text-xs flex items-center gap-2 text-orange-200"
    >
      <CalendarClock size={14} className="text-orange-400" />
      <span>{t.dashboard.staleDataAlert(latestDate, days)}</span>
    </div>
  )
}
