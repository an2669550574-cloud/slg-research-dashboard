import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Plus, Trash2, Pencil, Star } from 'lucide-react'
import { productsApi } from '../lib/api'
import { useT } from '../i18n'
import { PageHeader } from '../components/PageHeader'
import { QueryError } from '../components/QueryError'
import { ProductMaterials } from '../components/ProductMaterials'
import type { OwnProduct } from '../lib/types'

type FormState = { name: string; brief: string; match_keywords: string; is_default: boolean }
const EMPTY_FORM: FormState = { name: '', brief: '', match_keywords: '', is_default: false }
type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; id: number }

export default function ProductsManage() {
  const t = useT()
  const tp = t.productsManage
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  const isEditing = mode.kind === 'edit'
  const isOpen = mode.kind !== 'closed'

  const { data: products = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['ownProducts'],
    queryFn: productsApi.list,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['ownProducts'] })

  const createMut = useMutation({
    mutationFn: (data: FormState) => productsApi.create(data),
    onSuccess: (created) => {
      invalidate()
      toast.success(tp.added)
      // 保存后留在该产品的编辑态，直接露出素材上传/AI 解析区，无需再回列表点编辑
      setMode({ kind: 'edit', id: created.id })
      setForm({ name: created.name, brief: created.brief, match_keywords: created.match_keywords ?? '', is_default: created.is_default })
    },
  })
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<FormState> }) => productsApi.update(id, data),
    onSuccess: () => { invalidate(); closeForm(); toast.success(tp.updated) },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => productsApi.delete(id),
    onSuccess: () => { invalidate(); toast.success(tp.deleted) },
  })

  function closeForm() { setMode({ kind: 'closed' }); setForm(EMPTY_FORM) }
  function openCreate() { setMode({ kind: 'create' }); setForm(EMPTY_FORM) }
  function openEdit(p: OwnProduct) {
    setMode({ kind: 'edit', id: p.id })
    setForm({ name: p.name, brief: p.brief, match_keywords: p.match_keywords ?? '', is_default: p.is_default })
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) { toast.error(tp.nameRequired); return }
    if (!form.brief.trim()) { toast.error(tp.briefRequired); return }
    if (mode.kind === 'create') createMut.mutate(form)
    else if (mode.kind === 'edit') updateMut.mutate({ id: mode.id, data: form })
  }

  const handleDelete = (p: OwnProduct) => {
    if (!confirm(tp.confirmDelete(p.name))) return
    deleteMut.mutate(p.id)
  }

  const submitting = createMut.isPending || updateMut.isPending
  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="Products" title={tp.title} subtitle={tp.subtitle}>
        <button
          onClick={() => isOpen ? closeForm() : openCreate()}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent transition-all"
        >
          <Plus size={14} />
          {tp.add}
        </button>
      </PageHeader>

      {isOpen && (
        <form onSubmit={handleSubmit} className="bg-surface border border-default rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-primary">
            {isEditing ? tp.editFormTitle : tp.addFormTitle}
          </h3>
          <div>
            <label className="block text-xs text-secondary mb-1">{tp.nameLabel}</label>
            <input
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder={tp.namePlaceholder}
              className={`w-full ${inputClass}`}
            />
          </div>
          <div>
            <label className="block text-xs text-secondary mb-1">{tp.briefLabel}</label>
            <textarea
              rows={5}
              value={form.brief}
              onChange={e => setForm(f => ({ ...f, brief: e.target.value }))}
              placeholder={tp.briefPlaceholder}
              className={`w-full resize-y ${inputClass}`}
            />
          </div>
          <div>
            <label className="block text-xs text-secondary mb-1">{tp.matchKeywordsLabel}</label>
            <input
              value={form.match_keywords}
              onChange={e => setForm(f => ({ ...f, match_keywords: e.target.value }))}
              placeholder={tp.matchKeywordsPlaceholder}
              className={`w-full ${inputClass}`}
            />
            <p className="mt-1 text-[11px] text-muted leading-relaxed">{tp.matchKeywordsHint}</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer select-none">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={e => setForm(f => ({ ...f, is_default: e.target.checked }))}
              className="accent-brand-500"
            />
            {tp.setDefault}
          </label>

          {mode.kind === 'edit' ? (
            <ProductMaterials
              productId={mode.id}
              onBriefDraft={brief => setForm(f => ({ ...f, brief }))}
            />
          ) : (
            <p className="border-t border-default pt-4 text-xs text-muted">{tp.materialsSaveFirst}</p>
          )}

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
      ) : products.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tp.empty}</div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {products.map(p => (
            <div key={p.id} className="bg-surface border border-default rounded-xl p-4 flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-display font-bold text-primary truncate">{p.name}</span>
                  {p.is_default && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0">
                      <Star size={10} /> {tp.defaultBadge}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => openEdit(p)} title={t.common.edit}
                    className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={14} /></button>
                  <button onClick={() => handleDelete(p)} disabled={deleteMut.isPending} title={t.common.delete}
                    className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={14} /></button>
                </div>
              </div>
              <p className="text-xs text-secondary whitespace-pre-wrap leading-relaxed line-clamp-4">{p.brief}</p>
              {p.match_keywords && (
                <p className="text-[11px] text-muted truncate" title={p.match_keywords}>
                  <span className="text-secondary">⚔️ {tp.matchKeywordsBadge}</span> {p.match_keywords}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
