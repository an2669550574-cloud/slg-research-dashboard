import { AlertTriangle, RotateCw } from 'lucide-react'
import { useT } from '../i18n'

interface Props {
  onRetry: () => void
  /** 紧凑模式：嵌在卡片/图表里时用更小的留白 */
  compact?: boolean
}

// 查询失败时的兜底 UI。此前所有页面只 isLoading，请求失败会无限骨架屏或
// 看起来"空数据"，只有一闪而过的 toast——分不清"没数据"还是"加载挂了"。
export function QueryError({ onRetry, compact }: Props) {
  const t = useT()
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? 'py-10' : 'py-20'}`}>
      <AlertTriangle className="text-red-400 mb-3" size={compact ? 24 : 32} />
      <p className="text-sm text-secondary mb-4">{t.common.loadFailed}</p>
      <button
        onClick={onRetry}
        className="flex items-center gap-2 px-4 py-2 bg-elevated hover:bg-elevated/70 rounded-lg text-sm text-primary transition-colors"
      >
        <RotateCw size={14} />
        {t.common.retry}
      </button>
    </div>
  )
}
