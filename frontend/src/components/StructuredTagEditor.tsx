import { useQuery } from '@tanstack/react-query'
import { Asterisk, Calendar } from 'lucide-react'
import { tagsApi } from '../lib/api'
import { useT } from '../i18n'
import type { TagDimension, MaterialTagValueItem, MaterialTagValueInput } from '../lib/types'

/** 表单内的结构化标签选择态：维度 id → 已选 option(text) / 日期(date)。 */
export type TagValueState = Record<number, { optionIds: number[]; valueDate: string | null }>

export const emptyTagState = (): TagValueState => ({})

/** 把素材已打标记还原成表单态（编辑/重开表单时回填）。 */
export function tagStateFromItems(items: MaterialTagValueItem[] | undefined): TagValueState {
  const s: TagValueState = {}
  for (const it of items ?? []) {
    const cur = s[it.dimension_id] ?? { optionIds: [], valueDate: null }
    if (it.option_id != null) cur.optionIds = [...cur.optionIds, it.option_id]
    if (it.value_date) cur.valueDate = it.value_date
    s[it.dimension_id] = cur
  }
  return s
}

/** 表单态 → 提交体（空选的维度不发）。 */
export function tagStateToInputs(state: TagValueState): MaterialTagValueInput[] {
  const out: MaterialTagValueInput[] = []
  for (const [dimId, v] of Object.entries(state)) {
    const dimension_id = Number(dimId)
    if (v.optionIds.length) out.push({ dimension_id, option_ids: v.optionIds })
    else if (v.valueDate) out.push({ dimension_id, value_date: v.valueDate })
  }
  return out
}

/** 必填但未填的维度名（提交前本地校验，避免无谓 400）。 */
export function missingRequiredNames(dims: TagDimension[], state: TagValueState): string[] {
  return dims
    .filter(d => d.is_required)
    .filter(d => {
      const v = state[d.id]
      if (!v) return true
      return d.value_type === 'date' ? !v.valueDate : v.optionIds.length === 0
    })
    .map(d => d.name)
}

interface Props {
  materialType: string
  /** 当前素材所属产品（app_id）；传入后只展示作用域包含该产品的维度（S1）。 */
  appId?: string
  value: TagValueState
  onChange: (next: TagValueState) => void
}

/** 结构化打标签编辑器：按素材类型 + 产品作用域列出适用的一级标签。
 *  text 维度点选二级标签 (单/多选)，date 维度选日期。零 ST 配额（纯读本地标签库）。 */
export function StructuredTagEditor({ materialType, appId, value, onChange }: Props) {
  const t = useT()
  const tm = t.materials
  const { data: dims = [], isLoading } = useQuery({
    queryKey: ['tagDimensions', materialType || 'all', appId || 'any'],
    queryFn: () => tagsApi.listDimensions(materialType || undefined, appId || undefined),
  })

  if (isLoading || dims.length === 0) return null

  const toggleOption = (d: TagDimension, optId: number) => {
    const cur = value[d.id]?.optionIds ?? []
    let next: number[]
    if (d.allow_multi) {
      next = cur.includes(optId) ? cur.filter(x => x !== optId) : [...cur, optId]
    } else {
      next = cur.includes(optId) ? [] : [optId]  // 单选：再点取消
    }
    onChange({ ...value, [d.id]: { optionIds: next, valueDate: null } })
  }
  const setDate = (d: TagDimension, date: string) => {
    onChange({ ...value, [d.id]: { optionIds: [], valueDate: date || null } })
  }

  return (
    <div className="space-y-3 rounded-xl border border-default bg-elevated/30 p-4">
      <div className="eyebrow text-muted">{tm.structuredTagsLabel}</div>
      {dims.map(d => {
        const sel = value[d.id] ?? { optionIds: [], valueDate: null }
        return (
          <div key={d.id} className="space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-secondary">
              <span className="font-medium text-primary">{d.name}</span>
              {d.is_required && <Asterisk size={10} className="text-accent" />}
              <span className="text-[10px] text-muted">
                {d.value_type === 'date' ? tm.tagDateHint
                  : d.allow_multi ? tm.tagMultiHint : tm.tagSingleHint}
              </span>
            </div>
            {d.value_type === 'date' ? (
              <div className="flex items-center gap-2">
                <Calendar size={13} className="text-muted" />
                <input
                  type="date"
                  value={sel.valueDate ?? ''}
                  onChange={e => setDate(d, e.target.value)}
                  className="bg-surface border border-default rounded-lg px-2.5 py-1.5 text-xs text-primary focus:outline-none focus:border-accent [color-scheme:dark]"
                />
                {sel.valueDate && (
                  <button type="button" onClick={() => setDate(d, '')}
                    className="text-[11px] text-muted hover:text-red-400 transition-colors">
                    {t.common.clear}
                  </button>
                )}
              </div>
            ) : d.options.length === 0 ? (
              <p className="text-[11px] text-muted/70">{tm.tagNoOptions}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {d.options.map(o => {
                  const active = sel.optionIds.includes(o.id)
                  return (
                    <button type="button" key={o.id} onClick={() => toggleOption(d, o.id)}
                      className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${active
                        ? 'bg-accent/15 border-accent/40 text-accent'
                        : 'bg-surface border-default text-secondary hover:border-strong hover:text-primary'}`}>
                      {o.value}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
