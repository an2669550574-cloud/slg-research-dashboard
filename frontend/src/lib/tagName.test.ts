import { describe, it, expect } from 'vitest'
import { composeNameFromTags, composeNameFromTagValues, parseTagsFromName, mergeTagStates } from './tagName'
import type { TagDimension, MaterialTagValueItem } from './types'
import type { TagValueState } from '../components/StructuredTagEditor'

// 中文标签库夹具：路型(单/多选 text) / 桶子(text) / 投放时间(date)。
const opt = (id: number, value: string, dimension_id: number): TagDimension['options'][number] =>
  ({ id, dimension_id, value, sort_order: 0, created_at: '2026-06-02T00:00:00', app_ids: [] })

const dim = (id: number, name: string, value_type: 'text' | 'date', options: TagDimension['options']): TagDimension =>
  ({ id, name, value_type, material_type: null, is_required: false, allow_multi: true, sort_order: id, created_at: '2026-06-02T00:00:00', options, app_ids: [] })

const road = dim(2, '路型', 'text', [opt(11, '1路', 2), opt(12, '2路', 2), opt(13, '3路', 2)])
const bucket = dim(3, '桶子', 'text', [opt(21, '红桶', 3), opt(22, '蓝桶', 3)])
const launch = dim(1, '投放时间', 'date', [])
// dims 按 sort_order 到达：投放时间(1) → 路型(2) → 桶子(3)
const DIMS = [launch, road, bucket]

describe('composeNameFromTags', () => {
  it('按维度顺序拼接，跨维度用分隔符', () => {
    const state: TagValueState = {
      1: { optionIds: [], valueDate: '2026-06-02' },
      2: { optionIds: [12], valueDate: null },
      3: { optionIds: [21], valueDate: null },
    }
    expect(composeNameFromTags(state, DIMS)).toBe('2026-06-02_2路_红桶')
  })

  it('同维度多选用 + 连接，且按二级标签展示顺序（非点选顺序）', () => {
    const state: TagValueState = { 2: { optionIds: [13, 11], valueDate: null } }
    expect(composeNameFromTags(state, DIMS)).toBe('1路+3路')
  })

  it('跳过未选 / 空维度', () => {
    const state: TagValueState = {
      2: { optionIds: [11], valueDate: null },
      3: { optionIds: [], valueDate: null },
    }
    expect(composeNameFromTags(state, DIMS)).toBe('1路')
  })

  it('支持自定义分隔符', () => {
    const state: TagValueState = {
      2: { optionIds: [11], valueDate: null },
      3: { optionIds: [22], valueDate: null },
    }
    expect(composeNameFromTags(state, DIMS, '-')).toBe('1路-蓝桶')
  })

  it('全空返回空串', () => {
    expect(composeNameFromTags({}, DIMS)).toBe('')
    expect(composeNameFromTags({ 2: { optionIds: [], valueDate: null } }, DIMS)).toBe('')
  })
})

// 卡片/批量一键命名：直接吃素材自身 tag_values（后端已按维度序返回）
const tv = (
  dimension_id: number, dimension_name: string,
  value_type: 'text' | 'date', value: string | null, value_date: string | null = null,
): MaterialTagValueItem =>
  ({ dimension_id, dimension_name, value_type, option_id: null, value, value_date })

describe('composeNameFromTagValues', () => {
  it('按到达顺序分组维度，同维度多值用 + 连，维度间用 _', () => {
    const items = [
      tv(1, '投放时间', 'date', null, '2026-03-01'),
      tv(2, '角色', 'text', '机枪兵'),
      tv(2, '角色', 'text', '机甲'),
      tv(3, '路型', 'text', '3路'),
    ]
    expect(composeNameFromTagValues(items)).toBe('2026-03-01_机枪兵+机甲_3路')
  })

  it('跳过空值，支持自定义分隔符', () => {
    const items = [
      tv(1, '投放时间', 'date', null, null),
      tv(2, '桶子', 'text', '金像'),
    ]
    expect(composeNameFromTagValues(items, '-')).toBe('金像')
  })

  it('空数组返回空串', () => {
    expect(composeNameFromTagValues([])).toBe('')
  })
})

// ── 文件名反解（P0）：夹具按 prod 真实词表的坑造——选项值可含 '+' / '*' / '-' ──
const gate = dim(4, '增长门类型', 'text', [
  opt(31, '+1门（+n门）', 4), opt(32, '增长门', 4), opt(33, '*人数门', 4), opt(34, '无门', 4),
])
const flow = dim(5, '心流', 'text', [opt(41, '1搓', 5), opt(42, '无挫-1boss', 5), opt(43, '无挫-爽秒', 5)])
const roleSingle: TagDimension = { ...dim(6, '角色', 'text', [opt(51, 'AK', 6), opt(52, '机枪兵', 6)]), allow_multi: false }
const PARSE_DIMS = [launch, road, bucket, gate, flow]

