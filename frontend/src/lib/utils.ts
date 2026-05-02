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
  other:   { label: '其他',     color: 'text-gray-400' },
}
