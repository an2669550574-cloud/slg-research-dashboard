import { describe, it, expect } from 'vitest'
import { groupByApp, groupPublisherByApp } from './newcomerGrouping'
import type { NewcomerHistoryItem, PublisherNewcomerItem } from './types'

/** 造一条最小检出行——只填分组逻辑关心的字段，其余给安全默认。 */
function row(p: Partial<NewcomerHistoryItem> & { app_id: string }): NewcomerHistoryItem {
  return {
    id: Math.floor(Math.random() * 1e9),
    country: 'US', platform: 'ios', chart_type: 'grossing', as_of: '2026-06-01',
    name: 'Game', publisher: 'Studio', icon_url: null,
    rank: null, revenue: null, is_slg: false,
    first_detected_at: '2026-06-01T00:00:00Z',
    store_url: null, release_date: null, genre: null, rating: null,
    rating_count: null, price: null, description: null,
    summary_cn: null, description_cn: null, screenshots: [],
    version: null, current_version_date: null, languages: null,
    enrich_source: null, entity_id: null, entity_name: null, is_reentry: false,
    ...p,
  }
}

describe('groupByApp', () => {
  it('merges same app_id across markets into one group', () => {
    const groups = groupByApp([
      row({ app_id: '111', country: 'US', rank: 12, as_of: '2026-06-03', first_detected_at: '2026-06-03T00:00:00Z' }),
      row({ app_id: '111', country: 'JP', rank: 5, as_of: '2026-06-01', first_detected_at: '2026-06-01T00:00:00Z' }),
      row({ app_id: '111', country: 'KR', rank: 30, as_of: '2026-06-02', first_detected_at: '2026-06-02T00:00:00Z' }),
    ])
    expect(groups).toHaveLength(1)
    const g = groups[0]
    expect(g.app_id).toBe('111')
    expect(g.markets).toHaveLength(3)
    // 代表行 = 最佳名次（JP #5）
    expect(g.bestRank).toBe(5)
    expect(g.rep.country).toBe('JP')
    // markets 按名次升序
    expect(g.markets.map(m => m.rank)).toEqual([5, 12, 30])
    // 最早检出 = 06-01（JP）
    expect(g.earliestAsOf).toBe('2026-06-01')
  })

  it('keeps different app_ids separate (cross-platform not merged)', () => {
    // 同一款 iOS(数字) + Android(包名) 永不撞键 → 两张卡（跨平台是另一轴）
    const groups = groupByApp([
      row({ app_id: '111', platform: 'ios' }),
      row({ app_id: 'com.foo.bar', platform: 'android' }),
    ])
    expect(groups.map(g => g.app_id).sort()).toEqual(['111', 'com.foo.bar'])
  })

  it('preserves first-seen order (newest detection first from server sort)', () => {
    const groups = groupByApp([
      row({ app_id: 'A' }), row({ app_id: 'B' }), row({ app_id: 'A' }), row({ app_id: 'C' }),
    ])
    expect(groups.map(g => g.app_id)).toEqual(['A', 'B', 'C'])
  })

  it('handles null ranks (sink to bottom; bestRank null if all null)', () => {
    const g = groupByApp([
      row({ app_id: 'X', rank: null }),
      row({ app_id: 'X', rank: 7 }),
    ])[0]
    expect(g.bestRank).toBe(7)
    expect(g.markets.map(m => m.rank)).toEqual([7, null])

    const allNull = groupByApp([row({ app_id: 'Y', rank: null }), row({ app_id: 'Y', rank: null })])[0]
    expect(allNull.bestRank).toBeNull()
  })

  it('flags anyReentry when any market is a re-entry', () => {
    expect(groupByApp([
      row({ app_id: 'Z', is_reentry: false }),
      row({ app_id: 'Z', is_reentry: true }),
    ])[0].anyReentry).toBe(true)
    expect(groupByApp([row({ app_id: 'W', is_reentry: false })])[0].anyReentry).toBe(false)
  })
})

function pubRow(p: Partial<PublisherNewcomerItem> & { app_id: string }): PublisherNewcomerItem {
  return {
    country: 'US', platform: 'ios', as_of: '2026-06-01', name: 'Game', publisher: 'Studio',
    icon_url: null, rank: null, revenue: null, downloads: null,
    entity_id: 1, entity_name: '某主体', matched_by: 'alias',
    ...p,
  }
}

describe('groupPublisherByApp', () => {
  it('merges same app_id across markets, best rank as headline, earliest as_of', () => {
    const groups = groupPublisherByApp([
      pubRow({ app_id: 'p1', country: 'US', rank: 40, as_of: '2026-06-05', revenue: 100 }),
      pubRow({ app_id: 'p1', country: 'JP', rank: 12, as_of: '2026-06-02', revenue: 999 }),
    ])
    expect(groups).toHaveLength(1)
    const g = groups[0]
    expect(g.markets).toHaveLength(2)
    expect(g.bestRank).toBe(12)
    expect(g.rep.country).toBe('JP')        // 代表行 = 最佳名次
    expect(g.rep.revenue).toBe(999)          // revenue 取代表行（最佳名次市场）
    expect(g.earliestAsOf).toBe('2026-06-02')
    expect(g.markets.map(m => m.rank)).toEqual([12, 40])
  })

  it('keeps distinct app_ids separate and preserves entity order', () => {
    const groups = groupPublisherByApp([
      pubRow({ app_id: 'a', entity_name: '甲' }),
      pubRow({ app_id: 'b', entity_name: '乙' }),
      pubRow({ app_id: 'a', entity_name: '甲', country: 'JP' }),
    ])
    expect(groups.map(g => g.app_id)).toEqual(['a', 'b'])
  })
})
