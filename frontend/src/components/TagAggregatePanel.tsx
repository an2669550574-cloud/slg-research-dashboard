import { useEffect, useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { BarChart3, ChevronDown } from 'lucide-react'
import { tagsApi } from '../lib/api'
import { useT } from '../i18n'
import type { TagDimension } from '../lib/types'

/** 聚合分析面板（P4）：按某文字型一级标签统计当前筛选范围内的去重素材分布，
 * 可选第二维度做交叉透视。scope 跟随素材列表的 material_type + 分面筛选，
 * 纯本地 SQLite 聚合、零 Sensor Tower 配额。dims 为「文字型且有二级标签」的维度。 */
export function TagAggregatePanel({
  dims, materialType, tagOptions,
}: {
  dims: TagDimension[]
  materialType?: string
  tagOptions?: string
}) {
  const t = useT()
  const m = t.materials
  const [open, setOpen] = useState(false)
  const [primaryId, setPrimaryId] = useState(0)
  const [byId, setById] = useState(0)

  // 维度集合随 material_type 变化（通用 + 该类型）：选中项失效时回落到首个 / 无。
  useEffect(() => {
    const ids = new Set(dims.map(d => d.id))
    if (!ids.has(primaryId)) setPrimaryId(dims[0]?.id ?? 0)
    if (byId && !ids.has(byId)) setById(0)
  }, [dims, primaryId, byId])

  // 交叉维度不能与主维度相同：撞了就清掉。
  useEffect(() => { if (byId && byId === primaryId) setById(0) }, [byId, primaryId])

  const { data: agg, isFetching } = useQuery({
    queryKey: ['tagAggregate', primaryId, byId, materialType || 'all', tagOptions || ''],
    queryFn: () => tagsApi.aggregate({
      dimension_id: primaryId,
      by: byId || undefined,
      material_type: materialType || undefined,
      tag_options: tagOptions || undefined,
    }),
    enabled: open && primaryId > 0,
    placeholderData: keepPreviousData,
  })

  const maxCount = useMemo(
    () => Math.max(1, ...(agg?.buckets ?? []).map(b => b.count)),
    [agg],
  )
  const hasData = !!agg && agg.buckets.some(b => b.count > 0)

  if (dims.length === 0) return null

  const selectClass = "bg-elevated border border-default rounded-lg px-2.5 py-1.5 text-xs text-primary focus:outline-none focus:border-brand-500"

  return (
    <div className="rounded-lg border border-default/60 bg-surface/40">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
      >
        <BarChart3 size={14} className="text-accent" />
        <span className="text-xs font-medium text-secondary">{m.aggTitle}</span>
        <span className="text-[11px] text-muted hidden sm:inline">· {m.aggHint}</span>
        <ChevronDown size={14} className={`ml-auto text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="border-t border-default/60 px-3 py-3 space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-[11px] text-secondary">
              {m.aggPrimary}
              <select value={primaryId} onChange={e => setPrimaryId(Number(e.target.value))} className={selectClass}>
                {dims.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 text-[11px] text-secondary">
              {m.aggBy}
              <select value={byId} onChange={e => setById(Number(e.target.value))} className={selectClass}>
                <option value={0}>{m.aggByNone}</option>
                {dims.filter(d => d.id !== primaryId).map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
          </div>

          {agg && (
            <p className="text-[11px] text-muted">{m.aggSummary(agg.total_materials, agg.tagged_materials)}</p>
          )}

          {!hasData ? (
            <p className="text-xs text-muted py-2">{m.aggEmpty}</p>
          ) : agg!.by_dimension_id ? (
            <CrossTab agg={agg!} />
          ) : (
            <div className={`space-y-1.5 ${isFetching ? 'opacity-60' : ''}`}>
              {agg!.buckets.map(b => (
                <div key={b.option_id} className="flex items-center gap-2">
                  <span className="w-16 shrink-0 truncate text-[11px] text-secondary" title={b.value}>{b.value}</span>
                  <div className="relative h-4 flex-1 rounded bg-elevated/60 overflow-hidden">
                    <div className="h-full rounded bg-accent/70" style={{ width: `${(b.count / maxCount) * 100}%` }} />
                  </div>
                  <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-primary">{b.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 交叉透视矩阵：行=主维度二级标签，列=第二维度二级标签，单元=去重素材数（热度浅染）。 */
function CrossTab({ agg }: { agg: import('../lib/types').TagAggregateOut }) {
  const t = useT()
  const cols = agg.buckets[0]?.sub ?? []
  const maxCell = Math.max(1, ...agg.buckets.flatMap(b => (b.sub ?? []).map(s => s.count)))
  const heat = (n: number) => {
    if (!n) return ''
    const r = n / maxCell
    if (r <= 0.25) return 'bg-accent/10'
    if (r <= 0.5) return 'bg-accent/20'
    if (r <= 0.75) return 'bg-accent/30'
    return 'bg-accent/40'
  }
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] border-collapse">
        <thead>
          <tr className="text-secondary">
            <th className="px-2 py-1 text-left font-medium sticky left-0 bg-surface/40">
              {agg.dimension_name} \ {agg.by_dimension_name}
            </th>
            {cols.map(c => <th key={c.option_id} className="px-2 py-1 text-center font-medium min-w-[44px]">{c.value}</th>)}
            <th className="px-2 py-1 text-center font-medium text-muted">{t.materials.aggColTotal}</th>
          </tr>
        </thead>
        <tbody>
          {agg.buckets.map(b => (
            <tr key={b.option_id} className="border-t border-default/40">
              <td className="px-2 py-1 text-secondary sticky left-0 bg-surface/40 whitespace-nowrap">{b.value}</td>
              {(b.sub ?? []).map(s => (
                <td key={s.option_id} className={`px-2 py-1 text-center tabular-nums ${heat(s.count)} ${s.count ? 'text-primary' : 'text-muted'}`}>
                  {s.count || '·'}
                </td>
              ))}
              <td className="px-2 py-1 text-center tabular-nums text-muted">{b.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
