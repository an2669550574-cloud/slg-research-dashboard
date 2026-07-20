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

/** 文件名反解结果：解析出的表单态 + 没能归位的 token（前端高亮给人工处理）。 */
export interface ParsedNameTags {
  state: TagValueState
  unmatched: string[]
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}/

/** composeNameFromTags 的逆函数（P0，2026-07）：把规范文件名反解回结构化标签表单态。
 *
 * 买量团队的文件名本就按「维度段用 '_' 连、同维度多值用 '+' 连、日期在段位」编码
 * （与 compose 同一套约定），此前只有标签→标题方向，标题→标签靠人肉重录。
 *
 * 规则：
 * - 先按 separator 切段；段内**不能**裸 split('+')——选项值本身可含 '+' / '*'
 *   （真实词表：'+1门（+n门）'、'*人数门'、'+人数桶'），改用词表贪心分词：
 *   每个位置先试日期、再按选项值**从长到短**前缀匹配，都不中且当前是 '+' 则视为
 *   连接符跳过，再不中就吞到下一个 '+' 为一个 unmatched token。
 * - 'YYYY-MM-DD' 归第一个未填的 date 维度。
 * - 同一选项值出现在多个维度时：优先本段已命中的维度 → 未填的维度（按维度序）→ 首个候选。
 * - 单选维度已有值再遇到不同值 → 后者进 unmatched（不静默覆盖）。
 * - 别名两档（200 条 prod 语料回放驱动，2026-07-20）：
 *   ① 括号注释省写：'+1门（+n门）' 的文件名常写 '+1门'、'金像（泥像）' 写 '金像'——
 *      去括号后注册为别名（与既有值冲突则不注册）；
 *   ② 单字变体容错：'无搓-1boss' vs 词表 '无挫-1boss'（搓/挫混写）——整 token 等长、
 *      恰差 1 字、长度 ≥3 且唯一命中才采纳（防短 token 误吸）。
 * - 纯确定性、零 LLM；解析结果是「预填建议」，提交前仍由人眼核对。CJK 友好。 */
export function parseTagsFromName(
  name: string,
  dims: TagDimension[],
  separator = '_',
): ParsedNameTags {
  const state: TagValueState = {}
  const unmatched: string[] = []
  const dateDims = dims.filter(d => d.value_type === 'date')

  // 词表：选项值 → 候选 (维度, 选项)；同值跨维度保留全部候选，匹配用长度降序。
  const vocab = new Map<string, { dimId: number; optId: number }[]>()
  for (const d of dims) {
    if (d.value_type !== 'text') continue
    for (const o of d.options) {
      if (!o.value) continue
      const arr = vocab.get(o.value) ?? []
      arr.push({ dimId: d.id, optId: o.id })
      vocab.set(o.value, arr)
    }
  }
  // 别名①：去掉全角括号注释的省写形（'+1门（+n门）'→'+1门'）；与既有键冲突则不注册。
  for (const [full, cands] of [...vocab.entries()]) {
    const short = full.replace(/（[^）]*）/g, '').trim()
    if (short && short !== full && !vocab.has(short)) vocab.set(short, cands)
  }
  const values = [...vocab.keys()].sort((a, b) => b.length - a.length)

  // 别名②：整 token 单字变体容错（等长、恰差 1 字、len≥3、唯一命中）。
  const fuzzyLookup = (token: string) => {
    if (token.length < 3) return null
    let found: string | null = null
    for (const v of values) {
      if (v.length !== token.length) continue
      let diff = 0
      for (let k = 0; k < v.length && diff < 2; k++) if (v[k] !== token[k]) diff++
      if (diff === 1) {
        if (found) return null // 多个候选=歧义，放弃
        found = v
      }
    }
    return found
  }

  const dimOrder = dims.map(d => d.id)
  const dimById = new Map(dims.map(d => [d.id, d]))

  const assignOption = (token: string, segmentDims: Set<number>) => {
    const candidates = vocab.get(token)!
    // 消歧：本段已命中的维度 > 尚未填的维度（按维度序）> 首个候选
    const pick =
      candidates.find(c => segmentDims.has(c.dimId)) ??
      candidates
        .slice()
        .sort((a, b) => dimOrder.indexOf(a.dimId) - dimOrder.indexOf(b.dimId))
        .find(c => !state[c.dimId]?.optionIds.length) ??
      candidates[0]
    const dim = dimById.get(pick.dimId)!
    const cur = state[pick.dimId] ?? { optionIds: [], valueDate: null }
    if (cur.optionIds.includes(pick.optId)) return true // 重复值幂等
    if (!dim.allow_multi && cur.optionIds.length > 0) return false // 单选冲突
    state[pick.dimId] = { optionIds: [...cur.optionIds, pick.optId], valueDate: null }
    segmentDims.add(pick.dimId)
    return true
  }

  for (const rawSeg of name.split(separator)) {
    const seg = rawSeg.trim()
    if (!seg) continue
    const segmentDims = new Set<number>() // 本段已命中的维度（'+' 连的多值大概率同维度）
    let i = 0
    while (i < seg.length) {
      if (seg[i] === '+') {
        // 可能是连接符，也可能是 '+1门' 这类值的首字符——先试选项，试不中再当连接符
        const hit = values.find(v => seg.startsWith(v, i))
        if (hit) {
          if (!assignOption(hit, segmentDims)) unmatched.push(hit)
          i += hit.length
          continue
        }
        i += 1
        continue
      }
      const dm = seg.slice(i).match(DATE_RE)
      if (dm) {
        const target = dateDims.find(d => !state[d.id]?.valueDate)
        if (target) state[target.id] = { optionIds: [], valueDate: dm[0] }
        else unmatched.push(dm[0])
        i += dm[0].length
        continue
      }
      const hit = values.find(v => seg.startsWith(v, i))
      if (hit) {
        if (!assignOption(hit, segmentDims)) unmatched.push(hit)
        i += hit.length
        continue
      }
      // 词表不认识：吞到下一个 '+'（或段尾）作为一个 token，末次机会走单字容错
      const next = seg.indexOf('+', i + 1)
      const token = (next === -1 ? seg.slice(i) : seg.slice(i, next)).trim()
      if (token) {
        const fz = fuzzyLookup(token)
        if (fz) {
          if (!assignOption(fz, segmentDims)) unmatched.push(token)
        } else unmatched.push(token)
      }
      i = next === -1 ? seg.length : next
    }
  }
  return { state, unmatched }
}

/** 逐维度合并两份表单态：override 里填了的维度整体覆盖 base（解析值优先、共享编辑器补缺）。 */
export function mergeTagStates(base: TagValueState, override: TagValueState): TagValueState {
  const out: TagValueState = { ...base }
  for (const [dimId, v] of Object.entries(override)) {
    if (v.optionIds.length || v.valueDate) out[Number(dimId)] = v
  }
  return out
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
