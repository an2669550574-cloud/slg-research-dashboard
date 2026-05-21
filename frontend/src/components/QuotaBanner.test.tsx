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

  it('renders org line when organization data is present and binds tone to it', () => {
    // 公司 98% 但本项目只 20% → 整体应被 org 推到 danger 红色
    render(
      <QuotaBanner
        quota={makeQuota({
          used: 102,
          percentage: 20.4,
          organization: { usage: 2943, limit: 3000, remaining: 57, percentage: 98.1, tier: null },
          account_user_usage: 102,
          account_stale: false,
        })}
      />
    )
    const banner = screen.getByTestId('quota-banner')
    // 容器色调跟随更紧约束 = 公司线（warning，因为 98.1<100 未爆，但 >=80）
    expect(banner).toHaveAttribute('data-tone', 'warning')
    // 公司行
    expect(screen.getByText('2943 / 3000 次')).toBeInTheDocument()
    expect(screen.getByText('98.1%')).toBeInTheDocument()
    // 本项目行（label 切到「其中本项目」而非「本月 Sensor Tower API」）
    expect(screen.getByText('其中本项目')).toBeInTheDocument()
    expect(screen.getByText('102 / 500 次')).toBeInTheDocument()
  })

  it('flags danger tone when org exhausted regardless of local usage', () => {
    render(
      <QuotaBanner
        quota={makeQuota({
          used: 102,
          percentage: 20.4,
          organization: { usage: 3000, limit: 3000, remaining: 0, percentage: 100, tier: null },
        })}
      />
    )
    expect(screen.getByTestId('quota-banner')).toHaveAttribute('data-tone', 'danger')
    expect(screen.getByText(/公司账户额度已耗尽/)).toBeInTheDocument()
  })

  it('shows account stale notice when account_stale is true', () => {
    render(
      <QuotaBanner
        quota={makeQuota({
          organization: { usage: 1500, limit: 3000, remaining: 1500, percentage: 50, tier: null },
          account_stale: true,
        })}
      />
    )
    expect(screen.getByText(/账户用量读取失败/)).toBeInTheDocument()
  })
})
