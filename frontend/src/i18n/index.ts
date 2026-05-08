import { useEffect, useState, useSyncExternalStore } from 'react'
import { zh, type Translations } from './zh'
import { en } from './en'

export type Locale = 'zh' | 'en'

const STORAGE_KEY = 'slg-locale'
const DICTS: Record<Locale, Translations> = { zh, en }

const listeners = new Set<() => void>()
let current: Locale = readInitial()

function readInitial(): Locale {
  if (typeof window === 'undefined') return 'zh'
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'zh' || stored === 'en') return stored
  return navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'zh'
}

function notify() { listeners.forEach(l => l()) }

export function setLocale(l: Locale) {
  if (current === l) return
  current = l
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(STORAGE_KEY, l)
    document.documentElement.lang = l === 'zh' ? 'zh-CN' : 'en'
  }
  notify()
}

export function getLocale(): Locale {
  return current
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale, getLocale)
}

export function useT(): Translations {
  const locale = useLocale()
  return DICTS[locale]
}
