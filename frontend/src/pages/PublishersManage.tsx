import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Plus, Trash2, Pencil, X, Building2, Globe, Boxes, ChevronDown, ChevronRight, Link2, ShieldCheck } from 'lucide-react'
import { publishersApi } from '../lib/api'
import { useT } from '../i18n'
import { PageHeader } from '../components/PageHeader'
import { QueryError } from '../components/QueryError'
import type {
  PublisherEntity, PublisherEntityCreate, PublisherEntityUpdate,
  PublisherSourceCreate, PublisherSourceType,
} from '../lib/types'

type EntityForm = {
  name: string
  name_en: string
  hq_region: string
  is_slg: boolean
  brief: string
}
const EMPTY_FORM: EntityForm = { name: '', name_en: '', hq_region: '', is_slg: true, brief: '' }
type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; id: number }

const QK = ['publishers'] as const

// 一手在前、二手在后，select 里分组直观
const SOURCE_TYPE_ORDER: PublisherSourceType[] = [
  'registry', 'official_filing', 'official_platform', 'official_domain',
  'media', 'reference', 'analysis', 'self_report',
]
type SrcForm = { title: string; url: string; source_type: PublisherSourceType; confidence: string; as_of: string }
const BLANK_SRC: SrcForm = { title: '', url: '', source_type: 'registry', confidence: '', as_of: '' }

const fmtNum = (n: number) => n.toLocaleString('en-US')
const fmtMoney = (n: number) => `$${Math.round(n).toLocaleString('en-US')}`

