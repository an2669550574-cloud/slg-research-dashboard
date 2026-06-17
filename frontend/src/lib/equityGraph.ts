// 股权关系图谱的纯布局逻辑：从 publishers 列表（已带 parents/children）拼有向图，
// 按连通分量分组、母公司在上的分层布局。纯函数、零依赖，组件只管把结果画成 SVG。
import type { PublisherEntity, PublisherRelationType, ProvenanceTier } from './types'

export interface EquityNode {
  id: number
  name: string
  nameEn: string | null
  tier: ProvenanceTier
  depth: number
  x: number
  y: number
}

export interface EquityEdge {
  relationId: number
  parentId: number
  childId: number
  relationType: PublisherRelationType
  stakePct: number | null
  /** 调研备注（如持股来源、退出/回购历史），图谱上 hover 边可见 */
  note: string | null
}

export interface EquityComponent {
  nodes: EquityNode[]
  edges: EquityEdge[]
  width: number
  height: number
}

export const NODE_W = 156
export const NODE_H = 48
const H_GAP = 36
const V_GAP = 72

/**
 * 「资本系」并组口径——三视图（集团 tab / 股权图谱 / 资本树）共用的单一源：
 * 控制级（全资 wholly_owned / 控股 controlling）+ 品牌型关联（affiliate，如莉莉丝→Farlight、
 * 元趣→Funfly）才把两个主体并进同一资本系。纯财务参股（minority，如三七→星合 24%）**不并组**——
 * 只作主体卡上的关联链接，不进连通分量、不做资本树嵌套、不进集团卡。改口径只动这一处。
 */
export const GROUP_EDGE_TYPES: ReadonlySet<PublisherRelationType> = new Set<PublisherRelationType>([
  'wholly_owned', 'controlling', 'affiliate',
])
export const isGroupEdge = (relationType: PublisherRelationType) => GROUP_EDGE_TYPES.has(relationType)

/** 收集去重后的全部股权边。每条关系在 parent.children 与 child.parents 各出现一次，按 relation_id 去重。 */
function collectEdges(entities: PublisherEntity[]): EquityEdge[] {
  const known = new Set(entities.map(e => e.id))
  const byId = new Map<number, EquityEdge>()
  for (const e of entities) {
    for (const c of e.children) {
      if (known.has(c.entity_id)) byId.set(c.relation_id, {
        relationId: c.relation_id, parentId: e.id, childId: c.entity_id,
        relationType: c.relation_type, stakePct: c.stake_pct, note: c.note,
      })
    }
    for (const p of e.parents) {
      if (known.has(p.entity_id)) byId.set(p.relation_id, {
        relationId: p.relation_id, parentId: p.entity_id, childId: e.id,
        relationType: p.relation_type, stakePct: p.stake_pct, note: p.note,
      })
    }
  }
  return [...byId.values()]
}

/** 最长路径分层：无母公司 = 0 层，否则 max(母公司层)+1。数据出现环时断环兜底，不死循环。 */
function computeDepths(ids: number[], parentsOf: Map<number, number[]>): Map<number, number> {
  const depth = new Map<number, number>()
  const visiting = new Set<number>()
  const dfs = (id: number): number => {
    const memo = depth.get(id)
    if (memo !== undefined) return memo
    if (visiting.has(id)) return 0
    visiting.add(id)
    const ps = parentsOf.get(id) ?? []
    const d = ps.length ? Math.max(...ps.map(dfs)) + 1 : 0
    visiting.delete(id)
    depth.set(id, d)
    return d
  }
  ids.forEach(dfs)
  return depth
}

/**
 * 构建股权图谱：只含有股权关系的主体（孤立主体不画），按连通分量返回，
 * 每个分量内节点已算好 (x, y)（母公司在上、子层居中、按母公司重心排序减少交叉）。
 */
