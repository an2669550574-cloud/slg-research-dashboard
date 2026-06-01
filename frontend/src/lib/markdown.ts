/**
 * 把「跨素材统一创意方向」结果转成 Markdown，用于一键复制 / 下载 .md。
 *
 * 纯前端字符串拼接、无副作用；字段缺失自动跳过（不输出空标题 / 空条目）。
 * 标签固定用中文：导出的本就是给策划看的中文创意成稿（模型也按中文产出），
 * 与 UI 语言无关，不走 i18n。
 */

export interface UnifiedDirection {
  name?: string
  concept?: string
  borrows_from_refs?: string
  fit_to_self_product?: string
  opening_3sec?: string
  key_hooks?: { ts_est?: string; kind?: string; note?: string }[]
  ending_cta?: string
  risk_notes?: string
}

export interface UnifiedData {
  common_patterns?: {
    shared_structure?: string
    shared_hooks?: string[]
    shared_pacing?: string
    notable_variations?: string
  }
  directions?: UnifiedDirection[]
}

export interface UnifiedMarkdownMeta {
  cost?: number
  model?: string
  productBrief?: string
  generatedAt?: Date
}

export function unifiedToMarkdown(data: UnifiedData, meta: UnifiedMarkdownMeta = {}): string {
  const lines: string[] = ['# 跨素材统一创意方向', '']

  const metaBits: string[] = []
  if (meta.model) metaBits.push(`模型：${meta.model}`)
  if (typeof meta.cost === 'number') metaBits.push(`成本：$${meta.cost.toFixed(4)}`)
  metaBits.push(`生成时间：${(meta.generatedAt ?? new Date()).toLocaleString()}`)
  lines.push(`> ${metaBits.join(' · ')}`, '')

  if (meta.productBrief?.trim()) {
    lines.push('## 自家产品 brief', '', meta.productBrief.trim(), '')
  }

  const cp = data.common_patterns
  if (cp && (cp.shared_structure || cp.shared_pacing || cp.shared_hooks?.length || cp.notable_variations)) {
    lines.push('## 共性结构', '')
    if (cp.shared_structure) lines.push(`- **底层结构**：${cp.shared_structure}`)
    if (cp.shared_pacing) lines.push(`- **节奏共性**：${cp.shared_pacing}`)
    if (cp.shared_hooks?.length) lines.push(`- **共性钩子**：${cp.shared_hooks.join('、')}`)
    if (cp.notable_variations) lines.push(`- **差异点**：${cp.notable_variations}`)
    lines.push('')
  }

  const dirs = data.directions ?? []
  if (dirs.length) {
    lines.push('## 迁移方向', '')
    dirs.forEach((d, i) => {
      const idx = String(i + 1).padStart(2, '0')
      lines.push(`### ${idx}${d.name ? ` ${d.name}` : ''}`, '')
      if (d.concept) lines.push(d.concept, '')
      if (d.opening_3sec) lines.push(`- **0-3s**：${d.opening_3sec}`)
      if (d.borrows_from_refs) lines.push(`- **借鉴结构**：${d.borrows_from_refs}`)
      if (d.fit_to_self_product) lines.push(`- **贴合自家产品**：${d.fit_to_self_product}`)
      if (d.key_hooks?.length) {
        const hooks = d.key_hooks
          .map(h => [h.ts_est, h.kind, h.note].filter(Boolean).join(' '))
          .filter(Boolean)
        if (hooks.length) lines.push(`- **关键钩子**：${hooks.join('；')}`)
      }
      if (d.ending_cta) lines.push(`- **CTA**：${d.ending_cta}`)
      if (d.risk_notes) lines.push(`- ⚠ **风险提示**：${d.risk_notes}`)
      lines.push('')
    })
  }

  return lines.join('\n').trimEnd() + '\n'
}

/** 下载纯文本为文件（沿用 csv.ts 的 blob+anchor 套路，带 UTF-8 BOM 防乱码）。 */
export function downloadText(filename: string, text: string, mime = 'text/markdown;charset=utf-8'): void {
  const blob = new Blob(['﻿' + text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
