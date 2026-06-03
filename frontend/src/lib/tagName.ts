import type { TagDimension, MaterialTagValueItem } from './types'
import type { TagValueState } from '../components/StructuredTagEditor'

/** 自动命名（P5）：把素材已选的结构化标签按维度顺序拼成标题。模板化、零 LLM。
 *
 * 规则：
 * - 维度顺序沿用标签库的 sort_order（dims 已按序到达，逐维取段）。
 * - text 维度取所选二级标签的「值」，按维度内二级标签展示顺序排列，多选用 '+' 连接。
 * - date 维度取所选日期（ISO 串）。
 * - 未选 / 空的维度跳过；各维度段用 separator（默认 '_'）连接。
 * 全空时返回 ''（调用方据此禁用按钮）。CJK 友好：纯字符串拼接，不做 ASCII 假设。 */
export function composeNameFromTags(
  state: TagValueState,
  dims: TagDimension[],
  separator = '_',
): string {
  const segments: string[] = []
  for (const d of dims) {
    const v = state[d.id]
    if (!v) continue
    if (d.value_type === 'date') {
      if (v.valueDate) segments.push(v.valueDate)
    } else if (v.optionIds.length) {
      const vals = d.options.filter(o => v.optionIds.includes(o.id)).map(o => o.value)
      if (vals.length) segments.push(vals.join('+'))
    }
  }
  return segments.join(separator)
}

/** 从素材自身已打的结构化标签（tag_values）直接拼标题——卡片一键 / 批量「按标签命名」用。
 *  无需再取维度表：按 tag_values 到达顺序（后端已按维度 sort_order 返回）分组，
 *  同一维度的多个值用 '+' 连，维度间用 separator。date 取 value_date，text 取 value。
 *  与 composeNameFromTags 同构，CJK 友好；无可用标签时返回 ''。 */
export function composeNameFromTagValues(
  items: MaterialTagValueItem[],
  separator = '_',
): string {
  const order: number[] = []
  const byDim = new Map<number, string[]>()
  for (const it of items) {
    const seg = it.value_type === 'date' ? (it.value_date ?? '') : (it.value ?? '')
    if (!seg) continue
    if (!byDim.has(it.dimension_id)) { byDim.set(it.dimension_id, []); order.push(it.dimension_id) }
    byDim.get(it.dimension_id)!.push(seg)
  }
  return order.map(id => byDim.get(id)!.join('+')).join(separator)
}
