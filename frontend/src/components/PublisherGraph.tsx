import { useMemo, useState } from 'react'
import { useT } from '../i18n'
import type { PublisherEntity, PublisherRelationType } from '../lib/types'
import { buildEquityGraph, NODE_W, NODE_H, type EquityComponent } from '../lib/equityGraph'

const PAD = 16

// 边样式按关系强度递减：全资最重、关联最轻
const EDGE_STYLE: Record<PublisherRelationType, { width: number; dash?: string }> = {
  wholly_owned: { width: 2 },
  controlling: { width: 1.5 },
  minority: { width: 1.5, dash: '5 4' },
  affiliate: { width: 1.2, dash: '2 3' },
}

const TIER_STROKE = {
  primary: 'stroke-emerald-500/60',
  secondary: 'stroke-amber-500/60',
  none: '',
} as const

/** 股权关系图谱：只画有关系的主体，按连通分量分块，母公司在上。点节点回列表看档案。 */
export function PublisherGraph({ entities, onSelectEntity }: {
  entities: PublisherEntity[]
  onSelectEntity: (id: number) => void
}) {
  const t = useT()
  const tt = t.publishersManage
  const components = useMemo(() => buildEquityGraph(entities), [entities])

  if (components.length === 0) {
    return (
      <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl px-6">
        {tt.graphEmpty}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* 图例：边=关系强度，节点边框=溯源档位 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted">
        <span>{tt.graphHint}</span>
        <span className="flex items-center gap-3">
          {(Object.keys(EDGE_STYLE) as PublisherRelationType[]).map(rt => (
            <span key={rt} className="inline-flex items-center gap-1.5">
              <svg width="24" height="6" aria-hidden>
                <line x1="0" y1="3" x2="24" y2="3"
                  style={{ stroke: 'rgb(var(--text-secondary))' }}
                  strokeWidth={EDGE_STYLE[rt].width} strokeDasharray={EDGE_STYLE[rt].dash} />
              </svg>
              {tt.relationTypes[rt]}
            </span>
          ))}
        </span>
        <span className="inline-flex items-center gap-2">
          {tt.graphLegendNode}
          <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-emerald-500/60 inline-block" />{tt.provPrimary}</span>
          <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-amber-500/60 inline-block" />{tt.provSecondary}</span>
        </span>
      </div>

      <div className="flex flex-wrap gap-3">
        {components.map((c, i) => (
          <GraphComponent key={c.nodes[0]?.id ?? i} component={c} onSelectEntity={onSelectEntity} />
        ))}
      </div>
    </div>
  )
}

function GraphComponent({ component, onSelectEntity }: {
  component: EquityComponent
  onSelectEntity: (id: number) => void
}) {
  const t = useT()
  const tt = t.publishersManage
  const [hovered, setHovered] = useState<number | null>(null)
  const pos = useMemo(
    () => new Map(component.nodes.map(n => [n.id, n])),
    [component],
  )

  const w = component.width + PAD * 2
  const h = component.height + PAD * 2
  const isDim = (a: number, b: number) =>
    hovered !== null && hovered !== a && hovered !== b

  return (
    <div className="bg-surface border border-default rounded-xl p-2 overflow-x-auto max-w-full">
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img">
        {component.edges.map(e => {
          const p = pos.get(e.parentId)!
          const c = pos.get(e.childId)!
          const x0 = p.x + NODE_W / 2 + PAD
          const y0 = p.y + NODE_H + PAD
          const x1 = c.x + NODE_W / 2 + PAD
          const y1 = c.y + PAD
          const bend = Math.max(24, (y1 - y0) / 2)
          const style = EDGE_STYLE[e.relationType]
          const label = `${tt.relationTypes[e.relationType]}${e.stakePct != null ? ` ${e.stakePct}%` : ''}`
          const dim = isDim(e.parentId, e.childId)
          return (
            <g key={e.relationId} opacity={dim ? 0.18 : 1} className="transition-opacity">
              {/* 原生 tooltip：hover 边/标签可见调研备注（如回购退出史、持股口径） */}
              <title>{e.note ? `${label}\n${e.note}` : label}</title>
              <path
                d={`M ${x0} ${y0} C ${x0} ${y0 + bend}, ${x1} ${y1 - bend}, ${x1} ${y1}`}
                fill="none"
                style={{ stroke: 'rgb(var(--text-secondary))' }}
                strokeWidth={style.width}
                strokeDasharray={style.dash}
              />
              {/* 箭头：指向子公司 */}
              <path
                d={`M ${x1 - 4} ${y1 - 7} L ${x1} ${y1} L ${x1 + 4} ${y1 - 7}`}
                fill="none"
                style={{ stroke: 'rgb(var(--text-secondary))' }}
                strokeWidth={style.width}
              />
              <text
                x={(x0 + x1) / 2} y={(y0 + y1) / 2 - 4}
                textAnchor="middle" fontSize="10"
                style={{
                  fill: 'rgb(var(--text-secondary))',
                  stroke: 'rgb(var(--bg-surface))', strokeWidth: 3, paintOrder: 'stroke',
                  // 有备注的边：点状下划线提示可 hover
                  ...(e.note ? { textDecoration: 'underline dotted' } : {}),
                }}
              >
                {label}
              </text>
            </g>
          )
        })}
        {component.nodes.map(n => {
          const dim = hovered !== null && hovered !== n.id
            && !component.edges.some(e =>
              (e.parentId === hovered && e.childId === n.id) || (e.childId === hovered && e.parentId === n.id))
          return (
            <g
              key={n.id}
              transform={`translate(${n.x + PAD}, ${n.y + PAD})`}
              className="cursor-pointer"
              opacity={dim ? 0.3 : 1}
              onMouseEnter={() => setHovered(n.id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSelectEntity(n.id)}
            >
              {/* 节点全名 tooltip（卡片内名称可能被截断） */}
              <title>{n.nameEn ? `${n.name}\n${n.nameEn}` : n.name}</title>
              <rect
                width={NODE_W} height={NODE_H} rx="10"
                className={`transition-all ${TIER_STROKE[n.tier]}`}
                style={{
                  fill: 'rgb(var(--bg-elevated))',
                  ...(n.tier === 'none' ? { stroke: 'rgb(var(--border-default))' } : {}),
                }}
                strokeWidth={hovered === n.id ? 2 : 1.2}
              />
              <text
                x={NODE_W / 2} y={n.nameEn ? 20 : NODE_H / 2 + 4}
                textAnchor="middle" fontSize="12" fontWeight="600"
                style={{ fill: 'rgb(var(--text-primary))' }}
              >
                {n.name.length > 11 ? `${n.name.slice(0, 10)}…` : n.name}
              </text>
              {n.nameEn && (
                <text
                  x={NODE_W / 2} y={36}
                  textAnchor="middle" fontSize="9"
                  style={{ fill: 'rgb(var(--text-muted))' }}
                >
                  {n.nameEn.length > 24 ? `${n.nameEn.slice(0, 23)}…` : n.nameEn}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
