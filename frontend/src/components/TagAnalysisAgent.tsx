import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { Sparkles, FileText, FileSpreadsheet, Trash2, Send, Loader2, History, Plus, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { tagAnalysisApi } from '../lib/api'
import { useT } from '../i18n'
import type { TagAnalysisModel } from '../lib/types'

/** AI 标签分析 Agent（P6）：对当前筛选范围的素材「标签 + 已有 AI 分析」做对话式分析。
 * 收进右侧抽屉（筛选区只留一个触发按钮，省得页面拥挤）；一键报告（mode=report）+
 * 自由追问（mode=chat 多轮），会话落库可回查、可导出 md/csv。scope 跟随素材列表的
 * material_type + 游戏 + 分面筛选，走公司 LLM 网关、零 ST 配额。模型下拉旁实时干跑预估
 * 「约 $X」。护栏在后端：范围 0 / >50 条 / 模型白名单 / 日预算，命中报错由全局拦截器 toast。 */
export function TagAnalysisAgent({
  open, onClose, materialType, appId, tagOptions, scopeLabel,
}: {
  open: boolean
  onClose: () => void
  materialType?: string
  appId?: string
  tagOptions?: string
  scopeLabel?: string
}) {
  const t = useT()
  const a = t.materials.agent
  const qc = useQueryClient()

  // hooks 一律在任何 early return 之前（抽屉 prop 切换时 hook 数量必须恒定）
  const [model, setModel] = useState<TagAnalysisModel>('claude-sonnet-4.6')
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const [showHistory, setShowHistory] = useState(false)

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const { data: list } = useQuery({
    queryKey: ['tagAnalysisList'],
    queryFn: () => tagAnalysisApi.list(),
    enabled: open,
  })

  const { data: session } = useQuery({
    queryKey: ['tagAnalysisSession', sessionId],
    queryFn: () => tagAnalysisApi.get(sessionId as number),
    enabled: open && sessionId != null,
    placeholderData: keepPreviousData,
  })

  // 成本实时预估：仅新会话（未选历史）时跑——切模型 / 切范围自动重算。零配额干跑。
  const { data: estimate, isFetching: estimating } = useQuery({
    queryKey: ['tagAnalysisEstimate', model, appId, materialType, tagOptions],
    queryFn: () => tagAnalysisApi.estimate({
      model, app_id: appId, material_type: materialType, tag_options: tagOptions,
    }),
    enabled: open && sessionId == null,
    placeholderData: keepPreviousData,
  })

  const run = useMutation({
    mutationFn: tagAnalysisApi.run,
    onSuccess: (data) => {
      setSessionId(data.id)
      setInput('')
      qc.setQueryData(['tagAnalysisSession', data.id], data)
      qc.invalidateQueries({ queryKey: ['tagAnalysisList'] })
    },
  })

  const del = useMutation({
    mutationFn: (id: number) => tagAnalysisApi.del(id),
    onSuccess: (_d, id) => {
      if (sessionId === id) setSessionId(null)
      qc.invalidateQueries({ queryKey: ['tagAnalysisList'] })
      toast.success(a.deleted)
    },
  })

  if (!open) return null

  const busy = run.isPending
  const messages = session?.messages ?? []

  const report = () => run.mutate({
    mode: 'report', model,
    material_type: materialType, app_id: appId, tag_options: tagOptions,
  })
  const ask = () => {
    const msg = input.trim()
    if (!msg || sessionId == null || busy) return
    run.mutate({ session_id: sessionId, mode: 'chat', model, message: msg })
  }
  const newSession = () => { setSessionId(null); setShowHistory(false); run.reset() }

  // 预估文案：超限 / 空 / 金额三态
  const estimateText = !estimate ? (estimating ? a.estimating : '')
    : estimate.over_limit ? a.estimateOverLimit(estimate.material_count, estimate.limit)
    : estimate.empty ? a.estimateEmpty
    : a.estimateAbout(estimate.estimated_cost_usd, estimate.material_count)
  const reportDisabled = busy || (estimate != null && (estimate.empty || estimate.over_limit))

  const selectClass = "bg-elevated border border-default rounded-lg px-2.5 py-1.5 text-xs text-primary focus:outline-none focus:border-brand-500"
  const btnGhost = "inline-flex items-center gap-1.5 rounded-lg border border-default px-2.5 py-1.5 text-[11px] text-secondary hover:border-strong hover:text-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"

  return (
    <>
      <div className="fixed inset-0 z-40 bg-base/70 backdrop-blur-sm" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full sm:w-[560px] flex-col bg-surface border-l border-strong shadow-2xl">
        {/* 抽屉头 */}
        <div className="flex items-center gap-2 border-b border-default px-4 py-3 shrink-0">
          <Sparkles size={16} className="text-accent" />
          <span className="text-sm font-semibold text-primary">{a.title}</span>
          <span className="text-[11px] text-muted hidden sm:inline">· {a.hint}</span>
          <button onClick={onClose} className="ml-auto text-muted hover:text-primary transition-colors" title={a.close}>
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {/* 控制条：模型 + 预估 / 历史 / 新建 */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <label className="flex items-center gap-2 text-[11px] text-secondary">
              {a.model}
              <select value={model} onChange={e => setModel(e.target.value as TagAnalysisModel)} className={selectClass} disabled={busy}>
                <option value="claude-sonnet-4.6">{a.modelSonnet}</option>
                <option value="claude-opus-4.8">{a.modelOpus}</option>
              </select>
            </label>

            {/* 实时成本预估（仅新会话） */}
            {sessionId == null && estimateText && (
              <span className={`inline-flex items-center gap-1 text-[11px] ${
                estimate?.over_limit || estimate?.empty ? 'text-amber-400' : 'text-emerald-400'
              }`}>
                {estimating && <Loader2 size={11} className="animate-spin" />}
                {estimateText}
              </span>
            )}

            <div className="relative ml-auto">
              <button className={btnGhost} onClick={() => setShowHistory(s => !s)}>
                <History size={13} /> {a.history}{list?.length ? ` (${list.length})` : ''}
              </button>
              {showHistory && (
                <div className="absolute right-0 z-20 mt-1 w-72 max-h-64 overflow-auto rounded-lg border border-default bg-elevated shadow-lg">
                  {!list?.length ? (
                    <p className="px-3 py-2 text-[11px] text-muted">{a.historyEmpty}</p>
                  ) : list.map(s => (
                    <div key={s.id} className={`flex items-center gap-2 px-2.5 py-1.5 text-[11px] hover:bg-surface/60 ${s.id === sessionId ? 'bg-surface/40' : ''}`}>
                      <button className="flex-1 truncate text-left text-secondary hover:text-primary" title={s.title}
                        onClick={() => { setSessionId(s.id); setShowHistory(false) }}>
                        {s.title}
                      </button>
                      <button className="shrink-0 text-muted hover:text-rose-400" title={a.del}
                        onClick={() => del.mutate(s.id)}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {sessionId != null && (
              <button className={btnGhost} onClick={newSession}><Plus size={13} /> {a.newSession}</button>
            )}
          </div>

          <p className="text-[11px] text-muted">{a.scopeHint(scopeLabel)}</p>

          {/* 会话线程 */}
          {messages.length === 0 ? (
            <div className="space-y-2 py-1">
              <p className="text-[11px] text-muted">{a.scopeNote}</p>
              <p className="text-xs text-muted">{a.empty}</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {messages.map(msg => (
                <div key={msg.id} className={msg.role === 'user' ? 'flex justify-end' : ''}>
                  <div className={`max-w-[92%] rounded-lg px-3 py-2 text-xs leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-accent/15 border border-accent/30 text-primary'
                      : 'bg-elevated/60 border border-default/60 text-primary'
                  }`}>
                    <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
                      {msg.role === 'user' ? a.roleUser : a.roleAssistant}
                      {msg.role === 'assistant' && msg.cost_usd != null && msg.material_count != null && (
                        <span className="normal-case tracking-normal">· {a.meta(msg.model || model, msg.cost_usd, msg.material_count)}</span>
                      )}
                    </div>
                    <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {busy && (
            <p className="flex items-center gap-2 text-[11px] text-muted">
              <Loader2 size={13} className="animate-spin" /> {a.reportRunning}
            </p>
          )}
        </div>

        {/* 操作区（固定底部） */}
        <div className="border-t border-default px-4 py-3 shrink-0">
          {sessionId == null ? (
            <button
              onClick={report}
              disabled={reportDisabled}
              className="inline-flex items-center gap-2 rounded-lg bg-accent/80 px-3.5 py-2 text-xs font-medium text-white hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {a.report}
            </button>
          ) : (
            <div className="space-y-2">
              <div className="flex items-end gap-2">
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask() } }}
                  rows={2}
                  placeholder={a.askPlaceholder}
                  disabled={busy}
                  className="flex-1 resize-none rounded-lg border border-default bg-elevated px-3 py-2 text-xs text-primary focus:outline-none focus:border-brand-500 disabled:opacity-50"
                />
                <button
                  onClick={ask}
                  disabled={busy || !input.trim()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-accent/80 px-3 py-2 text-xs font-medium text-white hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <Send size={13} /> {a.send}
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button className={btnGhost} onClick={() => tagAnalysisApi.exportFile(sessionId, 'md')}>
                  <FileText size={13} /> {a.exportMd}
                </button>
                <button className={btnGhost} onClick={() => tagAnalysisApi.exportFile(sessionId, 'csv')}>
                  <FileSpreadsheet size={13} /> {a.exportCsv}
                </button>
                <button className={`${btnGhost} hover:!text-rose-400`} onClick={() => del.mutate(sessionId)}>
                  <Trash2 size={13} /> {a.del}
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  )
}
