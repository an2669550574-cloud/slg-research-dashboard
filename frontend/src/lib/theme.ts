import { useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'slg-theme'

function readInitial(): Theme {
  if (typeof window === 'undefined') return 'dark'
  // URL 参数 ?theme=light|dark 优先（用于无头截图等场景，覆盖 localStorage）
  const param = new URLSearchParams(window.location.search).get('theme')
  if (param === 'light' || param === 'dark') return param
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  // 暗色优先设计：未显式选择一律暗色（不跟随系统亮色偏好，否则首因印象就弱）
  return 'dark'
}

function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (theme === 'light') root.setAttribute('data-theme', 'light')
  else root.removeAttribute('data-theme')
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readInitial)

  useEffect(() => {
    applyTheme(theme)
    window.localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  return { theme, toggle: () => setTheme(t => (t === 'dark' ? 'light' : 'dark')) }
}
