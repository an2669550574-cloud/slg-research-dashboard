import type { TagDimension } from './types'
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
