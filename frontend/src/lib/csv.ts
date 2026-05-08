function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  let s = String(v)
  if (Array.isArray(v)) s = v.join('|')
  // 防御 CSV 注入：以 = + - @ \t \r 开头的单元格在 Excel 中会被当作公式，前缀单引号阻断
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"'
  return s
}

export interface CsvColumn<T> {
  header: string
  get: (row: T) => unknown
}

export function rowsToCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const headerLine = columns.map(c => escapeCell(c.header)).join(',')
  const lines = rows.map(r => columns.map(c => escapeCell(c.get(r))).join(','))
  return [headerLine, ...lines].join('\n')
}

export function downloadCsv<T>(filename: string, rows: T[], columns: CsvColumn<T>[]): void {
  const csv = rowsToCsv(rows, columns)
  // BOM 让 Excel 正确识别 UTF-8（否则中文乱码）
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
