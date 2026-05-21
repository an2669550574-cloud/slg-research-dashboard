import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, OctagonAlert } from 'lucide-react'
import { quotaApi } from '../lib/api'
import { useT } from '../i18n'

/**
 * 全站顶部窄警示条：当公司 ST 账户池进入 low/reserved 状态时显示。
 *
 * - normal / quota 未拉到 → 完全不渲染（无空 DOM）
 * - low（黄）→ 池剩余 ≤ SENSOR_TOWER_ORG_LOW_THRESHOLD（默认 100）
 * - reserved（红）→ 池剩余 ≤ SENSOR_TOWER_ORG_RESERVE（默认 30）
 *                  此时本项目 try_consume 已主动停拉，新数据全走历史快照
 *
 * 共享 ['quota'] queryKey，与 Dashboard/Compare 的 useQuery 自动去重，
 * 不会因为常驻 mount 就多打一次 /api/quota。
 */
export function GlobalQuotaAlert() {
  const t = useT()
  const { data: quota } = useQuery({
    queryKey: ['quota'],
    queryFn: () => quotaApi.get(),
    refetchInterval: 60_000,
  })

  const state = quota?.account_state
  if (!quota || (state !== 'low' && state !== 'reserved')) return null

  const remaining = quota.organization?.remaining ?? 0
  const isReserved = state === 'reserved'

  const Icon = isReserved ? OctagonAlert : AlertTriangle
  const containerCls = isReserved
    ? 'bg-red-950/40 border-red-900/60 text-red-200'
    : 'bg-yellow-950/40 border-yellow-900/60 text-yellow-200'
  const iconCls = isReserved ? 'text-red-400' : 'text-yellow-400'

  const message = isReserved
    ? t.dashboard.globalAlertReserved
    : t.dashboard.globalAlertLow(remaining)

  return (
    <div
      role="status"
      data-testid="global-quota-alert"
      data-state={state}
      className={`shrink-0 border-b px-4 py-2 text-xs flex items-center gap-2 ${containerCls}`}
    >
      <Icon size={14} className={iconCls} />
      <span>{message}</span>
    </div>
  )
}
