import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles, AlertCircle, Loader2, RefreshCw, Tag as TagIcon, Plus, Wand2, ChevronRight, FileText } from 'lucide-react'
import toast from 'react-hot-toast'
import { materialsApi } from '../lib/api'
import { OwnProductPicker } from './OwnProductPicker'
import type {
  MaterialOut, CreativeDirection, CreativeDirectionsResult, CreativeScriptResult,
} from '../lib/types'

/** 抽屉 + 详情页共用：拉取单素材分析数据 + 触发分析/采纳标签的 mutations。
 *
 * - 抽屉：传 `material`（已有对象）做 initialData，`enabled` 跟随开合。
 * - 详情页：只传 `id`（来自路由），首次靠 query 拉取。
 * 运行中每 3s 轮询直到终态（函数式 refetchInterval 读最新 data，两种入口都适用）。 */
export function useMaterialAnalysis(opts: {
  material?: MaterialOut | null
  id?: number
  enabled?: boolean
}) {
  const { material, enabled = true } = opts
  const id = opts.id ?? material?.id
  const qc = useQueryClient()

  const detail = useQuery({
    queryKey: ['material', id],
    queryFn: () => materialsApi.get(id!),
    enabled: enabled && !!id,
    initialData: material ?? undefined,
    refetchInterval: (q) =>
      (q.state.data as MaterialOut | undefined)?.analysis_status === 'running' ? 3000 : false,
  })
  const current = (detail.data ?? material ?? undefined) as MaterialOut | undefined

  const analyzeMut = useMutation({
    mutationFn: () => materialsApi.analyze(id!),
    onSuccess: (m) => {
      qc.setQueryData(['material', id], m)
      qc.invalidateQueries({ queryKey: ['materials'] })
      toast.success('已加入分析队列')
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || '触发失败'),
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

  const status = current?.analysis_status ?? 'pending'
  const isRunning = status === 'running'

  return {
    current, status, isRunning, analyzeMut, adoptMut,
    isLoading: detail.isLoading, isError: detail.isError,
  }
}

/** 抽屉 + 详情页共用的分析内容体：状态条 / 总览 / 标签 / 关键帧 / 分镜 / 钩子 / 创意迁移。
 *
 * 视频元素与 `seekTo` 由外层布局提供（抽屉里 video 在顶部、详情页里 video 左侧 sticky），
 * 因此本组件不渲染 video，只负责数据展示与点击回调。
 * 内容体不依赖父级 padding（AdaptBlock 不再用 -mx 负边距），可塞进任意壳。 */
export function MaterialAnalysisBody({
  current, status, isRunning, seekTo, onAnalyze, onAdopt, analyzeDisabled, adoptDisabled,
}: {
  current: MaterialOut
  status: string
  isRunning: boolean
  seekTo: (ts: number) => void
  onAnalyze: () => void
  onAdopt: () => void
  analyzeDisabled: boolean
  adoptDisabled: boolean
}) {
  return (
    <div className="space-y-5">
      {/* ── 状态条 ── */}
      <StatusBlock
        status={status}
        running={isRunning}
        error={current.analysis_error}
        cost={current.analysis_cost_usd}
        model={current.analysis_model}
        analyzedAt={current.analyzed_at}
        onAnalyze={onAnalyze}
        disabled={analyzeDisabled}
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
            <button onClick={onAdopt} disabled={adoptDisabled}
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

      {/* ── 关键帧总览（逐帧高清图，点击跳转到该时间点）── */}
      {current.analysis_frames && current.analysis_frames.length > 0 && (
        <Section title="关键帧总览">
          <div className="grid grid-cols-5 gap-1.5">
            {current.analysis_frames.map((f, i) => (
              <button key={i} onClick={() => seekTo(f.ts)} title={`跳转到 ${formatTs(f.ts)}`}
                className="group relative aspect-[9/16] rounded-md border border-default overflow-hidden bg-black hover:border-accent transition-colors">
                <img src={f.url} alt="" loading="lazy"
                  className="w-full h-full object-cover" />
                <span className="absolute bottom-0.5 left-0.5 px-1 rounded bg-black/60 font-data text-[9px] text-accent opacity-0 group-hover:opacity-100 transition-opacity">
                  {formatTs(f.ts)}
                </span>
              </button>
            ))}
          </div>
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

      {/* ── 创意迁移：参考片 + 自家产品 → 方向 → 脚本 ── */}
      {status === 'done' && <AdaptBlock materialId={current.id} />}
    </div>
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

// ─── 创意迁移区块 ─────────────────────────────────────────────────────
//
// 参考公众号《开源买量素材skill》（作者：杰克 Ultra）方法论：
//   先让 AI 给方向 → 人先挑方向 → 再让 AI 基于选中的方向写脚本
// localStorage 记忆"自家产品 brief"，避免每次重新填。

const LS_PRODUCT_KEY = 'slg.adaptOurProduct'

function AdaptBlock({ materialId }: { materialId: number }) {
  const [open, setOpen] = useState(false)
  const [product, setProduct] = useState(() => localStorage.getItem(LS_PRODUCT_KEY) || '')
  const [productId, setProductId] = useState<number | null>(null)
  const [directions, setDirections] = useState<CreativeDirection[] | null>(null)
  const [chosen, setChosen] = useState<CreativeDirection | null>(null)
  const [script, setScript] = useState<CreativeScriptResult['data'] | null>(null)
  const [totalCost, setTotalCost] = useState(0)

  const dirMut = useMutation({
    mutationFn: () => materialsApi.adaptDirections(materialId, product) as Promise<CreativeDirectionsResult>,
    onSuccess: (r) => {
      localStorage.setItem(LS_PRODUCT_KEY, product)
      setDirections(r.data.directions ?? [])
      setChosen(null); setScript(null)
      setTotalCost(c => c + (r.cost_usd || 0))
      toast.success(`生成 ${r.data.directions?.length ?? 0} 个方向（$${(r.cost_usd ?? 0).toFixed(4)}）`)
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || '方向生成失败'),
  })

  const scriptMut = useMutation({
    mutationFn: (d: CreativeDirection) =>
      materialsApi.adaptScript(materialId, product, d) as Promise<CreativeScriptResult>,
    onSuccess: (r) => {
      setScript(r.data)
      setTotalCost(c => c + (r.cost_usd || 0))
      toast.success(`脚本生成完成（$${(r.cost_usd ?? 0).toFixed(4)}）`)
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || '脚本生成失败'),
  })

  if (!open) {
    return (
      <section className="border-t border-default pt-5">
        <button onClick={() => setOpen(true)}
          className="w-full flex items-center justify-between gap-3 p-3 rounded-lg border border-accent/40 bg-accent/5 hover:bg-accent/10 transition-colors">
          <div className="flex items-center gap-2 text-accent">
            <Wand2 size={15} />
            <span className="font-display font-semibold text-sm">迁移到自家产品 · 生成创意方向</span>
          </div>
          <ChevronRight size={16} className="text-accent" />
        </button>
      </section>
    )
  }

  return (
    <section className="border-t border-default pt-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="eyebrow text-accent flex items-center gap-1.5">
          <Wand2 size={12} /> 创意迁移
        </h3>
        <div className="flex items-center gap-2 text-[11px] text-muted font-data">
          {totalCost > 0 && <span>累计 ${totalCost.toFixed(4)}</span>}
          <button onClick={() => setOpen(false)} className="hover:text-primary">收起</button>
        </div>
      </div>

      {/* ① 自家产品：从已存档案选一条带入，或手动输入（localStorage 记忆手输值）*/}
      <OwnProductPicker
        selectedId={productId}
        onPick={(id, brief) => { setProductId(id); if (brief !== null) setProduct(brief) }}
        autoSelectDefault
      />
      <div>
        <label className="block text-xs text-muted mb-1.5">自家产品 brief（题材 / 玩法 / 卖点 / 受众 / 差异化）</label>
        <textarea
          value={product}
          onChange={e => { setProduct(e.target.value); setProductId(null) }}
          rows={4}
          placeholder="例：《XX》现代都市丧尸题材 SLG，玩法核心：庇护所建造 + 队伍指挥；&#10;主打卖点：女性主角 + 故事化叙事；目标人群：30-45 岁女性玩家。"
          className="w-full bg-elevated/60 border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
        />
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={() => dirMut.mutate()}
            disabled={dirMut.isPending || !product.trim()}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-md bg-accent hover:brightness-110 disabled:opacity-50 text-sm font-semibold text-white">
            {dirMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
            {dirMut.isPending ? '生成中…' : (directions ? '重新生成方向' : '生成 3-5 个方向')}
          </button>
          <span className="text-[11px] text-muted">~5-10s · ~$0.03</span>
        </div>
      </div>

      {/* ② 方向卡片 */}
      {directions && directions.length > 0 && (
        <div className="space-y-2">
          <div className="eyebrow text-muted">方向（点卡片选定再生成脚本）</div>
          {directions.map((d, i) => {
            const isChosen = chosen?.name === d.name
            return (
              <button key={i} onClick={() => { setChosen(d); setScript(null) }}
                className={`block w-full text-left p-3 rounded-lg border transition-colors ${isChosen ? 'border-accent bg-accent/10' : 'border-default bg-elevated/40 hover:border-strong'}`}>
                <div className="flex items-baseline justify-between gap-2 mb-1">
                  <span className="font-display font-bold text-sm text-primary">{i + 1}. {d.name}</span>
                  {isChosen && <span className="text-[10px] font-data text-accent">已选</span>}
                </div>
                <div className="text-xs text-secondary mb-2">{d.concept}</div>
                <div className="space-y-1 text-[11px] text-muted">
                  <div><b className="text-secondary">前 3 秒：</b>{d.opening_3sec}</div>
                  <div><b className="text-secondary">借鉴：</b>{d.borrows_from_ref}</div>
                  <div><b className="text-secondary">契合：</b>{d.fit_to_self_product}</div>
                  <div><b className="text-secondary">结尾：</b>{d.ending_cta}</div>
                  {d.risk_notes && <div className="text-red-300/80"><b>避坑：</b>{d.risk_notes}</div>}
                </div>
                {d.key_hooks && d.key_hooks.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {d.key_hooks.map((h, j) => (
                      <span key={j} className="text-[10px] font-data px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">
                        {h.ts_est} · {h.kind}
                      </span>
                    ))}
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* ③ 脚本生成 */}
      {chosen && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="eyebrow text-muted">分镜脚本</div>
            <button
              onClick={() => scriptMut.mutate(chosen)}
              disabled={scriptMut.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent hover:brightness-110 disabled:opacity-50 text-xs font-semibold text-white">
              {scriptMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <FileText size={12} />}
              {scriptMut.isPending ? '生成中…' : (script ? '重新生成脚本' : `为「${chosen.name}」写脚本`)}
            </button>
          </div>
          {scriptMut.isPending && (
            <div className="text-xs text-muted">~10-15s · ~$0.05</div>
          )}
          {script && <ScriptTable script={script} />}
        </div>
      )}
    </section>
  )
}

function ScriptTable({ script }: { script: CreativeScriptResult['data'] }) {
  return (
    <div className="space-y-3">
      <div className="font-data text-[11px] text-muted">
        总时长：{script.total_duration_sec}s · 共 {script.shots?.length ?? 0} 个镜头
      </div>
      <ul className="space-y-2">
        {script.shots?.map((s, i) => (
          <li key={i} className="rounded border border-default bg-elevated/30 p-2.5">
            <div className="flex items-baseline gap-2 mb-1">
              <span className="font-data text-[11px] text-accent shrink-0">{s.ts}</span>
              <span className="text-[10px] font-data px-1.5 rounded bg-base/60 text-muted border border-default">{s.shot_type}</span>
            </div>
            <div className="text-xs text-secondary">{s.visual}</div>
            {s.audio_voiceover && s.audio_voiceover !== '无' && (
              <div className="mt-1 text-[11px] text-muted"><b>口播/音效：</b>{s.audio_voiceover}</div>
            )}
            {s.production_notes && s.production_notes !== '无' && (
              <div className="mt-0.5 text-[11px] text-muted"><b>制作：</b>{s.production_notes}</div>
            )}
          </li>
        ))}
      </ul>
      {script.constraints_check && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-muted hover:text-primary">五条硬约束自检</summary>
          <ul className="mt-2 space-y-0.5 text-[11px] text-secondary">
            <li>① 禁宏大叙事开场：{script.constraints_check.no_grand_opening}</li>
            <li>② 禁 CG 宣传片：{script.constraints_check.no_cg_promo}</li>
            <li>③ 一镜一事：{script.constraints_check.one_event_per_shot}</li>
            <li>④ 0-1.5s 单动作：{script.constraints_check.one_action_in_first_1_5s}</li>
            <li>⑤ 反馈单镜头：{script.constraints_check.feedback_separate_shot}</li>
          </ul>
        </details>
      )}
    </div>
  )
}

export function formatTs(sec: number): string {
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
