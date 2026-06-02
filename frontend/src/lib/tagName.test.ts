import { describe, it, expect } from 'vitest'
import { composeNameFromTags } from './tagName'
import type { TagDimension } from './types'
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
