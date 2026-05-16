import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

export function formatRevenue(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n}`
}

// 后端时间戳来自 SQLite CURRENT_TIMESTAMP（UTC，"YYYY-MM-DD HH:MM:SS"，无时区）
// 或带 T 的 datetime。必须当 UTC 解析，否则浏览器按本地时区会整体偏移。
export function backendTsToMs(raw: string): number | null {
  const norm = raw.includes('T') ? raw : raw.replace(' ', 'T')
  const iso = /[Z+]/.test(norm) ? norm : norm + 'Z'
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms
}

// 相对时间，紧凑且语言无关（与 formatNumber 的 K/M 同风格）：<1m / 5m / 2h / 3d
export function formatRelativeAge(raw: string): string {
  const ms = backendTsToMs(raw)
  if (ms === null) return '—'
  const sec = Math.max(0, (Date.now() - ms) / 1000)
  if (sec < 60) return '<1m'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h`
  return `${Math.floor(hr / 24)}d`
}

export const EVENT_TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  launch:    { label: '发布上线', color: 'text-emerald-400', bg: 'bg-emerald-500' },
  version:   { label: '版本更新', color: 'text-blue-400',    bg: 'bg-blue-500' },
  ranking:   { label: '排名突破', color: 'text-yellow-400',  bg: 'bg-yellow-500' },
  revenue:   { label: '收入里程碑', color: 'text-purple-400', bg: 'bg-purple-500' },
  marketing: { label: '营销事件', color: 'text-rose-400',    bg: 'bg-rose-500' },
}

export const PLATFORM_CONFIG: Record<string, { label: string; color: string }> = {
  youtube: { label: 'YouTube', color: 'text-red-400' },
  tiktok:  { label: 'TikTok',  color: 'text-pink-400' },
  meta:    { label: 'Meta Ads', color: 'text-blue-400' },
  other:   { label: '其他',     color: 'text-muted' },
}
