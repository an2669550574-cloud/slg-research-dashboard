import { useT } from '../i18n'
import { formatRelativeAge, backendTsToMs } from '../lib/utils'
import type { QuotaInfo } from '../lib/types'

export type { QuotaInfo, DataSource } from '../lib/types'

interface Props {
  quota: QuotaInfo | undefined
}

export function QuotaBanner({ quota }: Props) {
  const t = useT()
  if (!quota) return null

  const isWarning = !quota.exhausted && quota.percentage >= 80
  const tone = quota.exhausted ? 'danger' : isWarning ? 'warning' : 'normal'

  // 日同步应在 24h + 缓冲内刷新；超过 28h 说明 scheduler 可能已静默停摆。
  // 始终展示数据新鲜度，让"没报错但数据不更新"这种静默故障肉眼可见。
  const updatedMs = quota.data_updated_at ? backendTsToMs(quota.data_updated_at) : null
  const stale = updatedMs !== null && Date.now() - updatedMs > 28 * 3600 * 1000

  const containerCls = {
    danger: 'bg-red-950/40 border-red-900/60',
    warning: 'bg-yellow-950/40 border-yellow-900/60',
    normal: 'bg-surface border-default',
  }[tone]

  const usageCls = {
    danger: 'text-red-400',
    warning: 'text-yellow-400',
    normal: 'text-primary',
  }[tone]

  const percentCls = {
    danger: 'text-red-400',
    warning: 'text-yellow-400',
    normal: 'text-muted',
  }[tone]

  const barCls = {
    danger: 'bg-red-500',
    warning: 'bg-yellow-500',
    normal: 'bg-emerald-500',
  }[tone]

  return (
    <div data-testid="quota-banner" data-tone={tone} className={`rounded-xl border px-4 py-3 ${containerCls}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-secondary">{t.dashboard.quotaLabel}</span>
          <span className={`font-semibold ${usageCls}`}>
            {t.dashboard.quotaUsage(quota.used, quota.limit)}
          </span>
          <span className="text-xs text-muted">{t.dashboard.quotaResetHint(quota.year_month)}</span>
        </div>
        <span className={`text-xs ${percentCls}`}>{quota.percentage}%</span>
      </div>
      <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
        <div
          className={`h-full transition-all ${barCls}`}
          style={{ width: `${Math.min(100, quota.percentage)}%` }}
        />
      </div>
      {quota.exhausted && (
        <div className="mt-2 text-xs text-red-300">{t.dashboard.quotaExhausted}</div>
      )}
      {isWarning && (
        <div className="mt-2 text-xs text-yellow-300">{t.dashboard.quotaWarning}</div>
      )}
      {(quota.data_updated_at || (quota.data_source && quota.data_source !== 'real_api')) && (
        <div className="mt-2 flex items-center gap-3 text-xs text-muted">
          {quota.data_source && quota.data_source !== 'real_api' && (
            <span>
              {t.dashboard.dataSourceLabel}: <span className="text-primary font-medium">{t.dashboard.dataSource(quota.data_source)}</span>
            </span>
          )}
          {quota.data_updated_at && (
            <span className={stale ? 'text-yellow-400' : undefined}>
              {t.dashboard.dataUpdatedAt}: {formatRelativeAge(quota.data_updated_at)}
            </span>
          )}
        </div>
      )}
      {quota.data_source === 'snapshot_stale' && (
        <div className="mt-1.5 text-xs text-yellow-300">{t.dashboard.dataStaleWarning}</div>
      )}
    </div>
  )
}
