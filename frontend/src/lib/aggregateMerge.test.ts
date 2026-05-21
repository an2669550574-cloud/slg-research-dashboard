import { describe, it, expect } from 'vitest'
import { mergeCrossPlatform, normIdent, type AggRow } from './aggregateMerge'

function row(over: Partial<AggRow> & { app_id: string }): AggRow {
  return {
    name: null,
    publisher: null,
    icon_url: null,
    downloads: 0,
    revenue: 0,
    ...over,
  }
}

describe('normIdent', () => {
  it('lowercases and strips non-alphanumerics', () => {
    expect(normIdent('Century Games Pte. Ltd.')).toBe('centurygamespteltd')
    expect(normIdent('Century Games PTE. LTD.')).toBe('centurygamespteltd')
    expect(normIdent('Last War:Survival Game')).toBe('lastwarsurvivalgame')
    expect(normIdent(null)).toBe('')
    expect(normIdent(undefined)).toBe('')
  })
})

describe('mergeCrossPlatform', () => {
  it('merges iOS+Android with identical publisher and name (case differs)', () => {
    const rows: AggRow[] = [
      row({ app_id: 'ios.123', name: 'Whiteout Survival', publisher: 'Century Games PTE. LTD.', revenue: 929_000, downloads: 7_000 }),
      row({ app_id: 'com.android.whiteout', name: 'Whiteout Survival', publisher: 'Century Games Pte. Ltd.', revenue: 904_000, downloads: 14_000 }),
    ]
    const out = mergeCrossPlatform(rows)
    expect(out).toHaveLength(1)
    expect(out[0].app_id).toBe('ios.123')  // 收入更高的代表
    expect(out[0].revenue).toBe(1_833_000)
    expect(out[0].downloads).toBe(21_000)
  })

  it('merges by prefix when one name is prefix of the other', () => {
    const rows: AggRow[] = [
      row({ app_id: 'ios.1', name: 'Last War:Survival', publisher: 'FUNFLY PTE. LTD.', revenue: 962_000, downloads: 2_000 }),
      row({ app_id: 'and.1', name: 'Last War:Survival Game', publisher: 'FUNFLY PTE. LTD.', revenue: 596_000, downloads: 750 }),
    ]
    const out = mergeCrossPlatform(rows)
    expect(out).toHaveLength(1)
    expect(out[0].name).toBe('Last War:Survival')  // 收入高那条的 name
    expect(out[0].revenue).toBe(1_558_000)
  })

  it('does NOT merge across different publishers even with same name', () => {
    const rows: AggRow[] = [
      row({ app_id: 'a', name: 'Kingdom', publisher: 'Pub A', revenue: 100, downloads: 10 }),
      row({ app_id: 'b', name: 'Kingdom', publisher: 'Pub B', revenue: 80, downloads: 8 }),
    ]
    expect(mergeCrossPlatform(rows)).toHaveLength(2)
  })

  it('does NOT merge when short side < 5 chars (防止 "Z" 吞 "ZGame")', () => {
    const rows: AggRow[] = [
      row({ app_id: 'a', name: 'Z',     publisher: 'Same Pub', revenue: 100 }),
      row({ app_id: 'b', name: 'ZGame', publisher: 'Same Pub', revenue: 80 }),
    ]
    expect(mergeCrossPlatform(rows)).toHaveLength(2)
  })

  it('keeps rows with empty publisher unmerged (avoid lumping orphans together)', () => {
    const rows: AggRow[] = [
      row({ app_id: 'a', name: 'Foo', publisher: null, revenue: 100 }),
      row({ app_id: 'b', name: 'Foo', publisher: '',   revenue: 80 }),
    ]
    expect(mergeCrossPlatform(rows)).toHaveLength(2)
  })

  it('sorts final output by merged revenue descending', () => {
    const rows: AggRow[] = [
      row({ app_id: 'low', name: 'Small Title', publisher: 'P1', revenue: 100 }),
      row({ app_id: 'h1',  name: 'Big Title',   publisher: 'P2', revenue: 500 }),
      row({ app_id: 'h2',  name: 'Big Title',   publisher: 'P2', revenue: 400 }),
    ]
    const out = mergeCrossPlatform(rows)
    expect(out.map(r => r.app_id)).toEqual(['h1', 'low'])
    expect(out[0].revenue).toBe(900)
  })

  it('merges 3-way (e.g. iOS+Android+iPad variants) into one row', () => {
    const rows: AggRow[] = [
      row({ app_id: 'a', name: 'Kingshot',           publisher: 'Century Games', revenue: 300 }),
      row({ app_id: 'b', name: 'Kingshot',           publisher: 'Century Games', revenue: 200 }),
      row({ app_id: 'c', name: 'Kingshot Plus',      publisher: 'Century Games', revenue: 100 }),
    ]
    const out = mergeCrossPlatform(rows)
    expect(out).toHaveLength(1)
    expect(out[0].revenue).toBe(600)
  })
})
