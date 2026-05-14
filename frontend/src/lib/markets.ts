/**
 * 国家/平台常量，前端选择器共享。后端 scheduler 的 SYNC_RANKING_COMBOS
 * 决定哪些组合有 DB 当日数据；其它组合会回退到 Sensor Tower（消耗配额）。
 */

export const COUNTRIES = ['US', 'GB', 'DE', 'JP', 'KR', 'AU', 'CA', 'FR'] as const
export const PLATFORMS = ['ios', 'android'] as const

export type Country = (typeof COUNTRIES)[number]
export type Platform = (typeof PLATFORMS)[number]

export function platformLabel(p: Platform): string {
  return p === 'ios' ? 'iOS' : 'Android'
}
