import { useMemo } from 'react'
import { useT } from '../i18n'
import { Building2, CornerDownRight, Globe, Landmark, ShieldCheck } from 'lucide-react'
import { GameIcon } from './GameIcon'
import { isGroupEdge } from '../lib/equityGraph'
import type { PublisherEntity, PublisherRelationType } from '../lib/types'

/** 资本树视角：以顶层资本方/母体为根的缩进清单，按「谁的资本系」快速浏览。
 *
 *  与 SVG 图谱互补——图谱看拓扑，这里看档案：每行带关系 chip（类型+持股%）、
 *  旗下产品 icon、溯源盾，点行进详情抽屉。多母公司的主体只挂在「最强关系」的
 *  母公司下，其余母公司处显示灰字引用行（不重复展开防双计）；DFS 带 visited 防环。
 *  并组口径与集团 tab / 股权图谱共用 GROUP_EDGE_TYPES：只有控制级 + 品牌型关联进树，
 *  纯财务参股（minority）不嵌套——只在详情卡留关联链接。无并组关系的主体归底部
 *  「独立厂商」组（含仅有参股关系的主体）。纯前端、零新端点。
 */

// 关系强度排序：多母公司时挂最强的一条下面
const REL_RANK: Record<PublisherRelationType, number> = {
  wholly_owned: 0, controlling: 1, minority: 2, affiliate: 3,
}

interface TreeRow {
  entity: PublisherEntity
  depth: number
  /** 来自主挂母公司的那条关系（根节点为 null） */
  rel: { relationType: PublisherRelationType; stakePct: number | null; note: string | null } | null
  /** true = 灰字引用行：该主体的主位置在另一棵分支下 */
  ghost: boolean
}

function buildTrees(entities: PublisherEntity[]): { trees: TreeRow[][]; independents: PublisherEntity[] } {
  const byId = new Map(entities.map(e => [e.id, e]))
  // 入树口径 = 并组口径：只数控制级 + 品牌型关联（GROUP_EDGE_TYPES），参股不算入树。
  const inGraph = new Set<number>()
  for (const e of entities) {
    const has = (links: { entity_id: number; relation_type: PublisherRelationType }[]) =>
      links.some(l => isGroupEdge(l.relation_type) && byId.has(l.entity_id))
    if (has(e.parents) || has(e.children)) inGraph.add(e.id)
  }

  // 每个主体的「主挂母公司」= 并组关系里最强的一条（同强度取 relation_id 小的，稳定）；参股不挂。
  const primaryParent = new Map<number, number>()
  for (const e of entities) {
    const ps = e.parents
      .filter(p => isGroupEdge(p.relation_type) && byId.has(p.entity_id))
      .sort((a, b) => REL_RANK[a.relation_type] - REL_RANK[b.relation_type] || a.relation_id - b.relation_id)
    if (ps.length) primaryParent.set(e.id, ps[0].entity_id)
  }

  // 根 = 图内且无母公司的节点；排序：子树大的在前，同大小资本方优先
  const subtreeSize = (id: number, seen: Set<number>): number => {
    if (seen.has(id)) return 0
    seen.add(id)
    const e = byId.get(id)
    if (!e) return 0
    return 1 + e.children
      .filter(c => byId.has(c.entity_id) && primaryParent.get(c.entity_id) === id)
      .reduce((s, c) => s + subtreeSize(c.entity_id, seen), 0)
  }
  const roots = entities
    .filter(e => inGraph.has(e.id) && !primaryParent.has(e.id))
    .sort((a, b) => subtreeSize(b.id, new Set()) - subtreeSize(a.id, new Set())
      || Number(a.is_slg) - Number(b.is_slg) || a.id - b.id)

  const trees: TreeRow[][] = []
  for (const root of roots) {
    const rows: TreeRow[] = []
    const visited = new Set<number>()
    const walk = (e: PublisherEntity, depth: number, rel: TreeRow['rel']) => {
      if (visited.has(e.id)) return
      visited.add(e.id)
      rows.push({ entity: e, depth, rel, ghost: false })
      // 只展开并组关系的子节点；参股子公司不进树（在自己的资本系或独立厂区里出现）。
      const kids = e.children
        .filter(c => isGroupEdge(c.relation_type) && byId.has(c.entity_id))
        .sort((a, b) => REL_RANK[a.relation_type] - REL_RANK[b.relation_type] || a.relation_id - b.relation_id)
      for (const c of kids) {
        const child = byId.get(c.entity_id)!
        const relInfo = { relationType: c.relation_type, stakePct: c.stake_pct, note: c.note }
        if (primaryParent.get(c.entity_id) === e.id) {
          walk(child, depth + 1, relInfo)
        } else {
          // 非主挂母公司：灰字引用，不展开（主位置在别的分支）
          rows.push({ entity: child, depth: depth + 1, rel: relInfo, ghost: true })
        }
      }
    }
    walk(root, 0, null)
    trees.push(rows)
  }

  const independents = entities.filter(e => !inGraph.has(e.id))
  return { trees, independents }
}

