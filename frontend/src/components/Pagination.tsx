import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useT } from '../i18n'

interface Props {
  total: number
  offset: number
  pageSize: number
  onOffsetChange: (offset: number) => void
}

export function Pagination({ total, offset, pageSize, onOffsetChange }: Props) {
  const t = useT()
  if (total <= pageSize) return null // 单页时整体隐藏

  const page = Math.floor(offset / pageSize) + 1
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const from = offset + 1
  const to = Math.min(offset + pageSize, total)
  const canPrev = offset > 0
  const canNext = offset + pageSize < total

  return (
    <div className="flex items-center justify-between text-xs text-secondary">
      <span>{t.common.paginationRange(from, to, total)}</span>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}
          disabled={!canPrev}
          className="p-1.5 rounded-md bg-elevated hover:bg-elevated/70 disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={t.common.paginationPrev}
        >
          <ChevronLeft size={14} />
        </button>
        <span className="tabular-nums">{t.common.paginationPage(page, totalPages)}</span>
        <button
          onClick={() => onOffsetChange(offset + pageSize)}
          disabled={!canNext}
          className="p-1.5 rounded-md bg-elevated hover:bg-elevated/70 disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={t.common.paginationNext}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
}
