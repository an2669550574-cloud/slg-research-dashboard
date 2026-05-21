import { useT } from '../i18n'
import { formatRelativeAge, backendTsToMs } from '../lib/utils'
import type { QuotaInfo } from '../lib/types'

export type { QuotaInfo, DataSource } from '../lib/types'

interface Props {
  quota: QuotaInfo | undefined
}

type Tone = 'normal' | 'warning' | 'danger'

function toneFor(percentage: number, exhausted: boolean): Tone {
  if (exhausted || percentage >= 100) return 'danger'
  if (percentage >= 80) return 'warning'
  return 'normal'
}

const CONTAINER_CLS: Record<Tone, string> = {
  danger: 'bg-red-950/40 border-red-900/60',
  warning: 'bg-yellow-950/40 border-yellow-900/60',
  normal: 'bg-surface border-default',
}
const USAGE_CLS: Record<Tone, string> = {
  danger: 'text-red-400',
  warning: 'text-yellow-400',
  normal: 'text-primary',
}
const PERCENT_CLS: Record<Tone, string> = {
  danger: 'text-red-400',
  warning: 'text-yellow-400',
  normal: 'text-muted',
}
const BAR_CLS: Record<Tone, string> = {
  danger: 'bg-red-500',
  warning: 'bg-yellow-500',
  normal: 'bg-emerald-500',
}

export function QuotaBanner({ quota }: Props) {
  const t = useT()
  if (!quota) return null

  // 容器/视觉色调由「最紧的那条约束」决定——通常是公司账户线（3000 共享池），
  // 它一旦爆掉，本项目自己的 500 额度还剩多少都没意义（ST 会直接拒）。
  // 无 org 数据时退化成原来的本地口径。
  const orgUsage = quota.organization?.usage
  const orgLimit = quota.organization?.limit
  const orgPct = quota.organization?.percentage ?? 0
  const orgExhausted =
    typeof orgUsage === 'number' &&
    typeof orgLimit === 'number' &&
    orgLimit > 0 &&
    orgUsage >= orgLimit
  const orgTone: Tone | null = quota.organization
    ? toneFor(orgPct, orgExhausted)
    : null

  const localTone = toneFor(quota.percentage, quota.exhausted)

  // 优先用 org tone（更紧约束）；其次本地。
  const containerTone: Tone = orgTone ?? localTone

  const updatedMs = quota.data_updated_at ? backendTsToMs(quota.data_updated_at) : null
  const stale = updatedMs !== null && Date.now() - updatedMs > 28 * 3600 * 1000

  return (
    <div data-testid="quota-banner" data-tone={containerTone} className={`rounded-xl border px-4 py-3 ${CONTAINER_CLS[containerTone]}`}>
      {/* 公司账户行（如果有数据）：最重要的硬约束，放在最上面 */}
      {quota.organization && orgTone && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-secondary">{t.dashboard.quotaOrgLabel}</span>
              <span className={`font-semibold ${USAGE_CLS[orgTone]}`}>
                {t.dashboard.quotaUsage(orgUsage ?? 0, orgLimit ?? 0)}
              </span>
              {quota.account_stale && (
                <span className="text-xs text-yellow-300">·</span>
              )}
            </div>
            <span className={`text-xs ${PERCENT_CLS[orgTone]}`}>{orgPct}%</span>
          </div>
          <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
            <div
              data-testid="quota-org-bar"
              className={`h-full transition-all ${BAR_CLS[orgTone]}`}
              style={{ width: `${Math.min(100, orgPct)}%` }}
            />
          </div>
          {orgExhausted && (
            <div className="mt-2 text-xs text-red-300">{t.dashboard.quotaOrgExhausted}</div>
          )}
          {!orgExhausted && orgTone === 'warning' && (
            <div className="mt-2 text-xs text-yellow-300">{t.dashboard.quotaOrgWarning}</div>
          )}
          {quota.account_stale && (
            <div className="mt-1.5 text-xs text-yellow-300">{t.dashboard.quotaAccountStale}</div>
          )}
        </div>
      )}

      {/* 本项目本地计数（始终展示，是我们自己的护栏） */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-secondary">
            {quota.organization ? t.dashboard.quotaProjectLabel : t.dashboard.quotaLabel}
          </span>
          <span className={`font-semibold ${USAGE_CLS[localTone]}`}>
            {t.dashboard.quotaUsage(quota.used, quota.limit)}
          </span>
          <span className="text-xs text-muted">{t.dashboard.quotaResetHint(quota.year_month)}</span>
        </div>
        <span className={`text-xs ${PERCENT_CLS[localTone]}`}>{quota.percentage}%</span>
      </div>
      <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
        <div
          className={`h-full transition-all ${BAR_CLS[localTone]}`}
          style={{ width: `${Math.min(100, quota.percentage)}%` }}
        />
      </div>
      {quota.exhausted && (
        <div className="mt-2 text-xs text-red-300">{t.dashboard.quotaExhausted}</div>
      )}
      {!quota.exhausted && quota.percentage >= 80 && (
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
