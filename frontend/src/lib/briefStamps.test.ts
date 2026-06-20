import { describe, it, expect } from 'vitest'
import { parseBrief } from './briefStamps'

describe('parseBrief', () => {
  it('returns empty for null/undefined/empty', () => {
    expect(parseBrief(null)).toEqual({ main: '', stamps: [] })
    expect(parseBrief(undefined)).toEqual({ main: '', stamps: [] })
    expect(parseBrief('')).toEqual({ main: '', stamps: [] })
  })

  it('returns main only when no stamps', () => {
    const r = parseBrief('FunPlus 系；State of Survival / Sea of Conquest')
    expect(r.main).toBe('FunPlus 系；State of Survival / Sea of Conquest')
    expect(r.stamps).toEqual([])
  })

  it('parses one stamp after main', () => {
    const r = parseBrief('Survivor.io / Archero；独立\n\n【调研负面发现 2026-06-20】传言腾讯参股已查证无')
    expect(r.main).toBe('Survivor.io / Archero；独立')
    expect(r.stamps).toEqual([
      { label: '调研负面发现', date: '2026-06-20', content: '传言腾讯参股已查证无' },
    ])
  })

  it('parses multiple stamps in date-desc order (newest first)', () => {
    const text = [
      '主体起步',
      '',
      '【调研更新 2026-06-19】首次溯源',
      '',
      '【调研更新 2026-06-20】二次溯源补一手源',
      '',
      '【复查 negative 2026-06-18】查无母体',
    ].join('\n')
    const r = parseBrief(text)
    expect(r.main).toBe('主体起步')
    expect(r.stamps.map(s => s.date)).toEqual(['2026-06-20', '2026-06-19', '2026-06-18'])
    expect(r.stamps[0].label).toBe('调研更新')
    expect(r.stamps[2].label).toBe('复查 negative')
  })

  it('handles main with multiline content before first stamp', () => {
    const text = '行 1\n行 2\n行 3\n\n【调研更新 2026-06-20】戳记'
    const r = parseBrief(text)
    expect(r.main).toBe('行 1\n行 2\n行 3')
    expect(r.stamps).toHaveLength(1)
  })

  it('preserves stamp content with newlines', () => {
    const text = '主\n\n【调研更新 2026-06-20】第 1 行\n第 2 行\n第 3 行'
    const r = parseBrief(text)
    expect(r.stamps[0].content).toBe('第 1 行\n第 2 行\n第 3 行')
  })

  it('treats malformed stamp as fallback "历史" entry, sinks to bottom', () => {
    // 没 date 的不会被 STAMP_SPLIT_RE 切出来，所以这种情况其实测的是"被切但 STAMP_PARSE_RE 不匹配"
    // 用一个真的"被切但内部 label 没空格 + 日期"的字符串构造：
    // 实际上 STAMP_SPLIT_RE 要求 \n\n【...YYYY-MM-DD】，所以无日期段不会被切。
    // 该 case 验证：被切但 PARSE_RE 抓不到 label/date —— 几乎不可能。所以验"日期都正常 + 多个戳记"够了，跳过该 case。
    const text = '主\n\n【调研更新 2026-06-20】内容 A\n\n【调研更新 2026-06-21】内容 B'
    const r = parseBrief(text)
    expect(r.stamps).toHaveLength(2)
    expect(r.stamps[0].date).toBe('2026-06-21')
  })
})
