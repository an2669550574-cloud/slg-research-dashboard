import { useState } from 'react'

interface GameIconProps {
  src?: string | null
  name: string
  /** size + rounding utility classes, e.g. "w-9 h-9 rounded-xl" */
  className?: string
}

/**
 * 游戏图标。src 缺失或加载失败（链接 404 / 过期 / 网络问题）时，
 * 回退到游戏名首字符的色块占位，永不出现浏览器破图符号。
 */
export function GameIcon({ src, name, className = 'w-9 h-9 rounded-xl' }: GameIconProps) {
  const [failed, setFailed] = useState(false)

  if (src && !failed) {
    return (
      <img
        src={src}
        alt={name}
        className={`${className} object-cover shrink-0`}
        onError={() => setFailed(true)}
      />
    )
  }

  const letter = (name || '?').trim().charAt(0).toUpperCase() || '?'
  return (
    <div
      className={`${className} shrink-0 bg-elevated flex items-center justify-center text-secondary text-sm font-semibold select-none`}
      aria-label={name}
    >
      {letter}
    </div>
  )
}
