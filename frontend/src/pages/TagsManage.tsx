import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Plus, Trash2, Pencil, X, Calendar, Type, Asterisk } from 'lucide-react'
import { tagsApi } from '../lib/api'
import { useT } from '../i18n'
import { PageHeader } from '../components/PageHeader'
import { QueryError } from '../components/QueryError'
import type { TagDimension, TagOption, TagValueType } from '../lib/types'

// 计算「字符数」用 code point 长度（[...s].length），与后端 max_length=8 口径一致；
// 直接 s.length 会把代理对算成 2、给中文/emoji 错误计数。
const charLen = (s: string) => [...s].length

type DimForm = { name: string; value_type: TagValueType; is_required: boolean; allow_multi: boolean }
const EMPTY_DIM: DimForm = { name: '', value_type: 'text', is_required: false, allow_multi: true }
type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; id: number }

const QK = ['tagDimensions'] as const

export default function TagsManage() {
  const t = useT()
  const tt = t.tagsManage
  const qc = useQueryClient()
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

  function closeForm() { setMode({ kind: 'closed' }); setForm(EMPTY_DIM) }
  function openCreate() { setMode({ kind: 'create' }); setForm(EMPTY_DIM) }
  function openEdit(d: TagDimension) {
    setMode({ kind: 'edit', id: d.id })
    setForm({ name: d.name, value_type: d.value_type, is_required: d.is_required, allow_multi: d.allow_multi })
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const name = form.name.trim()
    if (!name) { toast.error(tt.nameRequired); return }
    if (charLen(name) > 8) { toast.error(tt.nameTooLong); return }
    if (mode.kind === 'create') {
      createDimMut.mutate({ ...form, name })
    } else if (mode.kind === 'edit') {
      // value_type 不可改，仅提交可变字段
      updateDimMut.mutate({ id: mode.id, data: { name, is_required: form.is_required, allow_multi: form.allow_multi } })
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
        <button
          onClick={() => isOpen ? closeForm() : openCreate()}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent transition-all"
        >
          <Plus size={14} />
          {tt.add}
        </button>
      </PageHeader>

      {isOpen && (
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

      {isError ? (
        <QueryError compact onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="text-center text-muted text-sm py-12">{t.common.loading}</div>
      ) : dims.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.empty}</div>
      ) : (
        <div className="space-y-3">
          {dims.map(d => (
            <div key={d.id} className="bg-surface border border-default rounded-xl p-4 space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 flex-wrap min-w-0">
                  <span className="font-display font-bold text-primary truncate">{d.name}</span>
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
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => openEdit(d)} title={t.common.edit}
                    className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={14} /></button>
                  <button onClick={() => handleDeleteDim(d)} disabled={deleteDimMut.isPending} title={t.common.delete}
                    className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={14} /></button>
                </div>
              </div>

              {d.value_type === 'date' ? (
                <p className="text-xs text-muted border-t border-default pt-3">{tt.dateNoOptions}</p>
              ) : (
                <div className="border-t border-default pt-3 space-y-2">
                  <div className="text-[11px] text-secondary">{tt.optionsLabel}（{d.options.length}）</div>
                  <div className="flex flex-wrap items-center gap-2">
                    {d.options.map(o => (
                      <span key={o.id}
                        className="group inline-flex items-center gap-1.5 text-xs text-primary bg-elevated border border-default rounded-lg pl-2.5 pr-1.5 py-1">
                        <button onClick={() => handleRenameOpt(o)} title={t.common.edit}
                          className="hover:text-brand-400 transition-colors">{o.value}</button>
                        <button onClick={() => handleDeleteOpt(o)} title={t.common.delete}
                          className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                      </span>
                    ))}
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
          ))}
        </div>
      )}
    </div>
  )
}
