import { describe, it, expect } from 'vitest'
import { composeNameFromTags, composeNameFromTagValues } from './tagName'
import type { TagDimension, MaterialTagValueItem } from './types'
import type { TagValueState } from '../components/StructuredTagEditor'

// 中文标签库夹具：路型(单/多选 text) / 桶子(text) / 投放时间(date)。
const opt = (id: number, value: string, dimension_id: number): TagDimension['options'][number] =>
  ({ id, dimension_id, value, sort_order: 0, created_at: '2026-06-02T00:00:00' })

const dim = (id: number, name: string, value_type: 'text' | 'date', options: TagDimension['options']): TagDimension =>
  ({ id, name, value_type, material_type: null, is_required: false, allow_multi: true, sort_order: id, created_at: '2026-06-02T00:00:00', options })

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
