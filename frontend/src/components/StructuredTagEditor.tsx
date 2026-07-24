import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Asterisk, Calendar, Layers } from 'lucide-react'
import { tagsApi } from '../lib/api'
import { useT } from '../i18n'
import { useLocalStorageState } from '../lib/hooks'
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

  // ── 标签包多选过滤：维度多的产品勾包收窄编辑器，不再一屏铺满全部标签 ──
  // 跟随该产品的包视图开关（与素材库同一开关、同一 queryKey 共享缓存）；
  // 不勾任何包 = 现状全显。注意：所有 hooks 必须在下方 early return 之前。
  const { data: packSetting } = useQuery({
    queryKey: ['tagPackSetting', appId],
    queryFn: () => tagsApi.getPackSetting(appId!),
    enabled: !!appId,
  })
  const { data: packs = [] } = useQuery({
    queryKey: ['tagPacks', appId],
    queryFn: () => tagsApi.listPacks(appId),
    enabled: !!appId && !!packSetting?.enabled,
  })
  // 勾选记忆按产品分槽存一个固定 key（hook 的 key 只在挂载时读一次，不能动态拼 appId）
  const [packSelMap, setPackSelMap] = useLocalStorageState<Record<string, number[]>>('mat.editorPacks', {})
  const packsOn = !!appId && !!packSetting?.enabled && packs.length > 0
  // 被删的包自动失效：与在世包求交集
  const selectedPackIds = useMemo(() => {
    if (!packsOn) return []
    const alive = new Set(packs.map(p => p.id))
    return (packSelMap[appId!] ?? []).filter(id => alive.has(id))
  }, [packsOn, packs, packSelMap, appId])
  const togglePack = (id: number) => {
    if (!appId) return
    const cur = packSelMap[appId] ?? []
    const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id]
    setPackSelMap({ ...packSelMap, [appId]: next })
  }
  const clearPacks = () => { if (appId) setPackSelMap({ ...packSelMap, [appId]: [] }) }
  // 勾了包 → 成员维度并集（整维度 + 选项子集的父维度，0047）+ 必填维度恒显
  // （否则提交被必填校验卡住却看不见字段）。选项级细化显示在切片 2。
  const visibleDims = useMemo(() => {
    if (!packsOn || selectedPackIds.length === 0) return dims
    const sel = packs.filter(p => selectedPackIds.includes(p.id))
    const member = new Set(sel.flatMap(p => p.dimension_ids))
    const optDim = new Map(dims.flatMap(d => d.options.map(o => [o.id, d.id] as const)))
    for (const oid of sel.flatMap(p => p.option_ids ?? [])) {
      const did = optDim.get(oid)
      if (did != null) member.add(did)
    }
    return dims.filter(d => member.has(d.id) || d.is_required)
  }, [dims, packs, packsOn, selectedPackIds])

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
      {/* 标签包多选过滤：勾包只展示成员标签（并集），必填标签始终显示 */}
      {packsOn && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-default/50 pb-2.5">
          <span className="flex items-center gap-1 text-[11px] text-muted">
            <Layers size={12} /> {tm.packSwitchLabel}
          </span>
          <button type="button" onClick={clearPacks}
            className={`px-2.5 py-0.5 rounded-md text-xs border transition-colors ${selectedPackIds.length === 0
              ? 'bg-accent/15 border-accent/40 text-accent'
              : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
            {tm.packAll}
          </button>
          {packs.map(p => {
            const active = selectedPackIds.includes(p.id)
            return (
              <button type="button" key={p.id} onClick={() => togglePack(p.id)} title={p.name}
                className={`px-2.5 py-0.5 rounded-md text-xs border transition-colors ${active
                  ? 'bg-accent/15 border-accent/40 text-accent'
                  : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
                {p.name}
              </button>
            )
          })}
          {selectedPackIds.length > 0 && (
            <span className="text-[10px] text-muted">{tm.editorPackHint}</span>
          )}
        </div>
      )}
      {visibleDims.map(d => {
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
