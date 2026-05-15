import { useState } from 'react'

interface GameIconProps {
  src?: string | null
  name: string
  /** size + rounding utility classes, e.g. "w-9 h-9 rounded-xl" */
  className?: string
}

// Apple mzstatic 图标 URL 以 /{w}x{h}bb.{jpg|png} 结尾，改这段即可服务端缩放。
// 页面最大显示 64px(GameDetail)，2x 屏需 128px → 统一拉 128 而非原始 512，
// 体积约降到 1/10。非 mzstatic 链接原样返回。
function downscaleIcon(url: string): string {
  return url.replace(/\/\d+x\d+bb\.(jpg|png)$/i, '/128x128bb.$1')
}

/**
 * 游戏图标。src 缺失或加载失败（链接 404 / 过期 / 网络问题）时，
 * 回退到游戏名首字符的色块占位，永不出现浏览器破图符号。
 */
export function GameIcon({ src, name, className = 'w-9 h-9 rounded-xl' }: GameIconProps) {
  // 记录"哪个 src 失败过"而非布尔值：src 变化（数据刷新/图标更新）时自动重试，
  // 不会因一次失败就永久退化成占位。
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
