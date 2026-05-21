import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { TodayMovements } from './TodayMovements'
import { setLocale } from '../i18n'
import type { MovementsOut } from '../lib/types'
import * as apiModule from '../lib/api'

function renderWith(data: MovementsOut | undefined) {
  vi.spyOn(apiModule.movementsApi, 'get').mockResolvedValue(
    data ?? { today: '2026-05-21', events: [], combos_without_baseline: [] }
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TodayMovements />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('TodayMovements', () => {
  beforeEach(() => {
    setLocale('zh')
    vi.restoreAllMocks()
  })

  it('renders empty state when no events', async () => {
    renderWith({ today: '2026-05-21', events: [], combos_without_baseline: [] })
    expect(await screen.findByText(/今日暂无显著异动/)).toBeInTheDocument()
  })

  it('renders new entrant card with rank transition', async () => {
    renderWith({
      today: '2026-05-21',
      events: [{
        kind: 'new_entrant',
        country: 'US', platform: 'ios', today: '2026-05-21', prev_date: '2026-05-20',
        app_id: 'com.game.x', name: 'Test Game', icon_url: null,
        prev_rank: null, cur_rank: 3,
        prev_revenue: null, cur_revenue: null, revenue_pct: null,
      }],
      combos_without_baseline: [],
    })
    expect(await screen.findByText('Test Game')).toBeInTheDocument()
    expect(screen.getByText(/新进\s*Top/i)).toBeInTheDocument()
    expect(screen.getByText(/榜外\s*→\s*#3/)).toBeInTheDocument()
    expect(screen.getByText(/US · iOS/)).toBeInTheDocument()
  })

  it('renders revenue spike with percentage', async () => {
    renderWith({
      today: '2026-05-21',
      events: [{
        kind: 'revenue_spike',
        country: 'JP', platform: 'android', today: '2026-05-21', prev_date: '2026-05-20',
        app_id: 'com.game.y', name: 'Money Game', icon_url: null,
        prev_rank: 5, cur_rank: 5,
        prev_revenue: 100_000, cur_revenue: 250_000, revenue_pct: 150,
      }],
      combos_without_baseline: [],
    })
    expect(await screen.findByText('Money Game')).toBeInTheDocument()
    expect(screen.getByText('+150%')).toBeInTheDocument()
  })

  it('shows cold-start hint when combos lack baseline', async () => {
    renderWith({
      today: '2026-05-21',
      events: [],
      combos_without_baseline: ['JP/android', 'KR/android'],
    })
    expect(await screen.findByText(/JP\/android、KR\/android/)).toBeInTheDocument()
  })
})