export function buildEquityGraph(entities: PublisherEntity[]): EquityComponent[] {
  const allEdges = collectEdges(entities)
  // 只有控制级 + 品牌型关联（GROUP_EDGE_TYPES）参与「连通分量 / 分层 / 布局」；纯参股不并组。
  const structural = allEdges.filter(e => isGroupEdge(e.relationType))
  if (structural.length === 0) return []
  const byId = new Map(entities.map(e => [e.id, e]))

  // 无向邻接 → BFS 分连通分量（只用 structural 边，参股不连通）
  const adj = new Map<number, Set<number>>()
  const link = (a: number, b: number) => {
    if (!adj.has(a)) adj.set(a, new Set())
    adj.get(a)!.add(b)
  }
  for (const ed of structural) { link(ed.parentId, ed.childId); link(ed.childId, ed.parentId) }

  const seen = new Set<number>()
  const componentIds: number[][] = []
  for (const id of adj.keys()) {
    if (seen.has(id)) continue
    const queue = [id]
    const comp: number[] = []
    seen.add(id)
    while (queue.length) {
      const cur = queue.shift()!
      comp.push(cur)
      for (const nb of adj.get(cur) ?? []) {
        if (!seen.has(nb)) { seen.add(nb); queue.push(nb) }
      }
    }
    componentIds.push(comp)
  }

  const components: EquityComponent[] = []
  for (const ids of componentIds) {
    const idSet = new Set(ids)
    // 分层/布局只看 structural 边；参股不抬高子层、不决定母重心。
    const structEdges = structural.filter(e => idSet.has(e.parentId) && idSet.has(e.childId))
    // 渲染用全部组内边：含组内成员之间的参股（两端同组才画虚线；跨组参股不入图，只在卡上留链接）。
    const compEdges = allEdges.filter(e => idSet.has(e.parentId) && idSet.has(e.childId))
    const parentsOf = new Map<number, number[]>()
    for (const e of structEdges) {
      if (!parentsOf.has(e.childId)) parentsOf.set(e.childId, [])
      parentsOf.get(e.childId)!.push(e.parentId)
    }
    const depth = computeDepths(ids, parentsOf)

    const layers = new Map<number, number[]>()
    for (const id of ids) {
      const d = depth.get(id) ?? 0
      if (!layers.has(d)) layers.set(d, [])
      layers.get(d)!.push(id)
    }
    const maxDepth = Math.max(...layers.keys())

    // 逐层定 x：第 0 层按名称稳定排序，之后各层按母公司 x 重心排序
    const x = new Map<number, number>()
    const nameOf = (id: number) => byId.get(id)?.name ?? ''
    for (let d = 0; d <= maxDepth; d++) {
      const layer = layers.get(d) ?? []
      layer.sort((a, b) => nameOf(a).localeCompare(nameOf(b), 'zh'))
      if (d > 0) {
        const bary = (id: number) => {
          const ps = (parentsOf.get(id) ?? []).filter(p => x.has(p))
          if (ps.length === 0) return Number.MAX_SAFE_INTEGER
          return ps.reduce((s, p) => s + x.get(p)!, 0) / ps.length
        }
        layer.sort((a, b) => bary(a) - bary(b))
      }
      layer.forEach((id, i) => x.set(id, i * (NODE_W + H_GAP)))
    }

    // 居中各层到最宽层
    const width = Math.max(...[...layers.values()].map(l => l.length)) * (NODE_W + H_GAP) - H_GAP
    for (const [d, layer] of layers) {
      const layerW = layer.length * (NODE_W + H_GAP) - H_GAP
      const offset = (width - layerW) / 2
      for (const id of layer) x.set(id, x.get(id)! + offset)
      void d
    }

    const nodes: EquityNode[] = ids.map(id => {
      const e = byId.get(id)!
      const d = depth.get(id) ?? 0
      return {
        id, name: e.name, nameEn: e.name_en, tier: e.provenance_tier,
        depth: d, x: x.get(id)!, y: d * (NODE_H + V_GAP),
      }
    })
    components.push({
      nodes, edges: compEdges,
      width,
      height: (maxDepth + 1) * NODE_H + maxDepth * V_GAP,
    })
  }
  // 大分量在前
  components.sort((a, b) => b.nodes.length - a.nodes.length)
  return components
}
