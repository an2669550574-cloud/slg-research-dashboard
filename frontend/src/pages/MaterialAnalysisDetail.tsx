import { useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Sparkles, Loader2 } from 'lucide-react'
import { useMaterialAnalysis, MaterialAnalysisBody } from '../components/MaterialAnalysisContent'

/** 单素材 AI 分析整页（深读 / 可分享链接）。路由 `/materials/:id/analysis`。
 *
 * 与抽屉共用 {@link useMaterialAnalysis} / {@link MaterialAnalysisBody}；区别在布局：
 * 左侧 video 桌面端 sticky 常驻，右侧滚动内容。因 video 不离屏，seek 无需 scrollIntoView。
 */
export default function MaterialAnalysisDetail() {
  const { id } = useParams()
  const mid = Number(id)
  const valid = Number.isFinite(mid) && mid > 0
  const { current, isLoading, isError, status, isRunning, analyzeMut, adoptMut } =
    useMaterialAnalysis({ id: valid ? mid : undefined })

  const videoRef = useRef<HTMLVideoElement | null>(null)
  // video 左侧 sticky 常驻，seek 直接播即可（无需滚回视口）。
  const seekTo = (ts: number) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = ts
    v.play().catch(() => {})
  }

  const back = (
    <Link to="/materials/analysis"
      className="inline-flex items-center gap-1.5 text-sm text-secondary hover:text-primary transition-colors">
      <ArrowLeft size={15} /> 返回 AI 解析报告
    </Link>
  )

  if (!valid || isError) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-4">
        {back}
        <div className="rounded-lg border border-default bg-elevated/30 px-4 py-8 text-center text-sm text-muted">
          素材不存在或加载失败。
        </div>
      </div>
    )
  }

  if (isLoading && !current) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-4">
        {back}
        <div className="flex items-center gap-2 text-sm text-muted px-4 py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> 加载中…
        </div>
      </div>
    )
  }

  if (!current) return null

  const hasVideo = current.source === 'upload' && !!current.stream_url

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      {back}

      <div className="grid gap-6 lg:grid-cols-[minmax(280px,360px)_1fr]">
        {/* 左：视频 + 标题，桌面端 sticky 常驻 */}
        <div className="space-y-3 lg:sticky lg:top-6 self-start">
          {hasVideo && (
            <video ref={videoRef} src={current.stream_url!} controls preload="metadata"
              className="w-full rounded-lg border border-default bg-black" />
          )}
          <div>
            <div className="flex items-center gap-2 eyebrow text-accent">
              <Sparkles size={12} /> AI ANALYSIS
            </div>
            <h1 className="mt-1 font-display font-bold text-primary text-lg leading-tight">
              {current.title}
            </h1>
          </div>
        </div>

        {/* 右：分析内容体（滚动） */}
        <div className="min-w-0">
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
      </div>
    </div>
  )
}
