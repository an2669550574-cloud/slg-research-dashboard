import type { NewcomerHistoryItem, PublisherNewcomerItem } from './types'

/** 同款全球游戏跨市场合并后的卡片模型（D1）。
 *  分组键 = app_id：iOS app_id 是数字 trackId、Android 是 GP 包名，二者跨国一致、
 *  且 iOS/Android 永不撞键，故按 app_id 分组恰好把「同一款在多市场」收成一张卡——
 *  与既有「跨平台」sibling 去重（iOS×Android 同游戏，sibling_match.py）是不同轴。 */
export interface GroupedNewcomer {
  app_id: string
  /** 展示代表行：取最佳名次那行（其 icon/名称/发行商/富化字段/归属在同 app_id 下一致）。 */
  rep: NewcomerHistoryItem
  /** 全部市场检出行，按名次升序（缺名次沉底）。 */
  markets: NewcomerHistoryItem[]
  /** 跨市场最佳（最小）名次。 */
  bestRank: number | null
  /** 最早一次检出的快照日（取 first_detected_at 最早那行的 as_of）。 */
  earliestAsOf: string
  /** 任一市场为「回归」检出。 */
  anyReentry: boolean
  /** 任一检出来自下载榜（chart='all' 时用于卡片打榜类型徽标）。 */
  anyFree: boolean
}

/** 把扁平的逐市场检出按 app_id 收成卡片。保留首次出现顺序（服务端已按
 *  first_detected_at 倒序 → 最新检出的款排在前），卡片顺序即「最近检出优先」。 */
export function groupByApp(items: NewcomerHistoryItem[]): GroupedNewcomer[] {
  const map = new Map<string, NewcomerHistoryItem[]>()
  for (const it of items) {
    const arr = map.get(it.app_id)
    if (arr) arr.push(it)
    else map.set(it.app_id, [it])
  }
  const rankOf = (r: NewcomerHistoryItem) => (r.rank == null ? Infinity : r.rank)
  return Array.from(map.values()).map(rows => {
    const markets = [...rows].sort((a, b) => rankOf(a) - rankOf(b))
    const rep = markets[0]
    const bestRank = rep.rank ?? null
    const earliest = rows.reduce((a, b) =>
      a.first_detected_at <= b.first_detected_at ? a : b)
    return {
      app_id: rep.app_id,
      rep,
      markets,
      bestRank,
      earliestAsOf: earliest.as_of,
      anyReentry: rows.some(r => r.is_reentry === true),
      anyFree: rows.some(r => r.chart_type === 'free'),
    }
  })
}

/** 厂商新品跨市场合并后的表格行模型（D2，与 D1 同轴对称）。 */
export interface GroupedPublisherNewcomer {
  app_id: string
  /** 代表行：最佳名次那行（同 app_id 必属同一已建档主体，entity/名称/归属一致）。 */
  rep: PublisherNewcomerItem
  /** 全部市场行，按名次升序（缺名次沉底）。 */
  markets: PublisherNewcomerItem[]
  /** 跨市场最佳（最小）名次。 */
  bestRank: number | null
  /** 最早一次出现的快照日（publisher 项无 first_detected_at，取最早 as_of）。 */
  earliestAsOf: string
}

/** 把「厂商新品」逐市场行按 app_id 收成一行 + 多市场徽标（与 groupByApp 同思路）。
 *  保留首次出现顺序——服务端已按 (entity_name, rank) 排序，故合并后仍按主体分组。 */
export function groupPublisherByApp(items: PublisherNewcomerItem[]): GroupedPublisherNewcomer[] {
  const map = new Map<string, PublisherNewcomerItem[]>()
  for (const it of items) {
    const arr = map.get(it.app_id)
    if (arr) arr.push(it)
    else map.set(it.app_id, [it])
  }
  const rankOf = (r: PublisherNewcomerItem) => (r.rank == null ? Infinity : r.rank)
  return Array.from(map.values()).map(rows => {
    const markets = [...rows].sort((a, b) => rankOf(a) - rankOf(b))
    const rep = markets[0]
    const earliest = rows.reduce((a, b) => (a.as_of <= b.as_of ? a : b))
    return { app_id: rep.app_id, rep, markets, bestRank: rep.rank ?? null, earliestAsOf: earliest.as_of }
  })
}
