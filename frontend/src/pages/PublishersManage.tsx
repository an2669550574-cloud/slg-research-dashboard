import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Plus, Trash2, Pencil, X, Building2, Globe, ChevronRight, Link2, ShieldCheck, Network, Search, List, Landmark, CornerDownRight, LayoutGrid, ListTree } from 'lucide-react'
import { publishersApi } from '../lib/api'
import { useT } from '../i18n'
import { PageHeader } from '../components/PageHeader'
import { QueryError } from '../components/QueryError'
import { PublisherGraph } from '../components/PublisherGraph'
import { PublisherCapitalTree } from '../components/PublisherCapitalTree'
import { GameIcon } from '../components/GameIcon'
import { useLocalStorageState } from '../lib/hooks'
import type {
  PublisherEntity, PublisherEntityCreate, PublisherEntityUpdate,
  PublisherSourceCreate, PublisherSourceType,
  PublisherRelationCreate, PublisherRelationType, RelationCounterpartRole,
} from '../lib/types'

type Segment = 'all' | 'operator' | 'capital'
type SortKey = 'default' | 'products' | 'provenance'
const PROV_RANK: Record<string, number> = { primary: 0, secondary: 1, none: 2 }

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
type SrcForm = { title: string; url: string; source_type: PublisherSourceType; confidence: string; as_of: string; note: string }
const BLANK_SRC: SrcForm = { title: '', url: '', source_type: 'registry', confidence: '', as_of: '', note: '' }

const RELATION_TYPE_ORDER: PublisherRelationType[] = ['wholly_owned', 'controlling', 'minority', 'affiliate']
type RelForm = { counterpart_id: string; counterpart_role: RelationCounterpartRole; relation_type: PublisherRelationType; stake_pct: string; note: string }
const BLANK_REL: RelForm = { counterpart_id: '', counterpart_role: 'parent', relation_type: 'controlling', stake_pct: '', note: '' }

const fmtNum = (n: number) => n.toLocaleString('en-US')
const fmtMoney = (n: number) => `$${Math.round(n).toLocaleString('en-US')}`

const chipInputClass = "bg-elevated border border-default rounded-lg px-2.5 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 w-36"

