import { describe, it, expect } from 'vitest'
import { buildEquityGraph } from './equityGraph'
import type { PublisherEntity, PublisherRelationLink, PublisherRelationType } from './types'

let relSeq = 0
function rel(entityId: number, name: string, type: PublisherRelationType = 'controlling', stake: number | null = null, relationId?: number): PublisherRelationLink {
  return { relation_id: relationId ?? ++relSeq, entity_id: entityId, name, relation_type: type, stake_pct: stake, note: null }
}

function entity(id: number, name: string, extra: Partial<PublisherEntity> = {}): PublisherEntity {
  return {
    id, name, name_en: null, hq_region: null, is_slg: true, brief: null, sort_order: 0,
    aliases: [], app_ids: [], itunes_artists: [], sources: [], provenance_tier: 'none',
    parents: [], children: [], product_count: 0, top_products: [],
    best_rank: null, best_rank_market: null,
    created_at: '2026-06-10T00:00:00', updated_at: '2026-06-10T00:00:00',
    ...extra,
  }
}

describe('buildEquityGraph', () => {
  it('无股权关系时返回空（孤立主体不画）', () => {
    expect(buildEquityGraph([entity(1, '甲'), entity(2, '乙')])).toEqual([])
  })

  it('三层股权链（控股 + 品牌关联）+ 独立一对 → 两个连通分量，大的在前、层级正确、关系去重', () => {
    // 中文传媒 -控股-> 智明星通 -关联(品牌)-> 江娱互动；世纪华通 -全资-> 点点互动；孤立的 FunPlus
    const zw = entity(1, '中文传媒', { children: [rel(2, '智明星通', 'controlling', 99.23, 101)] })
    const elex = entity(2, '智明星通', {
      parents: [rel(1, '中文传媒', 'controlling', 99.23, 101)],
      children: [rel(3, '江娱互动', 'affiliate', null, 102)],
    })
    const jy = entity(3, '江娱互动', { parents: [rel(2, '智明星通', 'affiliate', null, 102)] })
    const sht = entity(4, '世纪华通', { children: [rel(5, '点点互动', 'wholly_owned', 100, 103)] })
    const ddhd = entity(5, '点点互动', { parents: [rel(4, '世纪华通', 'wholly_owned', 100, 103)] })
    const funplus = entity(6, 'FunPlus')

    const comps = buildEquityGraph([zw, elex, jy, sht, ddhd, funplus])
    expect(comps).toHaveLength(2)
    expect(comps[0].nodes).toHaveLength(3) // 大分量在前
    expect(comps[1].nodes).toHaveLength(2)
    // 同一条关系在 parents/children 各出现一次 → 去重后每条只剩一条边
    expect(comps[0].edges).toHaveLength(2)
    expect(comps[1].edges).toHaveLength(1)
    // 母公司在上：深度 0/1/2
    const depthOf = (id: number) => comps[0].nodes.find(n => n.id === id)!.depth
    expect(depthOf(1)).toBe(0)
    expect(depthOf(2)).toBe(1)
    expect(depthOf(3)).toBe(2)
    // 孤立主体不进图
    expect(comps.flatMap(c => c.nodes).some(n => n.id === 6)).toBe(false)
  })

  it('纯财务参股不并组：仅靠参股相连的主体既不连通也不入图', () => {
    // 三七(1) -控股-> 极光(2)，三七(1) -参股24%-> 星合(3)。星合只有参股一条边 → 不进图。
    const sanqi = entity(1, '三七', {
      children: [rel(2, '极光', 'controlling', 100, 401), rel(3, '星合', 'minority', 24, 402)],
    })
    const jiguang = entity(2, '极光', { parents: [rel(1, '三七', 'controlling', 100, 401)] })
    const xinghe = entity(3, '星合', { parents: [rel(1, '三七', 'minority', 24, 402)] })

    const comps = buildEquityGraph([sanqi, jiguang, xinghe])
    expect(comps).toHaveLength(1)
    // 星合不并组、不入图；分量只含三七 + 极光
    expect(comps[0].nodes.map(n => n.id).sort()).toEqual([1, 2])
    expect(comps[0].edges).toHaveLength(1) // 仅控股边，参股边不入图
    expect(comps.flatMap(c => c.nodes).some(n => n.id === 3)).toBe(false)
  })

  it('组内成员之间的参股仍画虚线，但不抬高层级', () => {
    // 母(1) 全资 子A(2)、全资 子B(3)；子A -参股-> 子B（同组内）。参股边照画，子B 仍在第 1 层。
    const m = entity(1, '母', {
      children: [rel(2, '子A', 'wholly_owned', 100, 501), rel(3, '子B', 'wholly_owned', 100, 502)],
    })
    const a = entity(2, '子A', {
      parents: [rel(1, '母', 'wholly_owned', 100, 501)],
      children: [rel(3, '子B', 'minority', 8, 503)],
    })
    const b = entity(3, '子B', {
      parents: [rel(1, '母', 'wholly_owned', 100, 502), rel(2, '子A', 'minority', 8, 503)],
    })
    const [comp] = buildEquityGraph([m, a, b])
    expect(comp.nodes).toHaveLength(3)
    // 控股 2 条 + 组内参股 1 条都画
    expect(comp.edges).toHaveLength(3)
    expect(comp.edges.some(e => e.relationType === 'minority')).toBe(true)
    // 参股不抬高层级：子B 仍是第 1 层（由全资母决定），不会被压到第 2 层
    expect(comp.nodes.find(n => n.id === 3)!.depth).toBe(1)
  })

  it('坐标随深度递增，同分量内节点不重叠', () => {
    const a = entity(1, '母', { children: [rel(2, '子1', 'wholly_owned', 100, 201), rel(3, '子2', 'controlling', 60, 202)] })
    const b = entity(2, '子1', { parents: [rel(1, '母', 'wholly_owned', 100, 201)] })
    const c = entity(3, '子2', { parents: [rel(1, '母', 'controlling', 60, 202)] })
    const [comp] = buildEquityGraph([a, b, c])
    const root = comp.nodes.find(n => n.id === 1)!
    const kids = comp.nodes.filter(n => n.depth === 1)
    expect(kids).toHaveLength(2)
    expect(kids.every(k => k.y > root.y)).toBe(true)
    expect(kids[0].x).not.toBe(kids[1].x)
  })

  it('数据成环时不死循环', () => {
    const a = entity(1, 'A', { children: [rel(2, 'B', 'affiliate', null, 301)], parents: [rel(2, 'B', 'affiliate', null, 302)] })
    const b = entity(2, 'B', { children: [rel(1, 'A', 'affiliate', null, 302)], parents: [rel(1, 'A', 'affiliate', null, 301)] })
    const comps = buildEquityGraph([a, b])
    expect(comps).toHaveLength(1)
    expect(comps[0].nodes).toHaveLength(2)
    expect(comps[0].edges).toHaveLength(2)
  })

  it('对方主体不在列表里的关系被跳过（防御已删主体）', () => {
    const a = entity(1, 'A', { children: [rel(99, '已删', 'controlling', null, 401)] })
    expect(buildEquityGraph([a])).toEqual([])
  })
})
