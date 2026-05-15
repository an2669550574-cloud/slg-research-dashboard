import { useState } from 'react'

interface GameIconProps {
  src?: string | null
  name: string
  /** size + rounding utility classes, e.g. "w-9 h-9 rounded-xl" */
  className?: string
}

// mzstatic 图标 URL 末段 /{w}x{h}bb.{jpg|png} 可直接改写做服务端缩放。
function downscaleIcon(url: string): string {
  return url.replace(/\/\d+x\d+bb\.(jpg|png)$/i, '/128x128bb.$1')
}

export function GameIcon({ src, name, className = 'w-9 h-9 rounded-xl' }: GameIconProps) {
  // 记录失败的 src 而非布尔：src 变化时自动重试，不因一次失败永久退化为占位。
  const [failedSrc, setFailedSrc] = useState<string | null>(null)

  if (src && failedSrc !== src) {
    return (
      <img
        src={downscaleIcon(src)}
        alt={name}
        className={`${className} object-cover shrink-0`}
        onError={() => setFailedSrc(src)}
      />
    )
  }

  const letter = name.trim().charAt(0).toUpperCase() || '?'
  return (
    <div
      className={`${className} shrink-0 bg-elevated flex items-center justify-center text-secondary text-sm font-semibold select-none`}
      aria-label={name}
    >
      {letter}
    </div>
  )
}