export default function PublishersManage() {
  const t = useT()
  const tt = t.publishersManage
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [form, setForm] = useState<EntityForm>(EMPTY_FORM)
  // 网格卡片点开 → 右侧详情抽屉（一次只开一个主体，管理区全在抽屉里）
  const [detailId, setDetailId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [onlyResearched, setOnlyResearched] = useState(false)
  // 分段（全部/运营体/资本方）、排序、按股权分组——持久化用户偏好
  const [segment, setSegment] = useLocalStorageState<Segment>('pub.segment', 'all')
  const [sortKey, setSortKey] = useLocalStorageState<SortKey>('pub.sort', 'default')
  const [grouped, setGrouped] = useLocalStorageState<boolean>('pub.grouped', false)
  // 网格 / 股权图谱视图切换；图谱画全量（不受搜索/筛选影响）
  const [view, setView] = useState<'grid' | 'graph' | 'tree'>('grid')

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
    onSuccess: (_o, id) => {
      invalidate()
      setDetailId(d => (d === id ? null : d))
      toast.success(tt.deleted)
    },
  })

  function closeForm() { setMode({ kind: 'closed' }); setForm(EMPTY_FORM) }
  function openCreate() { setMode({ kind: 'create' }); setForm(EMPTY_FORM) }
  function openEdit(e: PublisherEntity) {
    setMode({ kind: 'edit', id: e.id })
    setForm({
      name: e.name, name_en: e.name_en || '', hq_region: e.hq_region || '',
      is_slg: e.is_slg, brief: e.brief || '',
    })
    // 编辑表单在页面顶部，从抽屉里点编辑时收起抽屉避免遮挡
    setDetailId(null)
    window.scrollTo({ top: 0 })
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

  const submitting = createMut.isPending || updateMut.isPending
  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

  // 「有调研数据」= 有溯源源或股权关系（区别于种子里就有的马甲/app_id）
  const isResearched = (e: PublisherEntity) =>
    e.sources.length > 0 || e.parents.length > 0 || e.children.length > 0
  // 资本方 = 非 SLG 运营体（is_slg=0 的纯控股/投资节点，如世纪华通/腾讯）
  const isCapital = (e: PublisherEntity) => !e.is_slg
  const q = search.trim().toLowerCase()
  const filtered = entities.filter(e => {
    if (segment === 'operator' && isCapital(e)) return false
    if (segment === 'capital' && !isCapital(e)) return false
    if (onlyResearched && !isResearched(e)) return false
    if (!q) return true
    return e.name.toLowerCase().includes(q)
      || (e.name_en || '').toLowerCase().includes(q)
      || e.aliases.some(a => a.keyword.toLowerCase().includes(q) || (a.label || '').toLowerCase().includes(q))
  })

  // 展示序列：分组（按股权嵌套 DFS，母公司后紧跟子公司）或扁平排序。
  // 网格里不做缩进，层级靠卡片上的「↳ 母公司」行表达，分组只决定顺序。
  const displayList: PublisherEntity[] = (() => {
    if (grouped) {
      const visibleIds = new Set(filtered.map(e => e.id))
      const childrenOf = new Map<number, PublisherEntity[]>()
      filtered.forEach(e => e.parents.forEach(p => {
        if (visibleIds.has(p.entity_id)) {
          if (!childrenOf.has(p.entity_id)) childrenOf.set(p.entity_id, [])
          childrenOf.get(p.entity_id)!.push(e)
        }
      }))
      const hasVisibleParent = (e: PublisherEntity) => e.parents.some(p => visibleIds.has(p.entity_id))
      const out: PublisherEntity[] = []
      const seen = new Set<number>()
      const visit = (e: PublisherEntity) => {
        if (seen.has(e.id)) return  // 防环 / 防多母公司重复
        seen.add(e.id)
        out.push(e)
        ;(childrenOf.get(e.id) || []).forEach(visit)
      }
      filtered.filter(e => !hasVisibleParent(e)).forEach(visit)
      filtered.forEach(e => { if (!seen.has(e.id)) out.push(e) })  // 环残留兜底
      return out
    }
    const sorted = [...filtered]
    if (sortKey === 'products') sorted.sort((a, b) => (b.product_count ?? 0) - (a.product_count ?? 0))
    else if (sortKey === 'provenance') sorted.sort((a, b) =>
      (PROV_RANK[a.provenance_tier] ?? 9) - (PROV_RANK[b.provenance_tier] ?? 9) || a.name.localeCompare(b.name))
    return sorted
  })()

  const detail = detailId != null ? entities.find(e => e.id === detailId) ?? null : null

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

      {/* 筛选栏：视图切换 + 搜索 + 只看有调研数据 + 计数（搜索/筛选只作用于网格） */}
      {!isLoading && !isError && entities.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 bg-elevated rounded-lg p-1">
            {(['grid', 'graph', 'tree'] as const).map(v => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
              >
                {v === 'grid' ? <LayoutGrid size={12} /> : v === 'graph' ? <Network size={12} /> : <ListTree size={12} />}
                {v === 'grid' ? tt.viewList : v === 'graph' ? tt.viewGraph : tt.viewTree}
              </button>
            ))}
          </div>
          {view === 'grid' && (
            <>
              <div className="relative flex-1 min-w-[180px] max-w-xs">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  type="text"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder={tt.search}
                  className="w-full bg-elevated border border-default rounded-lg pl-9 pr-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"
                />
              </div>
              {/* 分段：全部 / 运营体 / 资本方 */}
              <div className="flex gap-1 bg-elevated rounded-lg p-1">
                {(['all', 'operator', 'capital'] as const).map(v => (
                  <button
                    key={v}
                    onClick={() => setSegment(v)}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${segment === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                  >
                    {tt.segments[v]}
                  </button>
                ))}
              </div>
              <div className="flex gap-1 bg-elevated rounded-lg p-1">
                {([false, true] as const).map(v => (
                  <button
                    key={String(v)}
                    onClick={() => setOnlyResearched(v)}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${onlyResearched === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                  >
                    {v ? tt.onlyResearched : tt.showAll}
                  </button>
                ))}
              </div>
              {/* 按股权分组（开则忽略排序、母公司后紧跟子公司）*/}
              <button
                onClick={() => setGrouped(!grouped)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${grouped ? 'bg-brand-600 text-white border-transparent' : 'bg-elevated text-secondary hover:text-primary border-default'}`}
              >
                <Network size={12} />{tt.groupByEquity}
              </button>
              {/* 排序（分组态禁用，因层级即序）*/}
              <select
                value={sortKey}
                onChange={e => setSortKey(e.target.value as SortKey)}
                disabled={grouped}
                className="bg-elevated border border-default rounded-lg px-2.5 py-1.5 text-xs text-primary focus:outline-none focus:border-brand-500 disabled:opacity-40"
              >
                <option value="default">{tt.sortDefault}</option>
                <option value="products">{tt.sortProducts}</option>
                <option value="provenance">{tt.sortProvenance}</option>
              </select>
              <span className="font-data text-[11px] text-muted">{tt.countShown(filtered.length, entities.length)}</span>
            </>
          )}
        </div>
      )}

      {isError ? (
        <QueryError compact onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="text-center text-muted text-sm py-12">{t.common.loading}</div>
      ) : entities.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.empty}</div>
      ) : view === 'graph' ? (
        <PublisherGraph entities={entities} onSelectEntity={setDetailId} />
      ) : view === 'tree' ? (
        <PublisherCapitalTree entities={entities} onSelectEntity={setDetailId} />
      ) : filtered.length === 0 ? (
        <div className="text-center text-muted text-sm py-12 bg-surface border border-default rounded-xl">{tt.emptyFiltered}</div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {displayList.map(e => {
            const cap = isCapital(e)
            const parent = e.parents[0]
            return (
              <div
                key={e.id}
                id={`publisher-card-${e.id}`}
                onClick={() => setDetailId(e.id)}
                className={`group flex flex-col border rounded-xl p-4 cursor-pointer transition-colors ${cap
                  ? 'bg-elevated/60 border-default hover:border-amber-500/40'
                  : 'bg-surface border-default hover:border-brand-500/50'}`}
              >
                {/* 头：类型图标 + 名字（英文名次行）+ 操作 */}
                <div className="flex items-start gap-2.5">
                  <span className={`mt-0.5 shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${cap ? 'bg-amber-500/10' : 'bg-accent/10'}`}>
                    {cap ? <Landmark size={15} className="text-amber-500" /> : <Building2 size={15} className="text-accent" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`font-display font-bold truncate ${cap ? 'text-secondary' : 'text-primary'}`}>{e.name}</span>
                      {e.hq_region && e.hq_region !== '国内' && (
                        <span className="inline-flex items-center gap-0.5 text-[10px] text-secondary shrink-0"><Globe size={10} />{e.hq_region}</span>
                      )}
                    </div>
                    <div className="text-[11px] text-muted truncate">
                      {e.name_en || (cap ? tt.capitalBadge : tt.slgBadge)}
                    </div>
                  </div>
                  <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" onClick={ev => ev.stopPropagation()}>
                    <button onClick={() => openEdit(e)} title={t.common.edit}
                      className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={13} /></button>
                    <button onClick={() => handleDelete(e)} disabled={deleteMut.isPending} title={t.common.delete}
                      className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={13} /></button>
                  </div>
                </div>

                {/* 身：产品图标条（运营体）/ 控股说明（资本方）+ 母公司行 */}
                <div className="mt-3 space-y-1.5 min-h-[34px]">
                  {e.top_products.length > 0 ? (
                    <div className="flex items-center gap-1.5">
                      {e.top_products.map(p => (
                        <GameIcon key={p.app_id} src={p.icon_url} name={p.name ?? p.app_id} className="w-8 h-8 rounded-lg" />
                      ))}
                      {!!e.product_count && e.product_count > e.top_products.length && (
                        <span className="text-[11px] text-muted font-data">+{e.product_count - e.top_products.length}</span>
                      )}
                    </div>
                  ) : (
                    <div className="text-[11px] text-muted leading-8">
                      {cap
                        ? tt.capitalNoProducts
                        : (e.sources.length > 0 || e.parents.length > 0 || e.children.length > 0)
                          ? tt.sumNoProducts
                          : tt.sumEmpty}
                    </div>
                  )}
                  {parent && (
                    <div className="flex items-center gap-1 text-[11px] text-muted min-w-0">
                      <CornerDownRight size={11} className="shrink-0" />
                      <span className="truncate">
                        {tt.sumParent} {parent.name}（{tt.relationTypes[parent.relation_type]}{parent.stake_pct != null ? ' ' + tt.stakeSuffix(parent.stake_pct) : ''}）{e.parents.length > 1 ? ' 等' : ''}
                      </span>
                    </div>
                  )}
                </div>

                {/* 脚：统计 + 溯源状态（盾标）+ 展开箭头 */}
                <div className="mt-auto pt-3 flex items-center gap-2 text-[11px] text-muted font-data border-t border-default/60">
                  {!!e.product_count && <span>{tt.statProducts} <b className="text-secondary font-medium">{e.product_count}</b></span>}
                  {e.aliases.length > 0 && <span>{tt.statAliases} <b className="text-secondary font-medium">{e.aliases.length}</b></span>}
                  {e.children.length > 0 && <span>{tt.statChildren} <b className="text-secondary font-medium">{e.children.length}</b></span>}
                  {e.sources.length > 0 && <span>{tt.statSources} <b className="text-secondary font-medium">{e.sources.length}</b></span>}
                  <span className="ml-auto inline-flex items-center gap-1.5">
                    <ShieldCheck
                      size={13}
                      className={e.provenance_tier === 'primary' ? 'text-emerald-400' : e.provenance_tier === 'secondary' ? 'text-amber-500' : 'text-muted/40'}
                    >
                      <title>{e.provenance_tier === 'primary' ? tt.provPrimary : e.provenance_tier === 'secondary' ? tt.provSecondary : tt.provNone}</title>
                    </ShieldCheck>
                    <ChevronRight size={13} className="text-muted/50 group-hover:text-secondary group-hover:translate-x-0.5 transition-all" />
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {detail && (
        <PublisherDetailDrawer
          key={detail.id}
          entity={detail}
          entities={entities}
          onClose={() => setDetailId(null)}
          onEdit={() => openEdit(detail)}
          onDelete={() => handleDelete(detail)}
        />
      )}
    </div>
  )
}

/** 右侧详情抽屉：brief / 旗下产品 / 马甲 / app_id / 开发者账号 / 溯源 / 股权关系
 *  的查看与维护全部收在这里——网格卡片只读摘要，编辑动作一律进抽屉。
 *  ⚠️ 所有 hooks 在任何条件分支之前（hooks 顺序纪律）；父组件按 key=entity.id
 *  挂载，切换主体时整组件重建，表单状态自然归零。 */
function PublisherDetailDrawer({ entity: e, entities, onClose, onEdit, onDelete }: {
  entity: PublisherEntity
  entities: PublisherEntity[]
  onClose: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const t = useT()
  const tt = t.publishersManage
  const qc = useQueryClient()
  const [newAlias, setNewAlias] = useState('')
  const [newAppId, setNewAppId] = useState('')
  const [newArtist, setNewArtist] = useState({ artist_id: '', label: '', platform: 'ios' as 'ios' | 'gp' })
  const [srcForm, setSrcForm] = useState<SrcForm>(BLANK_SRC)
  const [relForm, setRelForm] = useState<RelForm>(BLANK_REL)

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const invalidate = () => qc.invalidateQueries({ queryKey: QK })

  const addAliasMut = useMutation({
    mutationFn: (keyword: string) => publishersApi.addAlias(e.id, { keyword }),
    onSuccess: () => { invalidate(); setNewAlias(''); toast.success(tt.aliasAdded) },
  })
  const delAliasMut = useMutation({
    mutationFn: (aliasId: number) => publishersApi.deleteAlias(e.id, aliasId),
    onSuccess: () => { invalidate(); toast.success(tt.aliasDeleted) },
  })
  const addAppIdMut = useMutation({
    mutationFn: (app_id: string) => publishersApi.addAppId(e.id, { app_id }),
    onSuccess: () => { invalidate(); setNewAppId(''); toast.success(tt.appIdAdded) },
  })
  const delAppIdMut = useMutation({
    mutationFn: (rowId: number) => publishersApi.deleteAppId(e.id, rowId),
    onSuccess: () => { invalidate(); toast.success(tt.appIdDeleted) },
  })
  const addArtistMut = useMutation({
    mutationFn: ({ artist_id, label, platform }: { artist_id: string; label: string; platform: 'ios' | 'gp' }) =>
      publishersApi.addItunesArtist(e.id, { artist_id, platform, label: label.trim() || null }),
    onSuccess: () => { invalidate(); setNewArtist({ artist_id: '', label: '', platform: 'ios' }); toast.success(tt.artistAdded) },
  })
  const delArtistMut = useMutation({
    mutationFn: (rowId: number) => publishersApi.deleteItunesArtist(e.id, rowId),
    onSuccess: () => { invalidate(); toast.success(tt.artistDeleted) },
  })
  const addSourceMut = useMutation({
    mutationFn: (data: PublisherSourceCreate) => publishersApi.addSource(e.id, data),
    onSuccess: () => { invalidate(); setSrcForm(BLANK_SRC); toast.success(tt.sourceAdded) },
  })
  const delSourceMut = useMutation({
    mutationFn: (sourceId: number) => publishersApi.deleteSource(e.id, sourceId),
    onSuccess: () => { invalidate(); toast.success(tt.sourceDeleted) },
  })
  const addRelationMut = useMutation({
    mutationFn: (data: PublisherRelationCreate) => publishersApi.addRelation(e.id, data),
    onSuccess: () => { invalidate(); setRelForm(BLANK_REL); toast.success(tt.relationAdded) },
  })
  const delRelationMut = useMutation({
    mutationFn: (relationId: number) => publishersApi.deleteRelation(e.id, relationId),
    onSuccess: () => { invalidate(); toast.success(tt.relationDeleted) },
  })

  const handleAddAlias = () => { const kw = newAlias.trim(); if (kw) addAliasMut.mutate(kw) }
  const handleDelAlias = (aliasId: number, kw: string) => {
    if (window.confirm(tt.confirmDeleteAlias(kw))) delAliasMut.mutate(aliasId)
  }
  const handleAddAppId = () => { const v = newAppId.trim(); if (v) addAppIdMut.mutate(v) }
  const handleDelAppId = (rowId: number, aid: string) => {
    if (window.confirm(tt.confirmDeleteAppId(aid))) delAppIdMut.mutate(rowId)
  }
  const handleAddArtist = () => { if (newArtist.artist_id.trim()) addArtistMut.mutate({ artist_id: newArtist.artist_id.trim(), label: newArtist.label, platform: newArtist.platform }) }
  const handleDelArtist = (rowId: number, aid: string) => {
    if (window.confirm(tt.confirmDeleteArtist(aid))) delArtistMut.mutate(rowId)
  }
  const handleAddSource = () => {
    const url = srcForm.url.trim()
    if (!url) return
    addSourceMut.mutate({
      url, title: srcForm.title.trim() || null, source_type: srcForm.source_type,
      confidence: srcForm.confidence || null, as_of: srcForm.as_of || null,
      note: srcForm.note.trim() || null,
    })
  }
  const handleDelSource = (sourceId: number) => {
    if (window.confirm(tt.confirmDeleteSource)) delSourceMut.mutate(sourceId)
  }
  const handleAddRelation = () => {
    if (!relForm.counterpart_id) { toast.error(tt.relationNeedCounterpart); return }
    const stake = relForm.stake_pct.trim()
    addRelationMut.mutate({
      counterpart_id: Number(relForm.counterpart_id), counterpart_role: relForm.counterpart_role,
      relation_type: relForm.relation_type, stake_pct: stake === '' ? null : Number(stake),
      note: relForm.note.trim() || null,
    })
  }
  const handleDelRelation = (relationId: number) => {
    if (window.confirm(tt.confirmDeleteRelation)) delRelationMut.mutate(relationId)
  }

  const cap = !e.is_slg

  return (
    <>
      {/* 遮罩：点击关闭 */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <aside className="fixed top-0 right-0 bottom-0 z-50 w-full sm:w-[600px] bg-surface border-l border-default shadow-2xl flex flex-col">
        {/* 抽屉头 */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-default shrink-0">
          <span className={`mt-0.5 shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${cap ? 'bg-amber-500/10' : 'bg-accent/10'}`}>
            {cap ? <Landmark size={16} className="text-amber-500" /> : <Building2 size={16} className="text-accent" />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-display font-bold text-primary">{e.name}</span>
              {e.name_en && <span className="text-xs text-muted">{e.name_en}</span>}
            </div>
            <div className="flex items-center gap-1.5 mt-1 flex-wrap">
              {e.hq_region && (
                <span className="inline-flex items-center gap-1 text-[10px] text-secondary border border-default bg-elevated rounded px-1.5 py-0.5"><Globe size={10} />{e.hq_region}</span>
              )}
              {e.is_slg ? (
                <span className="text-[10px] text-accent border border-accent/40 bg-accent/10 rounded px-1.5 py-0.5">{tt.slgBadge}</span>
              ) : (
                <span className="text-[10px] text-amber-500 border border-amber-500/40 bg-amber-500/10 rounded px-1.5 py-0.5">{tt.capitalBadge}</span>
              )}
              {e.provenance_tier === 'primary' ? (
                <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 border border-emerald-500/40 bg-emerald-500/10 rounded px-1.5 py-0.5"><ShieldCheck size={10} />{tt.provPrimary}</span>
              ) : e.provenance_tier === 'secondary' ? (
                <span className="text-[10px] text-amber-500 border border-amber-500/40 bg-amber-500/10 rounded px-1.5 py-0.5">{tt.provSecondary}</span>
              ) : (
                <span className="text-[10px] text-muted border border-default rounded px-1.5 py-0.5">{tt.provNone}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            <button onClick={onEdit} title={t.common.edit}
              className="p-1.5 text-muted hover:text-brand-400 transition-colors"><Pencil size={14} /></button>
            <button onClick={onDelete} title={t.common.delete}
              className="p-1.5 text-muted hover:text-red-400 transition-colors"><Trash2 size={14} /></button>
            <button onClick={onClose} title={t.common.cancel}
              className="p-1.5 text-muted hover:text-primary transition-colors"><X size={16} /></button>
          </div>
        </div>

        {/* 抽屉体（滚动区） */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {/* 公司介绍（业务最先看）*/}
          <div>
            <div className="text-[11px] text-secondary mb-1">{tt.briefSectionLabel}</div>
            {e.brief
              ? <p className="text-sm text-primary/90 leading-relaxed whitespace-pre-wrap">{e.brief}</p>
              : <p className="text-xs text-muted">{tt.briefEmpty}</p>}
          </div>

          {/* 旗下 SLG 产品（打开即自动加载·零 ST 配额）*/}
          <div className="border-t border-default pt-3 space-y-1.5">
            <div className="text-[11px] text-secondary">
              {tt.productsSectionLabel}{e.product_count != null ? `（${e.product_count}）` : ''}
            </div>
            <PublisherProducts entityId={e.id} />
          </div>

          {/* ↓ 以下为调研维护字段（业务可略）*/}
          <div className="pt-1 text-[10px] uppercase tracking-wider text-muted/60">{tt.maintLabel}</div>

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
                  <button onClick={() => handleDelAlias(a.id, a.keyword)} title={t.common.delete}
                    className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                </span>
              ))}
              <span className="inline-flex items-center gap-1">
                <input
                  value={newAlias}
                  onChange={ev => setNewAlias(ev.target.value)}
                  onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddAlias() } }}
                  placeholder={tt.aliasKeywordPlaceholder}
                  className={chipInputClass}
                />
                <button onClick={handleAddAlias} disabled={addAliasMut.isPending}
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
                  <button onClick={() => handleDelAppId(a.id, a.app_id)} title={t.common.delete}
                    className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                </span>
              ))}
              <span className="inline-flex items-center gap-1">
                <input
                  value={newAppId}
                  onChange={ev => setNewAppId(ev.target.value)}
                  onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddAppId() } }}
                  placeholder={tt.appIdPlaceholder}
                  className={chipInputClass}
                />
                <button onClick={handleAddAppId} disabled={addAppIdMut.isPending}
                  className="p-1 text-muted hover:text-accent transition-colors" title={tt.addAppId}>
                  <Plus size={14} />
                </button>
              </span>
            </div>
          </div>

          {/* App Store 开发者账号（iTunes artistId，清单 diff 抓未进榜新上架） */}
          <div className="border-t border-default pt-3 space-y-2">
            <div className="text-[11px] text-secondary" title={tt.artistsHint}>
              {tt.artistsLabel}（{e.itunes_artists.length}）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {e.itunes_artists.map(a => (
                <span key={a.id}
                  title={a.last_synced_at ? tt.artistSyncedAt(a.last_synced_at.slice(0, 10)) : tt.artistNeverSynced}
                  className="inline-flex items-center gap-1.5 text-xs text-primary bg-elevated border border-default rounded-lg pl-2.5 pr-1.5 py-1">
                  {a.platform === 'gp' && (
                    <span className="text-[10px] font-semibold text-emerald-400 bg-emerald-400/10 border border-emerald-400/30 rounded px-1 font-data">GP</span>
                  )}
                  <span className="font-data">{a.artist_id}</span>
                  {a.label && <span className="text-muted">· {a.label}</span>}
                  <span className={`w-1.5 h-1.5 rounded-full ${a.last_synced_at ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                  <button onClick={() => handleDelArtist(a.id, a.artist_id)} title={t.common.delete}
                    className="text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                </span>
              ))}
              <span className="inline-flex items-center gap-1">
                <select
                  value={newArtist.platform}
                  onChange={ev => setNewArtist(s => ({ ...s, platform: ev.target.value as 'ios' | 'gp' }))}
                  className={chipInputClass}
                >
                  <option value="ios">iOS</option>
                  <option value="gp">GP</option>
                </select>
                <input
                  value={newArtist.artist_id}
                  onChange={ev => setNewArtist(s => ({ ...s, artist_id: ev.target.value }))}
                  onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddArtist() } }}
                  placeholder={tt.artistIdPlaceholder}
                  className={chipInputClass}
                />
                <input
                  value={newArtist.label}
                  onChange={ev => setNewArtist(s => ({ ...s, label: ev.target.value }))}
                  onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddArtist() } }}
                  placeholder={tt.artistLabelPlaceholder}
                  className={chipInputClass}
                />
                <button onClick={handleAddArtist} disabled={addArtistMut.isPending}
                  className="p-1 text-muted hover:text-accent transition-colors" title={tt.addArtist}>
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
                <div key={s.id} className="bg-elevated border border-default rounded-lg px-2.5 py-1.5 space-y-0.5">
                  <div className="flex items-center gap-2 text-xs">
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
                    <button onClick={() => handleDelSource(s.id)} title={t.common.delete}
                      className="ml-auto shrink-0 text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                  </div>
                  {s.note && (
                    <div className="text-[10px] text-muted truncate" title={s.note}>{s.note}</div>
                  )}
                </div>
              ))}
              {e.sources.length === 0 && <div className="text-[11px] text-muted">{tt.noSources}</div>}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <input
                value={srcForm.url}
                onChange={ev => setSrcForm(s => ({ ...s, url: ev.target.value }))}
                onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddSource() } }}
                placeholder={tt.sourceUrlPlaceholder}
                className="bg-elevated border border-default rounded-lg px-2.5 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 flex-1 min-w-[160px]"
              />
              <input
                value={srcForm.title}
                onChange={ev => setSrcForm(s => ({ ...s, title: ev.target.value }))}
                placeholder={tt.sourceTitlePlaceholder}
                className={chipInputClass}
              />
              <select
                value={srcForm.source_type}
                onChange={ev => setSrcForm(s => ({ ...s, source_type: ev.target.value as PublisherSourceType }))}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
              >
                {SOURCE_TYPE_ORDER.map(st => <option key={st} value={st}>{tt.sourceTypes[st]}</option>)}
              </select>
              <select
                value={srcForm.confidence}
                onChange={ev => setSrcForm(s => ({ ...s, confidence: ev.target.value }))}
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
                value={srcForm.as_of}
                onChange={ev => setSrcForm(s => ({ ...s, as_of: ev.target.value }))}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
              />
              <input
                value={srcForm.note}
                onChange={ev => setSrcForm(s => ({ ...s, note: ev.target.value }))}
                onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddSource() } }}
                placeholder={tt.noteOptionalPlaceholder}
                className={chipInputClass}
              />
              <button onClick={handleAddSource} disabled={addSourceMut.isPending}
                className="p-1 text-muted hover:text-accent transition-colors" title={tt.addSource}>
                <Plus size={14} />
              </button>
            </div>
          </div>

          {/* 股权/母子关系 */}
          <div className="border-t border-default pt-3 space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] text-secondary">
              <Network size={12} />{tt.relationsLabel}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="space-y-1.5">
                <div className="text-[10px] text-muted">{tt.parentsLabel}</div>
                {e.parents.length === 0 && <div className="text-[11px] text-muted">{tt.noParents}</div>}
                {e.parents.map(p => (
                  <div key={p.relation_id} className="bg-elevated border border-default rounded-lg px-2.5 py-1.5 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs">
                      <Building2 size={11} className="text-accent shrink-0" />
                      <span className="text-primary truncate">{p.name}</span>
                      <span className="shrink-0 text-[10px] text-secondary">
                        {tt.relationTypes[p.relation_type]}{p.stake_pct != null ? ` · ${tt.stakeSuffix(p.stake_pct)}` : ''}
                      </span>
                      <button onClick={() => handleDelRelation(p.relation_id)} title={t.common.delete}
                        className="ml-auto shrink-0 text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                    </div>
                    {p.note && (
                      <div className="text-[10px] text-muted truncate" title={p.note}>{p.note}</div>
                    )}
                  </div>
                ))}
              </div>
              <div className="space-y-1.5">
                <div className="text-[10px] text-muted">{tt.childrenLabel}</div>
                {e.children.length === 0 && <div className="text-[11px] text-muted">{tt.noChildren}</div>}
                {e.children.map(c => (
                  <div key={c.relation_id} className="bg-elevated border border-default rounded-lg px-2.5 py-1.5 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs">
                      <Building2 size={11} className="text-secondary shrink-0" />
                      <span className="text-primary truncate">{c.name}</span>
                      <span className="shrink-0 text-[10px] text-secondary">
                        {tt.relationTypes[c.relation_type]}{c.stake_pct != null ? ` · ${tt.stakeSuffix(c.stake_pct)}` : ''}
                      </span>
                      <button onClick={() => handleDelRelation(c.relation_id)} title={t.common.delete}
                        className="ml-auto shrink-0 text-muted hover:text-red-400 transition-colors"><X size={12} /></button>
                    </div>
                    {c.note && (
                      <div className="text-[10px] text-muted truncate" title={c.note}>{c.note}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
            {/* 添加关系 */}
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <select
                value={relForm.counterpart_role}
                onChange={ev => setRelForm(s => ({ ...s, counterpart_role: ev.target.value as RelationCounterpartRole }))}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
              >
                <option value="parent">{tt.roleParent}</option>
                <option value="child">{tt.roleChild}</option>
              </select>
              <select
                value={relForm.counterpart_id}
                onChange={ev => setRelForm(s => ({ ...s, counterpart_id: ev.target.value }))}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500 flex-1 min-w-[140px]"
              >
                <option value="">{tt.relationPickCounterpart}</option>
                {entities.filter(o => o.id !== e.id).map(o => (
                  <option key={o.id} value={o.id}>{o.name}</option>
                ))}
              </select>
              <select
                value={relForm.relation_type}
                onChange={ev => setRelForm(s => ({ ...s, relation_type: ev.target.value as PublisherRelationType }))}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary focus:outline-none focus:border-brand-500"
              >
                {RELATION_TYPE_ORDER.map(rt => <option key={rt} value={rt}>{tt.relationTypes[rt]}</option>)}
              </select>
              <input
                type="number" min={0} max={100} step="0.01"
                value={relForm.stake_pct}
                onChange={ev => setRelForm(s => ({ ...s, stake_pct: ev.target.value }))}
                placeholder={tt.stakePlaceholder}
                className="bg-elevated border border-default rounded-lg px-2 py-1 text-xs text-primary placeholder:text-muted focus:outline-none focus:border-brand-500 w-20"
              />
              <input
                value={relForm.note}
                onChange={ev => setRelForm(s => ({ ...s, note: ev.target.value }))}
                onKeyDown={ev => { if (ev.key === 'Enter') { ev.preventDefault(); handleAddRelation() } }}
                placeholder={tt.noteOptionalPlaceholder}
                className={chipInputClass}
              />
              <button onClick={handleAddRelation} disabled={addRelationMut.isPending}
                className="p-1 text-muted hover:text-accent transition-colors" title={tt.addRelation}>
                <Plus size={14} />
              </button>
            </div>
          </div>
        </div>
      </aside>
    </>
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
