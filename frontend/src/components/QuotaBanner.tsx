import { useT } from '../i18n'

export interface QuotaInfo {
  year_month: string
  used: number
  limit: number
  remaining: number
  percentage: number
  exhausted: boolean
}

interface Props {
  quota: QuotaInfo | undefined
}

export function QuotaBanner({ quota }: Props) {
  const t = useT()
  if (!quota) return null

  const isWarning = !quota.exhausted && quota.percentage >= 80
  const tone = quota.exhausted ? 'danger' : isWarning ? 'warning' : 'normal'

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
    </div>
  )
}