export function PublisherCapitalTree({ entities, onSelectEntity }: {
  entities: PublisherEntity[]
  onSelectEntity: (id: number) => void
}) {
  const t = useT()
  const tt = t.publishersManage
  const { trees, independents } = useMemo(() => buildTrees(entities), [entities])

  if (trees.length === 0 && independents.length === 0) {
    return (
      <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl px-6">
        {tt.graphEmpty}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-muted">{tt.treeHint}</div>
      <div className="grid gap-3 lg:grid-cols-2">
        {trees.map(rows => (
          <TreeCard key={rows[0].entity.id} rows={rows} onSelectEntity={onSelectEntity} />
        ))}
      </div>
      {independents.length > 0 && (
        <div className="bg-surface border border-default rounded-xl p-4">
          <div className="text-[11px] text-muted uppercase tracking-wider mb-2.5">
            {tt.treeIndependents(independents.length)}
          </div>
          <div className="flex flex-wrap gap-2">
            {independents.map(e => (
              <button
                key={e.id}
                onClick={() => onSelectEntity(e.id)}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs bg-elevated border border-default text-secondary hover:text-primary hover:border-strong transition-colors"
              >
                <Building2 size={11} className="text-accent" />
                {e.name}
                {!!e.product_count && <span className="font-data text-[10px] text-muted">{e.product_count}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TreeCard({ rows, onSelectEntity }: { rows: TreeRow[]; onSelectEntity: (id: number) => void }) {
  const t = useT()
  const tt = t.publishersManage
  const root = rows[0].entity
  const rootCap = !root.is_slg

  return (
    <div className="bg-surface border border-default rounded-xl overflow-hidden self-start">
      {/* 树头：根主体 */}
      <button
        onClick={() => onSelectEntity(root.id)}
        className="w-full flex items-center gap-2.5 px-4 py-3 border-b border-default bg-elevated/40 hover:bg-elevated/70 transition-colors text-left"
      >
        <span className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${rootCap ? 'bg-amber-500/10' : 'bg-accent/10'}`}>
          {rootCap ? <Landmark size={15} className="text-amber-500" /> : <Building2 size={15} className="text-accent" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="font-display font-bold text-primary truncate">{root.name}</span>
            {root.hq_region && root.hq_region !== '国内' && (
              <span className="inline-flex items-center gap-0.5 text-[10px] text-secondary shrink-0"><Globe size={10} />{root.hq_region}</span>
            )}
          </div>
          {root.name_en && <div className="text-[11px] text-muted truncate">{root.name_en}</div>}
        </div>
        <span className="font-data text-[10px] text-muted shrink-0">{tt.treeMembers(rows.filter(r => !r.ghost).length)}</span>
      </button>

      {/* 树身：缩进行 */}
      <div className="py-1">
        {rows.slice(1).map((r, i) => (
          <TreeRowItem key={`${r.entity.id}-${r.ghost ? 'g' : 'm'}-${i}`} row={r} onSelectEntity={onSelectEntity} />
        ))}
        {rows.length === 1 && (
          <div className="px-4 py-2 text-[11px] text-muted">{tt.treeNoChildren}</div>
        )}
      </div>
    </div>
  )
}

function TreeRowItem({ row, onSelectEntity }: { row: TreeRow; onSelectEntity: (id: number) => void }) {
  const t = useT()
  const tt = t.publishersManage
  const e = row.entity
  const cap = !e.is_slg
  const relLabel = row.rel
    ? `${tt.relationTypes[row.rel.relationType]}${row.rel.stakePct != null ? ` ${row.rel.stakePct}%` : ''}`
    : ''

  return (
    <button
      onClick={() => onSelectEntity(e.id)}
      className={`w-full flex items-center gap-2 px-4 py-1.5 text-left hover:bg-elevated/50 transition-colors ${row.ghost ? 'opacity-50' : ''}`}
      style={{ paddingLeft: `${16 + (row.depth - 1) * 22}px` }}
    >
      <CornerDownRight size={12} className="text-muted shrink-0" />
      {cap
        ? <Landmark size={12} className="text-amber-500 shrink-0" />
        : <Building2 size={12} className="text-accent shrink-0" />}
      <span className={`text-sm truncate ${row.ghost ? 'text-muted' : 'text-primary'}`}>{e.name}</span>
      {row.rel && (
        <span
          title={row.rel.note ?? undefined}
          className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-data bg-elevated border border-default text-secondary ${row.rel.note ? 'underline decoration-dotted underline-offset-2' : ''}`}
        >
          {relLabel}
        </span>
      )}
      {row.ghost && <span className="shrink-0 text-[10px] text-muted">{tt.treeSeeAlso}</span>}
      <span className="flex-1" />
      {!row.ghost && e.top_products.slice(0, 3).map(p => (
        <GameIcon key={p.app_id} src={p.icon_url} name={p.name ?? p.app_id} className="w-5 h-5 rounded shrink-0" />
      ))}
      {!row.ghost && !!e.product_count && e.product_count > 0 && (
        <span className="font-data text-[10px] text-muted shrink-0">{e.product_count}</span>
      )}
      {!row.ghost && (
        <ShieldCheck
          size={12}
          className={`shrink-0 ${e.provenance_tier === 'primary' ? 'text-emerald-400' : e.provenance_tier === 'secondary' ? 'text-amber-500' : 'text-muted/40'}`}
        />
      )}
    </button>
  )
}
