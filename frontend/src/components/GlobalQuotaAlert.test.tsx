import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GlobalQuotaAlert } from './GlobalQuotaAlert'
import { setLocale } from '../i18n'
import type { QuotaInfo } from '../lib/types'
import * as apiModule from '../lib/api'

function renderWith(quota: QuotaInfo | undefined) {
  vi.spyOn(apiModule.quotaApi, 'get').mockResolvedValue(
    (quota ?? {
      year_month: '2026-05',
      used: 0,
      limit: 500,
      remaining: 500,
      percentage: 0,
      exhausted: false,
    }) as QuotaInfo,
  )
  // 每个用例独立 client，避免上一个 mock 的缓存命中
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <GlobalQuotaAlert />
    </QueryClientProvider>,
  )
}

const baseQuota: QuotaInfo = {
  year_month: '2026-05',
  used: 102,
  limit: 500,
  remaining: 398,
  percentage: 20.4,
  exhausted: false,
}

describe('GlobalQuotaAlert', () => {
  beforeEach(() => {
    setLocale('zh')
    vi.restoreAllMocks()
  })

  it('renders nothing while quota is still loading', () => {
    const { container } = renderWith(undefined)
    // useQuery 首次返回 undefined data；条件守卫直接返 null
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when account_state is normal', async () => {
    renderWith({
      ...baseQuota,
      account_state: 'normal',
      organization: { usage: 1000, limit: 3000, remaining: 2000, percentage: 33.3, tier: null },
    })
    // 等一帧后仍应不渲染
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('global-quota-alert')).toBeNull()
  })

  it('renders yellow alert with remaining count when state=low', async () => {
    renderWith({
      ...baseQuota,
      account_state: 'low',
      organization: { usage: 2950, limit: 3000, remaining: 50, percentage: 98.3, tier: null },
    })
    const alert = await screen.findByTestId('global-quota-alert')
    expect(alert).toHaveAttribute('data-state', 'low')
    expect(alert.className).toContain('bg-yellow-950/40')
    expect(alert.textContent).toContain('仅剩 50 次')
  })

  it('renders red alert with reserved messaging when state=reserved', async () => {
    renderWith({
      ...baseQuota,
      account_state: 'reserved',
      organization: { usage: 2998, limit: 3000, remaining: 2, percentage: 99.9, tier: null },
    })
    const alert = await screen.findByTestId('global-quota-alert')
    expect(alert).toHaveAttribute('data-state', 'reserved')
    expect(alert.className).toContain('bg-red-950/40')
    expect(alert.textContent).toMatch(/暂停调用/)
  })

  it('switches to English locale', async () => {
    setLocale('en')
    renderWith({
      ...baseQuota,
      account_state: 'low',
      organization: { usage: 2950, limit: 3000, remaining: 50, percentage: 98.3, tier: null },
    })
    const alert = await screen.findByTestId('global-quota-alert')
    expect(alert.textContent).toContain('Only 50 Sensor Tower calls left')
  })
})
