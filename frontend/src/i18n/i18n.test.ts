import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { setLocale, getLocale, useLocale, useT } from './index'
import { zh } from './zh'
import { en } from './en'

describe('i18n locale store', () => {
  beforeEach(() => {
    // 每个测试从 zh 开始，确保不被 localStorage 跨测试污染
    window.localStorage.clear()
    setLocale('zh')
  })

  afterEach(() => {
    setLocale('zh')
  })

  it('getLocale returns current value', () => {
    expect(getLocale()).toBe('zh')
    setLocale('en')
    expect(getLocale()).toBe('en')
  })

  it('useT returns the dictionary matching current locale', () => {
    const { result, rerender } = renderHook(() => useT())
    expect(result.current).toBe(zh)

    act(() => setLocale('en'))
    rerender()
    expect(result.current).toBe(en)
  })

  it('useLocale subscribes to changes via useSyncExternalStore', () => {
    const { result } = renderHook(() => useLocale())
    expect(result.current).toBe('zh')

    act(() => setLocale('en'))
    expect(result.current).toBe('en')
  })

  it('setLocale persists to localStorage', () => {
    setLocale('en')
    expect(window.localStorage.getItem('slg-locale')).toBe('en')
  })

  it('setLocale to same value is a noop (no spurious notifications)', () => {
    let renders = 0
    renderHook(() => {
      renders++
      return useLocale()
    })
    const before = renders
    act(() => setLocale('zh'))  // 已经是 zh
    expect(renders).toBe(before)
  })
})

describe('translation parity', () => {
  it('zh and en have identical top-level keys', () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort())
  })

  it('zh and en have identical dashboard sub-keys', () => {
    expect(Object.keys(zh.dashboard).sort()).toEqual(Object.keys(en.dashboard).sort())
  })

  it('quota strings exist in both locales', () => {
    expect(zh.dashboard.quotaLabel).toBeTruthy()
    expect(en.dashboard.quotaLabel).toBeTruthy()
    expect(typeof zh.dashboard.quotaUsage).toBe('function')
    expect(typeof en.dashboard.quotaUsage).toBe('function')
    expect(zh.dashboard.quotaUsage(10, 500)).toContain('10')
    expect(en.dashboard.quotaUsage(10, 500)).toContain('10')
  })
})
