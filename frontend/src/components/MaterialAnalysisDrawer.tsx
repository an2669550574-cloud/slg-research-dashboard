import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { X, Sparkles, Maximize2 } from 'lucide-react'
import type { MaterialOut } from '../lib/types'
import { useMaterialAnalysis, MaterialAnalysisBody } from './MaterialAnalysisContent'

/** 抽屉：展示 / 触发某素材的 LLM 视频分析（快速预览用）。
 *
 * 深读 / 分享请走整页 `/materials/:id/analysis`（header 的「在新页打开」）。
 * 数据拉取与内容体复用 {@link useMaterialAnalysis} / {@link MaterialAnalysisBody}，
 * 抽屉这层只负责：遮罩 + 侧滑壳 + 顶部 video + seekTo（滚回视口）。
 */
export function MaterialAnalysisDrawer({
  material, onClose,
}: { material: MaterialOut | null; onClose: () => void }) {
  const open = !!material
  // ⚠️ 所有 hooks 必须在 early return 之前（见 feedback-react-hooks-early-return）。
  const { current, status, isRunning, analyzeMut, adoptMut } = useMaterialAnalysis({ material, enabled: open })

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])

  const videoRef = useRef<HTMLVideoElement | null>(null)

  if (!open || !current) return null

  // 抽屉是竖向滚动壳：seek 时把 video 滚回视口再播，避免"有声没画"。
  const seekTo = (ts: number) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = ts
    v.scrollIntoView({ behavior: 'smooth', block: 'center' })
    v.play().catch(() => {})
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-base/70 backdrop-blur-sm" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-50 w-full sm:w-[520px] bg-surface border-l border-strong shadow-2xl overflow-y-auto"
        role="dialog" aria-label="素材 AI 分析">
        <header className="sticky top-0 z-10 bg-surface/95 backdrop-blur border-b border-default px-5 py-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 eyebrow text-accent">
              <Sparkles size={12} /> AI ANALYSIS
            </div>
            <div className="mt-1 font-display font-bold text-primary text-base leading-tight line-clamp-2">
              {current.title}
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Link to={`/materials/${current.id}/analysis`} onClick={onClose}
              title="在新页打开（深读 / 可分享链接）"
              className="p-1.5 text-muted hover:text-accent transition-colors" aria-label="在新页打开">
              <Maximize2 size={16} />
            </Link>
            <button onClick={onClose} className="p-1.5 text-muted hover:text-primary transition-colors" aria-label="关闭">
              <X size={18} />
            </button>
          </div>
        </header>

        {/* 视频预览（如果是上传素材） */}
        {current.source === 'upload' && current.stream_url && (
          <div className="px-5 pt-4">
            <video ref={videoRef} src={current.stream_url} controls preload="metadata"
              className="w-full rounded-lg border border-default bg-black" />
          </div>
        )}

        <div className="p-5">
          <MaterialAnalysisBody
            current={current}
            status={status}
            isRunning={isRunning}
            seekTo={seekTo}
            onAnalyze={(model) => analyzeMut.mutate(model)}
            onAdopt={() => adoptMut.mutate()}
            analyzeDisabled={analyzeMut.isPending || isRunning}
            adoptDisabled={adoptMut.isPending}
          />
        </div>
      </aside>
    </>
  )
}
