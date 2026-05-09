import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuotaBanner, type QuotaInfo } from './QuotaBanner'
import { setLocale } from '../i18n'

function makeQuota(overrides: Partial<QuotaInfo> = {}): QuotaInfo {
  return {
    year_month: '2026-05',
    used: 0,
    limit: 500,
    remaining: 500,
    percentage: 0,
    exhausted: false,
    ...overrides,
  }
}

describe('QuotaBanner', () => {
  beforeEach(() => {
    setLocale('zh')
  })

  it('returns null when quota is undefined', () => {
    const { container } = render(<QuotaBanner quota={undefined} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders normal tone when usage < 80%', () => {
    render(<QuotaBanner quota={makeQuota({ used: 100, percentage: 20 })} />)
    const banner = screen.getByTestId('quota-banner')
    expect(banner).toHaveAttribute('data-tone', 'normal')
    expect(screen.getByText('100 / 500 次')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
    // 普通态不应该出现警告文案
    expect(screen.queryByText(/已用 80%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/已用尽/)).not.toBeInTheDocument()
  })

  it('renders warning tone at 80%+ but below exhaustion', () => {
    render(<QuotaBanner quota={makeQuota({ used: 420, percentage: 84, exhausted: false })} />)
    expect(screen.getByTestId('quota-banner')).toHaveAttribute('data-tone', 'warning')
    expect(screen.getByText(/已用 80%/)).toBeInTheDocument()
    expect(screen.queryByText(/已用尽/)).not.toBeInTheDocument()
  })

  it('renders danger tone when exhausted', () => {
    render(<QuotaBanner quota={makeQuota({ used: 500, percentage: 100, exhausted: true, remaining: 0 })} />)
    expect(screen.getByTestId('quota-banner')).toHaveAttribute('data-tone', 'danger')
    expect(screen.getByText(/已用尽/)).toBeInTheDocument()
    // 红色优先级高于黄色：到达 100% 时不应再显示 80% 警告
    expect(screen.queryByText(/已用 80%/)).not.toBeInTheDocument()
  })

  it('caps progress bar width at 100% even if percentage somehow exceeds', () => {
    const { container } = render(
      <QuotaBanner quota={makeQuota({ used: 600, percentage: 120, exhausted: true })} />
    )
    const bar = container.querySelector('[style*="width"]') as HTMLElement | null
    expect(bar).not.toBeNull()
    expect(bar!.style.width).toBe('100%')
  })

  it('switches text to English locale', () => {
    setLocale('en')
    render(<QuotaBanner quota={makeQuota({ used: 100, percentage: 20 })} />)
    expect(screen.getByText('100 / 500 calls')).toBeInTheDocument()
  })
})
