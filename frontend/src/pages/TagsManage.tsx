import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Plus, Trash2, Pencil, X, Calendar, Type, Asterisk, Globe2, Package, Lock, Save, Copy, ChevronUp, ChevronDown, ArrowUpToLine, ChevronRight } from 'lucide-react'
import { tagsApi, gamesApi } from '../lib/api'
import { useT } from '../i18n'
import { PageHeader } from '../components/PageHeader'
import { QueryError } from '../components/QueryError'
import type { TagDimension, TagOption, TagValueType, GameOut } from '../lib/types'

// 计算「字符数」用 code point 长度（[...s].length），与后端 max_length=8 口径一致；
// 直接 s.length 会把代理对算成 2、给中文/emoji 错误计数。
const charLen = (s: string) => [...s].length

type DimForm = {
  name: string; value_type: TagValueType; is_required: boolean; allow_multi: boolean
  // 产品作用域名单（S1）：空 = 通用；非空 = 仅名单内 app_id 可见。
  app_ids: string[]
}
const EMPTY_DIM: DimForm = { name: '', value_type: 'text', is_required: false, allow_multi: true, app_ids: [] }
type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; id: number }

const QK = ['tagDimensions'] as const

export default function TagsManage() {
  const t = useT()
  const tt = t.tagsManage
  const qc = useQueryClient()
  const [viewMode, setViewMode] = useState<'tag' | 'product'>('tag')
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [form, setForm] = useState<DimForm>(EMPTY_DIM)
  // 每个一级标签卡片下「新增二级标签」输入框各自独立
  const [newOpt, setNewOpt] = useState<Record<number, string>>({})

  const isEditing = mode.kind === 'edit'
  const isOpen = mode.kind !== 'closed'

  const { data: dims = [], isLoading, isError, refetch } = useQuery({
    queryKey: QK,
    queryFn: () => tagsApi.listDimensions(),
  })
  // 产品作用域 picker 的候选游戏：与 Materials 页同 queryKey 共享缓存
  const { data: allGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })
  const gameMap = useMemo(
    () => Object.fromEntries(allGames.map(g => [g.app_id, g.name])),
    [allGames],
  )
  // 选项作用域编辑 modal 态：当前正在编辑作用域的二级标签 id
  const [scopeModal, setScopeModal] = useState<{ opt: TagOption; dim: TagDimension } | null>(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: QK })

  const createDimMut = useMutation({
    mutationFn: (data: DimForm) => tagsApi.createDimension(data),
    onSuccess: () => { invalidate(); closeForm(); toast.success(tt.dimAdded) },
  })
  const updateDimMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<DimForm> }) => tagsApi.updateDimension(id, data),
    onSuccess: () => { invalidate(); closeForm(); toast.success(tt.dimUpdated) },
  })
  const deleteDimMut = useMutation({
    mutationFn: ({ id, password }: { id: number; password?: string }) => tagsApi.deleteDimension(id, password),
    onSuccess: () => { invalidate(); toast.success(tt.dimDeleted) },
  })

  const createOptMut = useMutation({
    mutationFn: ({ dimId, value }: { dimId: number; value: string }) => tagsApi.createOption(dimId, { value }),
    onSuccess: (_o, { dimId }) => { invalidate(); setNewOpt(s => ({ ...s, [dimId]: '' })); toast.success(tt.optAdded) },
  })
  const updateOptMut = useMutation({
    mutationFn: ({ optId, value }: { optId: number; value: string }) => tagsApi.updateOption(optId, { value }),
    onSuccess: () => { invalidate(); toast.success(tt.optRenamed) },
  })
  const deleteOptMut = useMutation({
    mutationFn: ({ optId, password }: { optId: number; password?: string }) => tagsApi.deleteOption(optId, password),
    onSuccess: () => { invalidate(); toast.success(tt.optDeleted) },
  })

  // 维度排序（上移/下移/置顶）：乐观更新缓存里的顺序 + 提交完整 id 序，失败回滚重拉。
  const reorderMut = useMutation({
    mutationFn: (orderedIds: number[]) => tagsApi.reorderDimensions(orderedIds),
    onMutate: async (orderedIds) => {
      await qc.cancelQueries({ queryKey: QK })
      const prev = qc.getQueryData<TagDimension[]>(QK)
      if (prev) {
        const byId = new Map(prev.map(d => [d.id, d]))
        qc.setQueryData<TagDimension[]>(QK, orderedIds.map(id => byId.get(id)!).filter(Boolean))
      }
      return { prev }
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(QK, ctx.prev); toast.error(tt.reorderFailed) },
    onSettled: () => invalidate(),
  })
  const moveDim = (idx: number, dir: -1 | 1 | 'top') => {
    const ids = dims.map(d => d.id)
    if (dir === 'top') {
      if (idx === 0) return
      const [id] = ids.splice(idx, 1); ids.unshift(id)
    } else {
      const j = idx + dir
      if (j < 0 || j >= ids.length) return
      ;[ids[idx], ids[j]] = [ids[j], ids[idx]]
    }
    reorderMut.mutate(ids)
  }

  // 维度卡折叠态（本地，不持久化）：折叠后只留标题行，20+ 维度一屏看全、排序更快。
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const toggleCollapse = (id: number) =>
    setCollapsed(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const allCollapsed = dims.length > 0 && dims.every(d => collapsed.has(d.id))
  const toggleCollapseAll = () =>
    setCollapsed(allCollapsed ? new Set() : new Set(dims.map(d => d.id)))

  function closeForm() { setMode({ kind: 'closed' }); setForm(EMPTY_DIM) }
  function openCreate() { setMode({ kind: 'create' }); setForm(EMPTY_DIM) }
  function openEdit(d: TagDimension) {
    setMode({ kind: 'edit', id: d.id })
    setForm({
      name: d.name, value_type: d.value_type, is_required: d.is_required,
      allow_multi: d.allow_multi, app_ids: [...(d.app_ids ?? [])],
    })
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const name = form.name.trim()
    if (!name) { toast.error(tt.nameRequired); return }
    if (charLen(name) > 8) { toast.error(tt.nameTooLong); return }
    if (mode.kind === 'create') {
      createDimMut.mutate({ ...form, name })
    } else if (mode.kind === 'edit') {
      // value_type 不可改，仅提交可变字段；app_ids 走 replace-all（[] 即改回通用）
      updateDimMut.mutate({ id: mode.id, data: {
        name, is_required: form.is_required, allow_multi: form.allow_multi, app_ids: form.app_ids,
      } })
    }
  }

  // 删除走管理员口令 gate：一次 prompt 同时承担「二次确认 + 收口令」。
  // 取消（点 Cancel）→ null → 中止；本地未设口令则留空直接确定。
  const askDelete = (warn: string): string | null => {
    const pw = window.prompt(`${warn}\n\n${tt.adminPromptHint}`, '')
    return pw // null = 取消；'' / 文本 = 继续
  }
  const handleDeleteDim = (d: TagDimension) => {
    const pw = askDelete(tt.confirmDeleteDim(d.name, d.options.length))
    if (pw === null) return
    deleteDimMut.mutate({ id: d.id, password: pw || undefined })
  }
  const handleDeleteOpt = (o: TagOption) => {
    const pw = askDelete(tt.confirmDeleteOpt(o.value))
    if (pw === null) return
    deleteOptMut.mutate({ optId: o.id, password: pw || undefined })
  }
  const handleAddOpt = (dimId: number) => {
    const value = (newOpt[dimId] || '').trim()
    if (!value) return
    if (charLen(value) > 8) { toast.error(tt.optTooLong); return }
    createOptMut.mutate({ dimId, value })
  }
  const handleRenameOpt = (o: TagOption) => {
    const next = window.prompt(tt.renameOption(o.value), o.value)
    if (next === null) return
    const value = next.trim()
    if (!value || value === o.value) return
    if (charLen(value) > 8) { toast.error(tt.optTooLong); return }
    updateOptMut.mutate({ optId: o.id, value })
  }

  const submitting = createDimMut.isPending || updateDimMut.isPending
  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="Tags" title={tt.title} subtitle={tt.subtitle}>
        {viewMode === 'tag' && (
          <button
            onClick={() => isOpen ? closeForm() : openCreate()}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent transition-all"
          >
            <Plus size={14} />
            {tt.add}
          </button>
        )}
      </PageHeader>

      {/* 视图切换：标签视角（逐标签配产品）/ 产品视角（选产品批量收窄专属标签）*/}
      <div className="inline-flex rounded-lg border border-default bg-surface p-0.5 text-sm">
        {(['tag', 'product'] as const).map(m => (
          <button key={m} onClick={() => { setViewMode(m); closeForm() }}
            className={`px-3 py-1.5 rounded-md transition-colors ${viewMode === m
              ? 'bg-elevated text-primary font-semibold' : 'text-secondary hover:text-primary'}`}>
            {m === 'tag' ? tt.viewByTag : tt.viewByProduct}
          </button>
        ))}
      </div>

      {viewMode === 'product' && (
        <ProductScopeView
          dims={dims} games={allGames} gameMap={gameMap}
          inputClass={inputClass} onSaved={invalidate}
        />
      )}

      {viewMode === 'tag' && isOpen && (
        <form onSubmit={handleSubmit} className="bg-surface border border-default rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-primary">
            {isEditing ? tt.editDimTitle : tt.addDimTitle}
          </h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs text-secondary mb-1">{tt.nameLabel}</label>
              <input
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder={tt.namePlaceholder}
                className={`w-full ${inputClass}`}
              />
            </div>
            <div>
              <label className="block text-xs text-secondary mb-1">{tt.valueTypeLabel}</label>
              <select
                value={form.value_type}
                disabled={isEditing}
                onChange={e => setForm(f => ({ ...f, value_type: e.target.value as TagValueType }))}
                className={`w-full ${inputClass} disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                <option value="text">{tt.typeText}</option>
                <option value="date">{tt.typeDate}</option>
              </select>
              <p className="text-[11px] text-muted mt-1">
                {isEditing ? tt.typeImmutableHint : form.value_type === 'date' ? tt.typeDateHint : tt.typeTextHint}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-5">
            <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer select-none">
              <input type="checkbox" checked={form.is_required}
                onChange={e => setForm(f => ({ ...f, is_required: e.target.checked }))}
                className="accent-brand-500" />
              {tt.requiredLabel}
            </label>
            <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer select-none">
              <input type="checkbox" checked={form.allow_multi}
                onChange={e => setForm(f => ({ ...f, allow_multi: e.target.checked }))}
                className="accent-brand-500" />
              {tt.multiLabel}
            </label>
          </div>
          <ProductScopePicker
            value={form.app_ids}
            onChange={ids => setForm(f => ({ ...f, app_ids: ids }))}
            games={allGames}
            gameMap={gameMap}
            inputClass={inputClass}
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={closeForm}
              className="px-3 py-1.5 text-sm text-secondary hover:text-primary">{t.common.cancel}</button>
            <button type="submit" disabled={submitting}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {submitting ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {viewMode === 'tag' && (isError ? (
        <QueryError compact onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="text-center text-muted text-sm py-12">{t.common.loading}</div>
      ) : dims.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.empty}</div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-end">
            <button onClick={toggleCollapseAll}
              className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-primary transition-colors">
              {allCollapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
              {allCollapsed ? tt.expandAll : tt.collapseAll}
            </button>
          </div>
          {dims.map((d, idx) => {
            const isCollapsed = collapsed.has(d.id)
            return (
            <div key={d.id} className="bg-surface border border-default rounded-xl p-4 space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 flex-wrap min-w-0">
                  <button onClick={() => toggleCollapse(d.id)} title={isCollapsed ? tt.expand : tt.collapse}
                    className="shrink-0 p-0.5 -ml-1 text-muted hover:text-primary transition-colors">
                    {isCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
                  </button>
                  <button onClick={() => toggleCollapse(d.id)}
                    className="font-display font-bold text-primary truncate hover:text-brand-400 transition-colors">{d.name}</button>
                  <span className="inline-flex items-center gap-1 text-[10px] text-secondary border border-default bg-elevated rounded px-1.5 py-0.5 shrink-0">
                    {d.value_type === 'date' ? <Calendar size={10} /> : <Type size={10} />}
                    {d.value_type === 'date' ? tt.typeDate : tt.typeText}
                  </span>
                  {d.is_required && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0">
                      <Asterisk size={10} /> {tt.badgeRequired}
                    </span>
                  )}
                  <span className="text-[10px] text-muted border border-default rounded px-1.5 py-0.5 shrink-0">
                    {d.allow_multi ? tt.badgeMulti : tt.badgeSingle}
                  </span>
                  {d.value_type !== 'date' && (
                    <span className="text-[10px] text-muted shrink-0">{tt.optionsLabel} {d.options.length}</span>
                  )}
                  {/* 产品作用域徽标（S1）：通用 / 仅 N 个产品；hover 提示具体产品名 */}
                  {(() => {
                    const ids = d.app_ids ?? []
                    return ids.length === 0 ? (
                      <span className="inline-flex items-center gap-1 text-[10px] text-muted border border-default rounded px-1.5 py-0.5 shrink-0"
                        title={tt.scopeUniversal}>
                        <Globe2 size={10} /> {tt.scopeUniversal}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0"
                        title={ids.map(a => gameMap[a] || a).join(' · ')}>
                        <Package size={10} /> {tt.scopeNGames(ids.length)}
                      </span>
                    )
                  })()}
                </div>
                <div className="flex items-center gap-0.5 shrink-0">
                  <button onClick={() => moveDim(idx, 'top')} disabled={idx === 0 || reorderMut.isPending} title={tt.moveTop}
                    className="p-1.5 text-muted hover:text-brand-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"><ArrowUpToLine size={14} /></button>
                  <button onClick={() => moveDim(idx, -1)} disabled={idx === 0 || reorderMut.isPending} title={tt.moveUp}
                    className="p-1.5 text-muted hover:text-brand-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"><ChevronUp size={14} /></button>
                  <button onClick={() => moveDim(idx, 1)} disabled={idx === dims.length - 1 || reorderMut.isPending} title={tt.moveDown}
                    className="p-1.5 text-muted hover:text-brand-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"><ChevronDown size={14} /></button>
                  <span className="w-px h-4 bg-default mx-0.5" />
                  <button onClick={() => openEdit(d)} title={t.common.edit}
                    className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={14} /></button>
                  <button onClick={() => handleDeleteDim(d)} disabled={deleteDimMut.isPending} title={t.common.delete}
                    className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={14} /></button>
                </div>
              </div>

              {isCollapsed ? null : d.value_type === 'date' ? (
                <p className="text-xs text-muted border-t border-default pt-3">{tt.dateNoOptions}</p>
              ) : (
                <div className="border-t border-default pt-3 space-y-2">
                  <div className="text-[11px] text-secondary">{tt.optionsLabel}（{d.options.length}）</div>
                  <div className="flex flex-wrap items-center gap-2">
                    {d.options.map(o => {
                      const optIds = o.app_ids ?? []
                      return (
                        <span key={o.id}
                          className="group inline-flex items-center gap-1.5 text-xs text-primary bg-elevated border border-default rounded-lg pl-2.5 pr-1.5 py-1">
                          <button onClick={() => handleRenameOpt(o)} title={t.common.edit}
                            className="hover:text-brand-400 transition-colors">{o.value}</button>
                          {/* 选项作用域（S2）：通用→只 hover 显淡色 / 限定→显徽标 */}
                          <button type="button" onClick={() => setScopeModal({ opt: o, dim: d })}
                            title={optIds.length === 0 ? tt.scopeUniversal : tt.scopeNGames(optIds.length) + '：' + optIds.map(a => gameMap[a] || a).join(' · ')}
                            className={`inline-flex items-center gap-0.5 transition-colors ${optIds.length === 0
                              ? 'opacity-0 group-hover:opacity-100 text-muted hover:text-brand-400'
                              : 'text-accent hover:brightness-110'}`}>
                            {optIds.length === 0 ? <Globe2 size={10} /> : (<>
                              <Package size={10} /><span className="text-[10px]">{optIds.length}</span>
                            </>)}
                          </button>
                          <button onClick={() => handleDeleteOpt(o)} title={t.common.delete}
                            className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                        </span>
                      )
                    })}
                    <span className="inline-flex items-center gap-1">
                      <input
                        value={newOpt[d.id] || ''}
                        onChange={e => setNewOpt(s => ({ ...s, [d.id]: e.target.value }))}
                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddOpt(d.id) } }}
                        placeholder={tt.addOptionPlaceholder}
                        className="bg-elevated border border-default rounded-lg px-2.5 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 w-32"
                      />
                      <button onClick={() => handleAddOpt(d.id)} disabled={createOptMut.isPending}
                        className="p-1 text-muted hover:text-accent transition-colors" title={tt.addOption}>
                        <Plus size={14} />
                      </button>
                    </span>
                  </div>
                </div>
              )}
            </div>
          )})}
        </div>
      ))}

      {scopeModal && (
        <OptionScopeModal
          opt={scopeModal.opt}
          dim={scopeModal.dim}
          games={allGames}
          gameMap={gameMap}
          inputClass={inputClass}
          onClose={() => setScopeModal(null)}
          onSaved={() => { invalidate(); setScopeModal(null) }}
        />
      )}
    </div>
  )
}

// ── 复用：产品作用域多选 picker（chip + 搜索 + 滚动候选区）─────────────────
type PickerProps = {
  value: string[]
  onChange: (next: string[]) => void
  games: GameOut[]
  gameMap: Record<string, string>
  inputClass: string
}
function ProductScopePicker({ value, onChange, games, gameMap, inputClass }: PickerProps) {
  const t = useT()
  const tt = t.tagsManage
  const [q, setQ] = useState('')
  const filtered = useMemo(() => {
    const k = q.trim().toLowerCase()
    if (!k) return games
    return games.filter(g => g.name.toLowerCase().includes(k) || g.app_id.toLowerCase().includes(k))
  }, [games, q])
  const toggle = (aid: string) => onChange(
    value.includes(aid) ? value.filter(x => x !== aid) : [...value, aid]
  )
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <label className="text-xs text-secondary">{tt.scopeLabel}</label>
        <span className="text-[10px] text-muted">{tt.scopeHint}</span>
      </div>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map(aid => (
            <span key={aid}
              className="inline-flex items-center gap-1 text-[11px] text-accent bg-accent/10 border border-accent/40 rounded pl-2 pr-1 py-0.5">
              {gameMap[aid] || aid}
              <button type="button" onClick={() => toggle(aid)}
                className="text-muted hover:text-red-400"><X size={10} /></button>
            </span>
          ))}
          <button type="button" onClick={() => onChange([])}
            className="text-[11px] text-muted hover:text-red-400 px-1">{tt.scopeClearAll}</button>
        </div>
      )}
      <input value={q} onChange={e => setQ(e.target.value)}
        placeholder={tt.scopeSearchPlaceholder} className={`w-full ${inputClass}`} />
      <div className="max-h-40 overflow-y-auto border border-default rounded-lg bg-elevated">
        {filtered.length === 0 ? (
          <p className="text-[11px] text-muted px-3 py-2">{t.common.noData}</p>
        ) : (
          <div className="divide-y divide-default">
            {filtered.map(g => {
              const checked = value.includes(g.app_id)
              return (
                <label key={g.app_id}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs text-secondary hover:bg-surface cursor-pointer">
                  <input type="checkbox" checked={checked} onChange={() => toggle(g.app_id)}
                    className="accent-brand-500" />
                  <span className="text-primary truncate">{g.name}</span>
                  <span className="text-[10px] text-muted ml-auto">{g.app_id}</span>
                </label>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 二级标签作用域编辑 modal（S2）──────────────────────────────────────────
type OptionScopeModalProps = {
  opt: TagOption
  dim: TagDimension
  games: GameOut[]
  gameMap: Record<string, string>
  inputClass: string
  onClose: () => void
  onSaved: () => void
}
function OptionScopeModal({ opt, dim, games, gameMap, inputClass, onClose, onSaved }: OptionScopeModalProps) {
  const t = useT()
  const tt = t.tagsManage
  const [ids, setIds] = useState<string[]>(opt.app_ids ?? [])
  // opt 切换时同步初值（不同选项点开同一个 modal）
  useEffect(() => { setIds(opt.app_ids ?? []) }, [opt.id, opt.app_ids])
  const mut = useMutation({
    mutationFn: () => tagsApi.updateOption(opt.id, { app_ids: ids }),
    onSuccess: () => { toast.success(tt.optScopeSaved); onSaved() },
  })
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}>
      <div className="bg-surface border border-default rounded-xl p-5 max-w-lg w-full space-y-4"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-primary">{tt.optScopeTitle}</h3>
            <p className="text-[11px] text-muted mt-0.5">
              {dim.name} · <span className="text-secondary">{opt.value}</span>
            </p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-red-400"><X size={14} /></button>
        </div>
        <ProductScopePicker value={ids} onChange={setIds}
          games={games} gameMap={gameMap} inputClass={inputClass} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="px-3 py-1.5 text-sm text-secondary hover:text-primary">{t.common.cancel}</button>
          <button type="button" disabled={mut.isPending} onClick={() => mut.mutate()}
            className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
            {mut.isPending ? t.common.saving : t.common.save}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 产品视角（S4）：选一个产品 → 一屏批量把「通用」标签收窄成「该产品专属」──────
// 语义只做干净的「通用 ⇄ 仅该产品」翻转；多产品 / 属他产品的复杂作用域只读展示，
// 不让一键勾选误覆盖（白名单是加法语义，clobber 会抹掉别人的名单）。
type ProductScopeViewProps = {
  dims: TagDimension[]
  games: GameOut[]
  gameMap: Record<string, string>
  inputClass: string
  onSaved: () => void
}
function ProductScopeView({ dims, games, gameMap, inputClass, onSaved }: ProductScopeViewProps) {
  const t = useT()
  const tt = t.tagsManage
  const [pid, setPid] = useState('')
  // 仅存「与原值不同」的改动行：维度/选项各一份 map（key=id，value=新 app_ids）
  const [dimDraft, setDimDraft] = useState<Record<number, string[]>>({})
  const [optDraft, setOptDraft] = useState<Record<number, string[]>>({})

  const pending = Object.keys(dimDraft).length + Object.keys(optDraft).length

  // 切换产品前若有未保存改动，确认丢弃（避免静默丢工作）
  function switchProduct(next: string) {
    if (pending > 0 && !window.confirm(t.common.discardChanges)) return
    setPid(next); setDimDraft({}); setOptDraft({})
  }

  const mut = useMutation({
    mutationFn: () => tagsApi.scopeBatch({
      dimensions: Object.entries(dimDraft).map(([id, app_ids]) => ({ id: Number(id), app_ids })),
      options: Object.entries(optDraft).map(([id, app_ids]) => ({ id: Number(id), app_ids })),
    }),
    onSuccess: (res) => {
      toast.success(tt.scopeBatchSaved(res.updated_dimensions, res.updated_options))
      setDimDraft({}); setOptDraft({}); onSaved()
    },
  })

  // 模板复制（P1）：以另一产品的专属维度为模板克隆给当前产品（新品建库场景，如 Kingshot）。
  // 后端克隆语义 + 幂等（同名可见维度自动跳过），前端只负责选源和展示结果。
  const [copySrc, setCopySrc] = useState('')
  const [copyOpts, setCopyOpts] = useState(true)
  const copyMut = useMutation({
    mutationFn: () => tagsApi.copyTemplate({
      source_app_id: copySrc, target_app_id: pid, include_options: copyOpts,
    }),
    onSuccess: (res) => {
      if (res.copied.length === 0) toast(tt.copyTemplateAllSkipped(res.skipped.length))
      else toast.success(tt.copyTemplateDone(res.copied.length, res.options_copied, res.skipped.length))
      setCopySrc(''); onSaved()
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || tt.copyTemplateFailed),
  })
  // 只有「有专属维度」的产品才配当模板源（通用维度后端本就不复制）
  const templateSources = useMemo(() => {
    const scoped = new Set(dims.flatMap(d => d.app_ids ?? []))
    return games.filter(g => g.app_id !== pid && scoped.has(g.app_id))
  }, [dims, games, pid])

  // 行状态：universal（通用，可勾）/ exclusive（仅本产品，可取消）/ complex（只读）
  type RowKind = 'universal' | 'exclusive' | 'complex'
  const classify = (orig: string[]): RowKind => {
    if (orig.length === 0) return 'universal'
    if (orig.length === 1 && orig[0] === pid) return 'exclusive'
    return 'complex'
  }
  // 当前生效勾选态（draft 覆盖原值）
  const isChecked = (id: number, orig: string[], draft: Record<number, string[]>) => {
    const cur = draft[id] ?? orig
    return cur.length === 1 && cur[0] === pid
  }
  const toggle = (
    id: number, orig: string[],
    draft: Record<number, string[]>, setDraft: React.Dispatch<React.SetStateAction<Record<number, string[]>>>,
  ) => {
    const next = isChecked(id, orig, draft) ? [] : [pid]
    setDraft(prev => {
      const n = { ...prev }
      // next 与原值相同 → 抵消，移出 draft
      if (next.length === orig.length && next.every((x, i) => x === orig[i])) delete n[id]
      else n[id] = next
      return n
    })
  }

  // 复杂作用域行的只读说明：列出名单里的产品名
  const otherNames = (ids: string[]) => ids.map(a => gameMap[a] || a).join(' · ')

  // 单行复选（维度 / 选项共用）
  function ScopeRow({ kind, orig, draft, setDraft, id, label, badges }: {
    kind: RowKind; orig: string[]
    draft: Record<number, string[]>
    setDraft: React.Dispatch<React.SetStateAction<Record<number, string[]>>>
    id: number; label: React.ReactNode; badges?: React.ReactNode
  }) {
    if (kind === 'complex') {
      return (
        <div className="flex items-center gap-2 text-xs" title={tt.scopeOtherProductsHint}>
          <Lock size={12} className="text-muted shrink-0" />
          <span className="text-secondary truncate">{label}</span>
          {badges}
          <span className="text-[10px] text-muted truncate ml-auto">{tt.scopeOtherProducts(otherNames(orig))}</span>
        </div>
      )
    }
    const checked = isChecked(id, orig, draft)
    const dirty = draft[id] !== undefined
    return (
      <label className={`flex items-center gap-2 text-xs cursor-pointer select-none ${dirty ? 'text-accent' : 'text-secondary'}`}>
        <input type="checkbox" checked={checked} onChange={() => toggle(id, orig, draft, setDraft)}
          className="accent-brand-500" />
        <span className="text-primary truncate">{label}</span>
        {badges}
        {checked && <span className="text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0">{tt.restrictToProduct}</span>}
      </label>
    )
  }

  return (
    <div className="space-y-4">
      <div className="bg-surface border border-default rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-secondary">{tt.productPickLabel}</label>
          <select value={pid} onChange={e => switchProduct(e.target.value)}
            className={inputClass}>
            <option value="">{tt.productPickPlaceholder}</option>
            {games.length === 0
              ? <option disabled>{tt.noProducts}</option>
              : games.map(g => <option key={g.app_id} value={g.app_id}>{g.name}（{g.app_id}）</option>)}
          </select>
          {pending > 0 && (
            <span className="text-[11px] text-accent ml-auto">{tt.pendingChanges(pending)}</span>
          )}
          <button type="button" disabled={pending === 0 || mut.isPending}
            onClick={() => mut.mutate()}
            className="flex items-center gap-1.5 px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm text-white transition-colors">
            <Save size={13} />
            {mut.isPending ? t.common.saving : t.common.save}
          </button>
        </div>
        {pid && <p className="text-[11px] text-muted">{tt.productViewHint}</p>}
      </div>

      {/* 模板复制：给刚建档的新产品一键克隆另一产品的整套专属维度（复制后各自独立演进） */}
      {pid && templateSources.length > 0 && (
        <div className="bg-surface border border-default rounded-xl p-4 flex flex-wrap items-center gap-3">
          <span className="text-xs text-secondary">{tt.copyTemplateLabel}</span>
          <select value={copySrc} onChange={e => setCopySrc(e.target.value)} className={inputClass}>
            <option value="">{tt.copyTemplatePlaceholder}</option>
            {templateSources.map(g => <option key={g.app_id} value={g.app_id}>{g.name}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-secondary cursor-pointer select-none">
            <input type="checkbox" checked={copyOpts} onChange={e => setCopyOpts(e.target.checked)}
              className="accent-brand-500" />
            {tt.copyTemplateWithOptions}
          </label>
          <button type="button" disabled={!copySrc || copyMut.isPending}
            onClick={() => {
              const src = templateSources.find(g => g.app_id === copySrc)
              if (window.confirm(tt.copyTemplateConfirm(src?.name || copySrc, gameMap[pid] || pid))) copyMut.mutate()
            }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs border border-default text-secondary hover:text-accent hover:border-accent/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
            <Copy size={13} />
            {copyMut.isPending ? t.common.saving : tt.copyTemplateBtn}
          </button>
          <span className="text-[10px] text-muted basis-full">{tt.copyTemplateHint}</span>
        </div>
      )}

      {!pid ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.productViewEmpty}</div>
      ) : (
        <div className="space-y-3">
          {dims.map(d => {
            const dOrig = d.app_ids ?? []
            const dKind = classify(dOrig)
            return (
              <div key={d.id} className="bg-surface border border-default rounded-xl p-4 space-y-3">
                <div className="border-b border-default pb-3">
                  <ScopeRow kind={dKind} orig={dOrig} draft={dimDraft} setDraft={setDimDraft}
                    id={d.id}
                    label={<span className="font-display font-bold">{d.name}</span>}
                    badges={d.is_required ? (
                      <span className="inline-flex items-center gap-1 text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0">
                        <Asterisk size={10} /> {tt.badgeRequired}
                      </span>
                    ) : undefined}
                  />
                </div>
                {d.value_type === 'date' ? (
                  <p className="text-[11px] text-muted">{tt.dateNoOptions}</p>
                ) : d.options.length === 0 ? (
                  <p className="text-[11px] text-muted">{tt.optionsLabel}（0）</p>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {d.options.map(o => {
                      const oOrig = o.app_ids ?? []
                      return (
                        <ScopeRow key={o.id} kind={classify(oOrig)} orig={oOrig}
                          draft={optDraft} setDraft={setOptDraft} id={o.id} label={o.value} />
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
