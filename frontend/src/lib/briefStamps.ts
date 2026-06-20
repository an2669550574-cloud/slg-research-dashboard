/**
 * brief 戳记解析：约定 brief 文本里以「\n\n【label YYYY-MM-DD】content」追加历史
 * 戳记（调研更新 / 调研负面发现 / 复查 negative 等），抽屉里把主 brief 顶部显示、
 * 戳记折叠到「调研历史」区，避免一坨长文。编辑模式（form 里）仍是全文本。
 *
 * 不改 DB schema —— brief 留纯文本，结构纯靠客户端 parse 这套约定 marker。
 */

export type BriefStamp = {
  label: string   // 如「调研更新」「调研负面发现」「复查 negative」
  date: string    // ISO YYYY-MM-DD，无则 ''
  content: string
}

export type ParsedBrief = {
  main: string         // 主 brief（戳记前的部分）
  stamps: BriefStamp[] // 解出的戳记，按日期倒序（最新在上）
}

// 段落分割：戳记必在 "\n\n【...YYYY-MM-DD】" 开头
const STAMP_SPLIT_RE = /\n\n(?=【[^】]*?\d{4}-\d{2}-\d{2}】)/
// 戳记头：【label YYYY-MM-DD】content
const STAMP_PARSE_RE = /^【([^】]+?)\s+(\d{4}-\d{2}-\d{2})】([\s\S]*)$/

export function parseBrief(text: string | null | undefined): ParsedBrief {
  if (!text) return { main: '', stamps: [] }
  const parts = text.split(STAMP_SPLIT_RE)
  const main = parts[0].trim()
  const stamps: BriefStamp[] = []
  for (let i = 1; i < parts.length; i++) {
    const m = parts[i].match(STAMP_PARSE_RE)
    if (m) {
      stamps.push({ label: m[1].trim(), date: m[2], content: m[3].trim() })
    } else {
      // 戳记格式不符（手写脏数据）：当作无 label/date 的历史段落原样保留
      stamps.push({ label: '历史', date: '', content: parts[i].trim() })
    }
  }
  // 日期倒序：最新调研在前；无日期段落沉底
  stamps.sort((a, b) => {
    if (!a.date && !b.date) return 0
    if (!a.date) return 1
    if (!b.date) return -1
    return b.date.localeCompare(a.date)
  })
  return { main, stamps }
}