describe('parseTagsFromName', () => {
  it('标准七段名：日期归 date 维度、各段按词表归位', () => {
    const { state, unmatched } = parseTagsFromName('2026-06-02_红桶_增长门_无挫-1boss_3路', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[1]).toEqual({ optionIds: [], valueDate: '2026-06-02' })
    expect(state[3]).toEqual({ optionIds: [21], valueDate: null })
    expect(state[4]).toEqual({ optionIds: [32], valueDate: null })
    expect(state[5]).toEqual({ optionIds: [42], valueDate: null })
    expect(state[2]).toEqual({ optionIds: [13], valueDate: null })
  })

  it('同维度多值 + 连：拆回多选', () => {
    const { state, unmatched } = parseTagsFromName('红桶+蓝桶_2路', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[3].optionIds).toEqual([21, 22])
    expect(state[2].optionIds).toEqual([12])
  })

  it('选项值自带 + 前缀（+1门（+n门））：不被当连接符劈开', () => {
    const { state, unmatched } = parseTagsFromName('+1门（+n门）_1搓', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[4].optionIds).toEqual([31])
    expect(state[5].optionIds).toEqual([41])
  })

  it('括号注释省写别名：文件名 +1门 命中 +1门（+n门）', () => {
    const { state, unmatched } = parseTagsFromName('+1门_1搓', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[4].optionIds).toEqual([31])
  })

  it('单字变体容错：无搓-1boss 命中词表 无挫-1boss（等长恰差1字且唯一）', () => {
    const { state, unmatched } = parseTagsFromName('红桶_无搓-1boss', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[5].optionIds).toEqual([42])
  })

  it('短 token 不走容错（防误吸）：2搓 不该吸成 1搓', () => {
    const { state, unmatched } = parseTagsFromName('2搓', PARSE_DIMS)
    expect(state[5]).toBeUndefined()
    expect(unmatched).toEqual(['2搓'])
  })

  it('多值里混含 * 前缀值（增长门+*人数门）', () => {
    const { state, unmatched } = parseTagsFromName('增长门+*人数门', PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state[4].optionIds).toEqual([32, 33])
  })

  it('token 边界：未知 token 含已知值前缀（木桶王）整体进 unmatched，不劈开产出错标签', () => {
    const buckets = dim(3, '桶子', 'text', [opt(21, '木桶', 3), opt(22, '蓝桶', 3)])
    const { state, unmatched } = parseTagsFromName('木桶王_蓝桶', [buckets])
    expect(unmatched).toEqual(['木桶王'])
    expect(state[3].optionIds).toEqual([22])
  })

  it('词表不认识的 token 进 unmatched，不影响其余归位', () => {
    const { state, unmatched } = parseTagsFromName('2026-01-01_新桶子_3路', PARSE_DIMS)
    expect(unmatched).toEqual(['新桶子'])
    expect(state[1].valueDate).toBe('2026-01-01')
    expect(state[2].optionIds).toEqual([13])
  })

  it('未知 token 与已知值同段 + 连：只丢未知半截', () => {
    const { state, unmatched } = parseTagsFromName('新桶+红桶', PARSE_DIMS)
    expect(unmatched).toEqual(['新桶'])
    expect(state[3].optionIds).toEqual([21])
  })

  it('单选维度收到第二个不同值：进 unmatched 不静默覆盖', () => {
    const { state, unmatched } = parseTagsFromName('AK+机枪兵', [roleSingle])
    expect(state[6].optionIds).toEqual([51])
    expect(unmatched).toEqual(['机枪兵'])
  })

  it('多选维度 AK+机枪兵 正常拆两值', () => {
    const multiRole = dim(6, '角色', 'text', [opt(51, 'AK', 6), opt(52, '机枪兵', 6)])
    const { state, unmatched } = parseTagsFromName('AK+机枪兵', [multiRole])
    expect(unmatched).toEqual([])
    expect(state[6].optionIds).toEqual([51, 52])
  })

  it('compose→parse 往返一致（同一份维度表）', () => {
    const orig: TagValueState = {
      1: { optionIds: [], valueDate: '2026-03-01' },
      2: { optionIds: [12], valueDate: null },
      4: { optionIds: [32, 33], valueDate: null },
      5: { optionIds: [42], valueDate: null },
    }
    const name = composeNameFromTags(orig, PARSE_DIMS)
    const { state, unmatched } = parseTagsFromName(name, PARSE_DIMS)
    expect(unmatched).toEqual([])
    expect(state).toEqual(orig)
  })

  it('空名 / 全未知：state 空、token 全收', () => {
    expect(parseTagsFromName('', PARSE_DIMS).state).toEqual({})
    const r = parseTagsFromName('随便什么_东西', PARSE_DIMS)
    expect(r.state).toEqual({})
    expect(r.unmatched).toEqual(['随便什么', '东西'])
  })
})

describe('mergeTagStates', () => {
  it('override 填了的维度整体覆盖，空维度不覆盖', () => {
    const base: TagValueState = {
      2: { optionIds: [11], valueDate: null },
      3: { optionIds: [21], valueDate: null },
    }
    const override: TagValueState = {
      2: { optionIds: [13], valueDate: null },
      4: { optionIds: [], valueDate: null }, // 空——不该覆盖也不该新增
    }
    const merged = mergeTagStates(base, override)
    expect(merged[2].optionIds).toEqual([13])
    expect(merged[3].optionIds).toEqual([21])
    expect(merged[4]).toBeUndefined()
  })
})