export default function PublishersManage() {
  const t = useT()
  const tt = t.publishersManage
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [form, setForm] = useState<EntityForm>(EMPTY_FORM)
  // 每张卡片下「新增马甲 / app_id」输入框各自独立
  const [newAlias, setNewAlias] = useState<Record<number, string>>({})
  const [newAppId, setNewAppId] = useState<Record<number, string>>({})
  const [srcForm, setSrcForm] = useState<Record<number, SrcForm>>({})
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const isEditing = mode.kind === 'edit'
  const isOpen = mode.kind !== 'closed'

  const { data: entities = [], isLoading, isError, refetch } = useQuery({
    queryKey: QK,
    queryFn: () => publishersApi.list(),
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: QK })

  const createMut = useMutation({
    mutationFn: (data: PublisherEntityCreate) => publishersApi.create(data),
    onSuccess: () => { invalidate(); closeForm(); toast.success(tt.added) },
  })
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: PublisherEntityUpdate }) => publishersApi.update(id, data),
    onSuccess: () => { invalidate(); closeForm(); toast.success(tt.updated) },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => publishersApi.delete(id),
    onSuccess: () => { invalidate(); toast.success(tt.deleted) },
  })
  const addAliasMut = useMutation({
    mutationFn: ({ id, keyword }: { id: number; keyword: string }) => publishersApi.addAlias(id, { keyword }),
    onSuccess: (_o, { id }) => { invalidate(); setNewAlias(s => ({ ...s, [id]: '' })); toast.success(tt.aliasAdded) },
  })
  const delAliasMut = useMutation({
    mutationFn: ({ id, aliasId }: { id: number; aliasId: number }) => publishersApi.deleteAlias(id, aliasId),
    onSuccess: () => { invalidate(); toast.success(tt.aliasDeleted) },
  })
  const addAppIdMut = useMutation({
    mutationFn: ({ id, app_id }: { id: number; app_id: string }) => publishersApi.addAppId(id, { app_id }),
    onSuccess: (_o, { id }) => { invalidate(); setNewAppId(s => ({ ...s, [id]: '' })); toast.success(tt.appIdAdded) },
  })
  const delAppIdMut = useMutation({
    mutationFn: ({ id, rowId }: { id: number; rowId: number }) => publishersApi.deleteAppId(id, rowId),
    onSuccess: () => { invalidate(); toast.success(tt.appIdDeleted) },
  })
  const addSourceMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: PublisherSourceCreate }) => publishersApi.addSource(id, data),
    onSuccess: (_o, { id }) => { invalidate(); setSrcForm(s => ({ ...s, [id]: BLANK_SRC })); toast.success(tt.sourceAdded) },
  })
  const delSourceMut = useMutation({
    mutationFn: ({ id, sourceId }: { id: number; sourceId: number }) => publishersApi.deleteSource(id, sourceId),
    onSuccess: () => { invalidate(); toast.success(tt.sourceDeleted) },
  })

  function closeForm() { setMode({ kind: 'closed' }); setForm(EMPTY_FORM) }
  function openCreate() { setMode({ kind: 'create' }); setForm(EMPTY_FORM) }
  function openEdit(e: PublisherEntity) {
    setMode({ kind: 'edit', id: e.id })
    setForm({
      name: e.name, name_en: e.name_en || '', hq_region: e.hq_region || '',
      is_slg: e.is_slg, brief: e.brief || '',
    })
  }

  const handleSubmit = (ev: React.FormEvent) => {
    ev.preventDefault()
    const name = form.name.trim()
    if (!name) { toast.error(tt.nameRequired); return }
    const payload = {
      name,
      name_en: form.name_en.trim() || null,
      hq_region: form.hq_region || null,
      is_slg: form.is_slg,
      brief: form.brief.trim() || null,
    }
    if (mode.kind === 'create') createMut.mutate(payload)
    else if (mode.kind === 'edit') updateMut.mutate({ id: mode.id, data: payload })
  }

  const handleDelete = (e: PublisherEntity) => {
    if (!window.confirm(tt.confirmDelete(e.name))) return
    deleteMut.mutate(e.id)
  }
  const handleAddAlias = (id: number) => {
    const keyword = (newAlias[id] || '').trim()
    if (!keyword) return
    addAliasMut.mutate({ id, keyword })
  }
  const handleDelAlias = (id: number, aliasId: number, kw: string) => {
    if (!window.confirm(tt.confirmDeleteAlias(kw))) return
    delAliasMut.mutate({ id, aliasId })
  }
  const handleAddAppId = (id: number) => {
    const app_id = (newAppId[id] || '').trim()
    if (!app_id) return
    addAppIdMut.mutate({ id, app_id })
  }
  const handleDelAppId = (id: number, rowId: number, aid: string) => {
    if (!window.confirm(tt.confirmDeleteAppId(aid))) return
    delAppIdMut.mutate({ id, rowId })
  }
  const setSrc = (id: number, patch: Partial<SrcForm>) =>
    setSrcForm(s => ({ ...s, [id]: { ...(s[id] ?? BLANK_SRC), ...patch } }))
  const handleAddSource = (id: number) => {
    const f = srcForm[id] ?? BLANK_SRC
    const url = f.url.trim()
    if (!url) return
    addSourceMut.mutate({ id, data: {
      url, title: f.title.trim() || null, source_type: f.source_type,
      confidence: f.confidence || null, as_of: f.as_of || null,
    } })
  }
  const handleDelSource = (id: number, sourceId: number) => {
    if (!window.confirm(tt.confirmDeleteSource)) return
    delSourceMut.mutate({ id, sourceId })
  }

  const submitting = createMut.isPending || updateMut.isPending
  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"
  const chipInputClass = "bg-elevated border border-default rounded-lg px-2.5 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 w-36"

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="Publishers" title={tt.title} subtitle={tt.subtitle}>
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
            {isEditing ? tt.editFormTitle : tt.addFormTitle}
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
              <label className="block text-xs text-secondary mb-1">{tt.nameEnLabel}</label>
              <input
                value={form.name_en}
                onChange={e => setForm(f => ({ ...f, name_en: e.target.value }))}
                placeholder={tt.nameEnPlaceholder}
                className={`w-full ${inputClass}`}
              />
            </div>
            <div>
              <label className="block text-xs text-secondary mb-1">{tt.hqRegionLabel}</label>
              <select
                value={form.hq_region}
                onChange={e => setForm(f => ({ ...f, hq_region: e.target.value }))}
                className={`w-full ${inputClass}`}
              >
                <option value="">{tt.regionUnset}</option>
                <option value="国内">{tt.regionDomestic}</option>
                <option value="海外">{tt.regionOverseas}</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer select-none sm:mt-6">
              <input type="checkbox" checked={form.is_slg}
                onChange={e => setForm(f => ({ ...f, is_slg: e.target.checked }))}
                className="accent-brand-500" />
              {tt.isSlgLabel}
            </label>
          </div>
          <div>
            <label className="block text-xs text-secondary mb-1">{tt.briefLabel}</label>
            <textarea
              value={form.brief}
              onChange={e => setForm(f => ({ ...f, brief: e.target.value }))}
              placeholder={tt.briefPlaceholder}
              rows={2}
              className={`w-full ${inputClass} resize-y`}
            />
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
      ) : entities.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.empty}</div>
      ) : (
        <div className="space-y-3">
          {entities.map(e => (
            <div key={e.id} className="bg-surface border border-default rounded-xl p-4 space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 flex-wrap min-w-0">
                  <Building2 size={15} className="text-accent shrink-0" />
                  <span className="font-display font-bold text-primary truncate">{e.name}</span>
                  {e.name_en && <span className="text-xs text-muted truncate">{e.name_en}</span>}
                  {e.hq_region && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-secondary border border-default bg-elevated rounded px-1.5 py-0.5 shrink-0">
                      <Globe size={10} />{e.hq_region}
                    </span>
                  )}
                  {e.is_slg && (
                    <span className="text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5 shrink-0">
                      {tt.slgBadge}
                    </span>
                  )}
                  {e.provenance_tier === 'primary' ? (
                    <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 border border-emerald-500/40 bg-emerald-500/10 rounded px-1.5 py-0.5 shrink-0">
                      <ShieldCheck size={10} />{tt.provPrimary}
                    </span>
                  ) : e.provenance_tier === 'secondary' ? (
                    <span className="text-[10px] text-amber-500 border border-amber-500/40 bg-amber-500/10 rounded px-1.5 py-0.5 shrink-0">
                      {tt.provSecondary}
                    </span>
                  ) : (
                    <span className="text-[10px] text-muted border border-default rounded px-1.5 py-0.5 shrink-0">
                      {tt.provNone}
                    </span>
                  )}
                  {e.product_count != null && e.product_count > 0 && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted border border-default rounded px-1.5 py-0.5 shrink-0">
                      <Boxes size={10} />{tt.productCount(e.product_count)}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => openEdit(e)} title={t.common.edit}
                    className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={14} /></button>
                  <button onClick={() => handleDelete(e)} disabled={deleteMut.isPending} title={t.common.delete}
                    className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={14} /></button>
                </div>
              </div>

              {e.brief && <p className="text-xs text-muted">{e.brief}</p>}

              {/* 海外发行马甲 */}
              <div className="border-t border-default pt-3 space-y-2">
                <div className="text-[11px] text-secondary" title={tt.aliasHint}>
                  {tt.aliasesLabel}（{e.aliases.length}）
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {e.aliases.map(a => (
                    <span key={a.id}
                      className="inline-flex items-center gap-1.5 text-xs text-primary bg-elevated border border-default rounded-lg pl-2.5 pr-1.5 py-1">
                      <span className="font-data">{a.keyword}</span>
                      {a.label && <span className="text-muted">· {a.label}</span>}
                      <button onClick={() => handleDelAlias(e.id, a.id, a.keyword)} title={t.common.delete}
                        className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                    </span>
                  ))}
                  <span className="inline-flex items-center gap-1">
                    <input
                      value={newAlias[e.id] || ''}
                      onChange={ev => setNewAlias(s => ({ ...s, [e.id]: ev.target.value }))}
                      onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddAlias(e.id) } }}
                      placeholder={tt.aliasKeywordPlaceholder}
                      className={chipInputClass}
                    />
                    <button onClick={() => handleAddAlias(e.id)} disabled={addAliasMut.isPending}
                      className="p-1 text-muted hover:text-accent transition-colors" title={tt.addAlias}>
                      <Plus size={14} />
                    </button>
                  </span>
                </div>
              </div>

              {/* 关注 app_id */}
              <div className="border-t border-default pt-3 space-y-2">
                <div className="text-[11px] text-secondary" title={tt.appIdHint}>
                  {tt.appIdsLabel}（{e.app_ids.length}）
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {e.app_ids.map(a => (
                    <span key={a.id}
                      className="inline-flex items-center gap-1.5 text-xs text-primary bg-elevated border border-default rounded-lg pl-2.5 pr-1.5 py-1">
                      <span className="font-data">{a.app_id}</span>
                      {a.note && <span className="text-muted">· {a.note}</span>}
                      <button onClick={() => handleDelAppId(e.id, a.id, a.app_id)} title={t.common.delete}
                        className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                    </span>
                  ))}
                  <span className="inline-flex items-center gap-1">
                    <input
                      value={newAppId[e.id] || ''}
                      onChange={ev => setNewAppId(s => ({ ...s, [e.id]: ev.target.value }))}
                      onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddAppId(e.id) } }}
                      placeholder={tt.appIdPlaceholder}
                      className={chipInputClass}
                    />
                    <button onClick={() => handleAddAppId(e.id)} disabled={addAppIdMut.isPending}
                      className="p-1 text-muted hover:text-accent transition-colors" title={tt.addAppId}>
                      <Plus size={14} />
                    </button>
                  </span>
                </div>
              </div>

              {/* 调研溯源（一手源沉淀） */}
              <div className="border-t border-default pt-3 space-y-2">
                <div className="text-[11px] text-secondary" title={tt.sourcesHint}>
                  {tt.sourcesLabel}（{e.sources.length}）
                </div>
                <div className="space-y-1.5">
                  {e.sources.map(s => (
                    <div key={s.id} className="flex items-center gap-2 text-xs bg-elevated border border-default rounded-lg px-2.5 py-1.5">
                      <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${s.is_primary ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-amber-500 bg-amber-500/10 border-amber-500/30'}`}>
                        {s.is_primary ? tt.primaryTag : tt.secondaryTag}
                      </span>
                      <span className="shrink-0 text-[10px] text-secondary">{tt.sourceTypes[s.source_type]}</span>
                      <a href={s.url} target="_blank" rel="noreferrer"
                        className="min-w-0 truncate text-brand-400 hover:underline inline-flex items-center gap-1">
                        <Link2 size={11} className="shrink-0" />{s.title || s.url}
                      </a>
                      {s.confidence && (
                        <span className="shrink-0 text-[10px] text-muted">
                          {tt.confidenceOptions[s.confidence as keyof typeof tt.confidenceOptions] ?? s.confidence}
                        </span>
                      )}
                      {s.as_of && <span className="shrink-0 text-[10px] text-muted font-data">{s.as_of}</span>}
                      <button onClick={() => handleDelSource(e.id, s.id)} title={t.common.delete}
                        className="ml-auto shrink-0 text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                    </div>
                  ))}
                  {e.sources.length === 0 && <div className="text-[11px] text-muted">{tt.noSources}</div>}
                </div>
                <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                  <input
                    value={(srcForm[e.id] ?? BLANK_SRC).url}
                    onChange={ev => setSrc(e.id, { url: ev.target.value })}
                    onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddSource(e.id) } }}
                    placeholder={tt.sourceUrlPlaceholder}
                    className="bg-elevated border border-default rounded-lg px-2.5 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 flex-1 min-w-[160px]"
                  />
                  <input
                    value={(srcForm[e.id] ?? BLANK_SRC).title}
                    onChange={ev => setSrc(e.id, { title: ev.target.value })}
                    placeholder={tt.sourceTitlePlaceholder}
                    className={chipInputClass}
                  />
                  <select
                    value={(srcForm[e.id] ?? BLANK_SRC).source_type}
                    onChange={ev => setSrc(e.id, { source_type: ev.target.value as PublisherSourceType })}
                    className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
                  >
                    {SOURCE_TYPE_ORDER.map(st => <option key={st} value={st}>{tt.sourceTypes[st]}</option>)}
                  </select>
                  <select
                    value={(srcForm[e.id] ?? BLANK_SRC).confidence}
                    onChange={ev => setSrc(e.id, { confidence: ev.target.value })}
                    className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
                  >
                    <option value="">{tt.confidenceOptions.unset}</option>
                    <option value="high">{tt.confidenceOptions.high}</option>
                    <option value="medium">{tt.confidenceOptions.medium}</option>
                    <option value="low">{tt.confidenceOptions.low}</option>
                    <option value="unverified">{tt.confidenceOptions.unverified}</option>
                  </select>
                  <input
                    type="date"
                    value={(srcForm[e.id] ?? BLANK_SRC).as_of}
                    onChange={ev => setSrc(e.id, { as_of: ev.target.value })}
                    className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
                  />
                  <button onClick={() => handleAddSource(e.id)} disabled={addSourceMut.isPending}
                    className="p-1 text-muted hover:text-accent transition-colors" title={tt.addSource}>
                    <Plus size={14} />
                  </button>
                </div>
              </div>

              {/* 旗下产品（按需展开聚合） */}
              <div className="border-t border-default pt-3">
                <button
                  onClick={() => setExpanded(s => ({ ...s, [e.id]: !s[e.id] }))}
                  className="flex items-center gap-1.5 text-[11px] text-secondary hover:text-primary transition-colors"
                >
                  {expanded[e.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  {expanded[e.id] ? tt.hideProducts : tt.viewProducts}
                  {e.product_count != null && <span className="text-muted">（{e.product_count}）</span>}
                </button>
                {expanded[e.id] && <PublisherProducts entityId={e.id} />}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PublisherProducts({ entityId }: { entityId: number }) {
  const t = useT()
  const tt = t.publishersManage
  const navigate = useNavigate()
  const { data: products = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['publisherProducts', entityId],
    queryFn: () => publishersApi.products(entityId, 30),
  })

  if (isError) return <div className="pt-2"><QueryError compact onRetry={() => refetch()} /></div>
  if (isLoading) return <div className="text-center text-muted text-xs py-3">{t.common.loading}</div>
  if (products.length === 0) return <div className="text-muted text-xs py-3">{tt.productsEmpty}</div>

  return (
    <div className="pt-2 space-y-1.5">
      <div className="text-[10px] text-muted">{tt.productsDaysHint}</div>
      {products.map(p => (
        <button
          key={p.app_id}
          onClick={() => navigate(`/game/${p.app_id}`)}
          className="w-full flex items-center gap-3 px-2.5 py-2 rounded-lg bg-elevated hover:bg-elevated/70 border border-default transition-colors text-left"
        >
          {p.icon_url
            ? <img src={p.icon_url} alt="" className="w-8 h-8 rounded-lg shrink-0 object-cover" />
            : <div className="w-8 h-8 rounded-lg shrink-0 bg-surface" />}
          <div className="min-w-0 flex-1">
            <div className="text-xs text-primary truncate">{p.name || p.app_id}</div>
            <div className="text-[10px] text-muted truncate">{p.publisher || '—'}</div>
          </div>
          <span className="text-[10px] text-secondary border border-default rounded px-1.5 py-0.5 shrink-0">
            {p.matched_by === 'app_id' ? tt.productMatchedAppId : tt.productMatchedAlias}
          </span>
          <div className="text-right shrink-0 w-24">
            <div className="text-xs text-primary font-data">{fmtMoney(p.revenue)}</div>
            <div className="text-[10px] text-muted font-data">{fmtNum(p.downloads)} ↓</div>
          </div>
        </button>
      ))}
    </div>
  )
}
