/**
 * 仪表盘合计视图：跨平台合并同款游戏（iOS + Android）。
 *
 * 背景：后端 /games/aggregate-leaderboard 按 app_id GROUP BY，所以同款游戏的
 * iOS 与 Android 因为是不同 app_id 会作为两行返回。前端在收到美区名字归一化后，
 * 再做一次"同 publisher + 名字前缀匹配"的二次合并，把视觉上的重复消掉。
 *
 * 不在后端做的原因：① aggregate-leaderboard 也被详情页头部"已监测市场合计"对账
 * 引用（同 app_id 口径），改后端会牵动那条对账； ② 合并完代表行只剩一个 app_id,
 * 点进详情仍合理；③ 前端能复用美区名字参考表，更准。
 */

export type AggRow = {
  app_id: string
  name: string | null
  publisher: string | null
  icon_url: string | null
  downloads: number | null
  revenue: number | null
}

/** 规范化标识：去大小写 + 删非字母数字。"Century Games PTE. LTD." 与
 *  "Century Games Pte. Ltd." 规范化后等同。 */
export function normIdent(s: string | null | undefined): string {
  return (s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '')
}

/**
 * 合并规则：
 *  - 同 publisher（规范化等同）AND 名字一方是另一方的规范化前缀
 *  - 且较短一方的规范化字符串 ≥ 5（防"Z" 误合 "ZGame"）
 *  - 跨 publisher 不合并（publisher 差异大就视为不同游戏）
 *  - 空 publisher 不参与合并（每行独立保留）
 *
 * 代表行取 cluster 内**收入最高**的那条（保留 app_id/name/publisher/icon）；
 * downloads/revenue 求和；最后按合并后 revenue 降序返回。
 */
export function mergeCrossPlatform(rows: AggRow[]): AggRow[] {
  const noPub: AggRow[] = []
  const byPub = new Map<string, AggRow[]>()
  for (const r of rows) {
    const pk = normIdent(r.publisher)
    if (!pk) { noPub.push(r); continue }
    const arr = byPub.get(pk) ?? []
    arr.push(r)
    byPub.set(pk, arr)
  }
  const out: AggRow[] = [...noPub]
  for (const list of byPub.values()) {
    // 同 publisher 内按收入降序，cluster 代表自然就是收入最高的那条
    list.sort((a, b) => (b.revenue ?? 0) - (a.revenue ?? 0))
    const reps: { rep: AggRow; sumRev: number; sumDl: number }[] = []
    for (const r of list) {
      const rk = normIdent(r.name)
      const idx = reps.findIndex(({ rep }) => {
        const pk = normIdent(rep.name)
        const short = pk.length <= rk.length ? pk : rk
        const long = pk.length <= rk.length ? rk : pk
        if (short.length < 5) return false
        return long.startsWith(short)
      })
      if (idx >= 0) {
        reps[idx].sumRev += r.revenue ?? 0
        reps[idx].sumDl += r.downloads ?? 0
      } else {
        reps.push({ rep: r, sumRev: r.revenue ?? 0, sumDl: r.downloads ?? 0 })
      }
    }
    for (const { rep, sumRev, sumDl } of reps) {
      out.push({ ...rep, revenue: sumRev, downloads: sumDl })
    }
  }
  out.sort((a, b) => (b.revenue ?? 0) - (a.revenue ?? 0))
  return out
}
