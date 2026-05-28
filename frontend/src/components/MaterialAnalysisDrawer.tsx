import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X, Sparkles, AlertCircle, Loader2, RefreshCw, Tag as TagIcon, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { materialsApi } from '../lib/api'
import type { MaterialOut } from '../lib/types'

/** 抽屉：展示 / 触发某素材的 LLM 视频分析。
 *
 * 状态机：
 * - status=null|pending → 显示"开始分析"按钮
 * - status=running     → 显示 spinner，每 3s 轮询直到终态
 * - status=done        → 显示 brief/tags/scenes/hooks，含"重新分析"和"采纳标签"
 * - status=failed      → 显示错误信息 + 重试按钮
 */
export function MaterialAnalysisDrawer({
  material, onClose,
}: { material: MaterialOut | null; onClose: () => void }) {
  const qc = useQueryClient()
  const open = !!material
  const id = material?.id

  // 后台分析中时轮询；其它状态停止。useQuery 提供轮询机制 + 缓存与列表同步。
  const isRunning = material?.analysis_status === 'running'
  const detail = useQuery({
    queryKey: ['material', id],
    queryFn: () => materialsApi.get(id!),
    enabled: open && !!id,
    initialData: material ?? undefined,
    refetchInterval: isRunning ? 3000 : false,
  })
  const current = (detail.data ?? material) as MaterialOut | undefined

  // 每次轮询完成都把最新数据回写列表，避免抽屉关掉后列表里还是旧 status
  useEffect(() => {
    if (current) qc.setQueryData(['materials'], (old: any) => old)  // 触发列表重渲；具体 invalidate 在 mutation 里做
  }, [current?.analysis_status])

  const analyzeMut = useMutation({
    mutationFn: () => materialsApi.analyze(id!),
    onSuccess: (m) => {
      qc.setQueryData(['material', id], m)
      qc.invalidateQueries({ queryKey: ['materials'] })
      toast.success('已加入分析队列')
    },
    onError: (e: any) => {
      toast.error(e?.response?.data?.detail || '触发失败')
    },
  })

  const adoptMut = useMutation({
    mutationFn: () => materialsApi.adoptTags(id!),
    onSuccess: (m) => {
      qc.setQueryData(['material', id], m)
      qc.invalidateQueries({ queryKey: ['materials'] })
      qc.invalidateQueries({ queryKey: ['materialTags'] })
      toast.success('已采纳到标签')
    },
  })

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])

  // ⚠️ useRef 必须在条件 return 前调用，否则 open=false→true 时 hook 数量变化
  // 触发 React "Rendered more hooks than during the previous render" 崩溃。
  const videoRef = useRef<HTMLVideoElement | null>(null)

  if (!open || !current) return null

  const status = current.analysis_status ?? 'pending'
  const seekTo = (ts: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = ts
      videoRef.current.play().catch(() => {})
    }
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
          <button onClick={onClose} className="p-1.5 text-muted hover:text-primary transition-colors" aria-label="关闭">
            <X size={18} />
          </button>
        </header>

        {/* 视频预览（如果是上传素材） */}
        {current.source === 'upload' && current.stream_url && (
          <div className="px-5 pt-4">
            <video ref={videoRef} src={current.stream_url} controls preload="metadata"
              className="w-full rounded-lg border border-default bg-black" />
          </div>
        )}

        <div className="p-5 space-y-5">
          {/* ── 状态条 ── */}
          <StatusBlock
            status={status}
            running={isRunning}
            error={current.analysis_error}
            cost={current.analysis_cost_usd}
            model={current.analysis_model}
            analyzedAt={current.analyzed_at}
            onAnalyze={() => analyzeMut.mutate()}
            disabled={analyzeMut.isPending || isRunning}
          />

          {/* ── Brief ── */}
          {current.analysis_brief && (
            <Section title="总览">
              <p className="text-sm text-secondary leading-relaxed whitespace-pre-wrap">
                {current.analysis_brief}
              </p>
            </Section>
          )}

          {/* ── AI 提议的 tags ── */}
          {current.analysis_tags && current.analysis_tags.length > 0 && (
            <Section
              title="AI 标签建议"
              action={
                <button onClick={() => adoptMut.mutate()} disabled={adoptMut.isPending}
                  className="flex items-center gap-1 text-[11px] text-accent hover:text-accent/80 disabled:opacity-50">
                  <Plus size={12} /> 采纳到标签
                </button>
              }
            >
              <div className="flex flex-wrap gap-1.5">
                {current.analysis_tags.map((tag) => {
                  const already = (current.tags ?? []).includes(tag)
                  return (
                    <span key={tag}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] ${already ? 'border-accent/40 bg-accent/10 text-accent' : 'border-default bg-elevated text-secondary'}`}
                      title={already ? '已在人工标签中' : '建议标签'}>
                      <TagIcon size={10} />{tag}
                    </span>
                  )
                })}
              </div>
            </Section>
          )}

          {/* ── 联系单（所有关键帧拼图）── */}
          {current.analysis_contact_sheet_url && (
            <Section title="关键帧联系单">
              <a href={current.analysis_contact_sheet_url} target="_blank" rel="noopener noreferrer"
                title="点击查看大图">
                <img src={current.analysis_contact_sheet_url} alt="关键帧联系单"
                  className="w-full rounded-lg border border-default bg-black" loading="lazy" />
              </a>
            </Section>
          )}

          {/* ── 分镜（左侧时间戳 + 缩略图 + 描述）── */}
          {current.analysis_scenes && current.analysis_scenes.length > 0 && (
            <Section title="分镜">
              <ul className="space-y-3">
                {current.analysis_scenes.map((s, i) => {
                  const frame = findClosestFrame(s.ts, current.analysis_frames)
                  return (
                    <li key={i} className="flex gap-3 text-sm">
                      <button onClick={() => seekTo(s.ts)}
                        className="font-data text-[11px] text-accent hover:underline shrink-0 w-12 text-right pt-0.5"
                        title="跳转到该时间点">
                        {formatTs(s.ts)}
                      </button>
                      {frame && (
                        <button onClick={() => seekTo(s.ts)} title="跳转到该时间点"
                          className="shrink-0 w-24 aspect-video rounded border border-default overflow-hidden bg-black hover:border-accent transition-colors">
                          <img src={frame.url} alt="" loading="lazy"
                            className="w-full h-full object-cover" />
                        </button>
                      )}
                      <span className="text-secondary flex-1 pt-0.5">{s.description}</span>
                    </li>
                  )
                })}
              </ul>
            </Section>
          )}

          {/* ── 钩子 ── */}
          {current.analysis_hooks && current.analysis_hooks.length > 0 && (
            <Section title="买量钩子">
              <ul className="space-y-2">
                {current.analysis_hooks.map((h, i) => (
                  <li key={i} className="flex gap-3 text-sm">
                    <button onClick={() => seekTo(h.ts)}
                      className="font-data text-[11px] text-accent hover:underline shrink-0 w-12 text-right pt-0.5">
                      {formatTs(h.ts)}
                    </button>
                    <div className="flex-1">
                      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-data bg-accent/10 text-accent border border-accent/30 mr-2">
                        {h.kind}
                      </span>
                      <span className="text-secondary">{h.note}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </aside>
    </>
  )
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h3 className="eyebrow text-muted">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function StatusBlock({
  status, running, error, cost, model, analyzedAt, onAnalyze, disabled,
}: {
  status: string; running: boolean; error: string | null
  cost: number | null; model: string | null; analyzedAt: string | null
  onAnalyze: () => void; disabled: boolean
}) {
  if (running) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-accent/40 bg-accent/5 px-4 py-3">
        <Loader2 size={18} className="text-accent animate-spin shrink-0" />
        <div className="flex-1">
          <div className="text-sm text-primary">正在分析…</div>
          <div className="text-xs text-muted mt-0.5">抽帧 + 视觉模型推理，通常需 20–60 秒</div>
        </div>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3">
        <div className="flex items-start gap-2 mb-2">
          <AlertCircle size={16} className="text-red-400 shrink-0 mt-0.5" />
          <div className="text-sm text-primary">分析失败</div>
        </div>
        {error && <div className="text-xs text-secondary mb-3 break-words">{error}</div>}
        <button onClick={onAnalyze} disabled={disabled}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent hover:brightness-110 disabled:opacity-50 text-xs font-semibold text-white">
          <RefreshCw size={12} /> 重试
        </button>
      </div>
    )
  }
  if (status === 'done') {
    return (
      <div className="rounded-lg border border-default bg-elevated/30 px-4 py-3">
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <div className="eyebrow text-emerald-400">已分析</div>
          <button onClick={onAnalyze} disabled={disabled}
            title="重新分析（会重新消耗 LLM 预算）"
            className="flex items-center gap-1 text-[11px] text-muted hover:text-primary">
            <RefreshCw size={11} /> 重新分析
          </button>
        </div>
        <div className="font-data text-[11px] text-muted space-y-0.5">
          {analyzedAt && <div>时间：{new Date(analyzedAt).toLocaleString()}</div>}
          {model && <div>模型：{model}</div>}
          {cost != null && <div>成本：${cost.toFixed(4)}</div>}
        </div>
      </div>
    )
  }
  // pending / null：尚未分析
  return (
    <div className="rounded-lg border border-dashed border-default bg-elevated/20 px-4 py-4 text-center">
      <Sparkles size={20} className="text-muted mx-auto mb-2" />
      <p className="text-sm text-secondary mb-3">尚未分析。点击下方按钮触发 LLM 视频分析。</p>
      <button onClick={onAnalyze} disabled={disabled}
        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md bg-accent hover:brightness-110 disabled:opacity-50 text-sm font-semibold text-white">
        <Sparkles size={14} /> 开始分析
      </button>
    </div>
  )
}

function formatTs(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 按 ts 找最近的帧。LLM 的 scenes ts 一般就近 extract_frames 的采样点，
 * 但偶尔会偏移；近邻匹配比按 index 强（也容忍 scenes 数 ≠ frames 数）。*/
function findClosestFrame(
  ts: number,
  frames: { ts: number; url: string }[] | null,
): { ts: number; url: string } | null {
  if (!frames || frames.length === 0) return null
  let best = frames[0]
  let bestDelta = Math.abs(best.ts - ts)
  for (const f of frames) {
    const d = Math.abs(f.ts - ts)
    if (d < bestDelta) { best = f; bestDelta = d }
  }
  return best
}
