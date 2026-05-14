import { useEffect, useState } from 'react'

/**
 * 输入防抖。打字时不要每键弹一次后端请求；同时翻页等其它 state 变更立即生效。
 * 典型用法：把 search 输入框的值传进来，把返回值用进 queryKey/queryFn。
 */
export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(id)
  }, [value, delayMs])
  return debounced
}
