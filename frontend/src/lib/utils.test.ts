import { describe, it, expect } from 'vitest'
import {
  formatNumber, formatRevenue, EVENT_TYPE_CONFIG, PLATFORM_CONFIG, cn,
  formatRelativeAge, backendTsToMs,
} from './utils'

describe('formatNumber', () => {
  it('returns raw string under 1000', () => {
    expect(formatNumber(0)).toBe('0')
    expect(formatNumber(42)).toBe('42')
    expect(formatNumber(999)).toBe('999')
  })

  it('formats thousands as K with no decimals', () => {
    expect(formatNumber(1000)).toBe('1K')
    expect(formatNumber(12_345)).toBe('12K')
    expect(formatNumber(999_999)).toBe('1000K')
  })

  it('formats millions as M with one decimal', () => {
    expect(formatNumber(1_000_000)).toBe('1.0M')
    expect(formatNumber(2_500_000)).toBe('2.5M')
    expect(formatNumber(57_300_000)).toBe('57.3M')
  })
})

describe('formatRevenue', () => {
  it('prefixes $ on small numbers', () => {
    expect(formatRevenue(0)).toBe('$0')
    expect(formatRevenue(99)).toBe('$99')
  })

  it('formats thousands with $K', () => {
    expect(formatRevenue(1000)).toBe('$1K')
    expect(formatRevenue(75_000)).toBe('$75K')
  })

  it('formats millions with $X.XXM', () => {
    expect(formatRevenue(1_470_000)).toBe('$1.47M')
    expect(formatRevenue(10_000_000)).toBe('$10.00M')
  })
})

describe('EVENT_TYPE_CONFIG / PLATFORM_CONFIG', () => {
  it('covers all expected event kinds', () => {
    expect(Object.keys(EVENT_TYPE_CONFIG).sort()).toEqual(
      ['launch', 'marketing', 'ranking', 'revenue', 'version']
    )
  })

  it('covers all expected platforms', () => {
    expect(Object.keys(PLATFORM_CONFIG).sort()).toEqual(
      ['meta', 'other', 'tiktok', 'youtube']
    )
  })

  it('every entry has label + color', () => {
    for (const cfg of Object.values(EVENT_TYPE_CONFIG)) {
      expect(cfg.label).toBeTruthy()
      expect(cfg.color).toMatch(/^text-/)
      expect(cfg.bg).toMatch(/^bg-/)
    }
    for (const cfg of Object.values(PLATFORM_CONFIG)) {
      expect(cfg.label).toBeTruthy()
      expect(cfg.color).toMatch(/^text-/)
    }
  })
})

describe('backendTsToMs', () => {
  it('parses SQLite-style "YYYY-MM-DD HH:MM:SS" as UTC', () => {
    expect(backendTsToMs('2026-05-15 14:44:21')).toBe(Date.parse('2026-05-15T14:44:21Z'))
  })

  it('parses ISO with T/Z unchanged', () => {
    expect(backendTsToMs('2026-05-15T14:44:21Z')).toBe(Date.parse('2026-05-15T14:44:21Z'))
  })

  it('returns null on garbage', () => {
    expect(backendTsToMs('not-a-date')).toBeNull()
  })
})

describe('formatRelativeAge', () => {
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

  it('under a minute → <1m', () => {
    expect(formatRelativeAge(ago(30_000))).toBe('<1m')
  })

  it('minutes / hours / days, floored', () => {
    expect(formatRelativeAge(ago(5 * 60_000))).toBe('5m')
    expect(formatRelativeAge(ago(3 * 3_600_000))).toBe('3h')
    expect(formatRelativeAge(ago(50 * 3_600_000))).toBe('2d')
  })

  it('accepts the space-separated UTC backend format', () => {
    expect(formatRelativeAge('2020-01-01 00:00:00')).toMatch(/\d+d$/)
  })

  it('invalid input → em dash', () => {
    expect(formatRelativeAge('nonsense')).toBe('—')
  })
})

describe('cn', () => {
  it('merges class names and dedupes Tailwind conflicts', () => {
    // tailwind-merge: later wins for the same utility group
    expect(cn('px-2', 'px-4')).toBe('px-4')
    expect(cn('text-sm', false && 'text-lg', 'font-bold')).toBe('text-sm font-bold')
  })
})
