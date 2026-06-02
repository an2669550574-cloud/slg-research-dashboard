import { useEffect, useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { useNavigate, Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Sparkles, ChevronRight, Film as FilmIcon, Radio, Tag as TagIcon, ArrowLeft, Download as DownloadIcon, Search, Layers, X } from 'lucide-react'
import { materialsApi, gamesApi } from '../lib/api'
import type { MaterialListParams } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { PageHeader } from '../components/PageHeader'
import { Select } from '../components/Select'
import { Pagination } from '../components/Pagination'
import { QueryError } from '../components/QueryError'
import { UnifiedDirectionsModal } from '../components/UnifiedDirectionsModal'
import { downloadCsv } from '../lib/csv'
import { useDebouncedValue } from '../lib/hooks'
import { useT } from '../i18n'
import type { MaterialOut } from '../lib/types'

const PAGE_SIZE = 12

function formatTs(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function MaterialAnalysis() {
  const navigate = useNavigate()
  const t = useT()
  const [filterGame, setFilterGame] = useState('')
  const [filterPlatform, setFilterPlatform] = useState('')
  const [filterTag, setFilterTag] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('analyzed_at:desc')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [unifiedOpen, setUnifiedOpen] = useState(false)
  const debouncedSearch = useDebouncedValue(search)

  const MAX_UNIFIED = 15
  const toggleSelect = (id: number) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else if (next.size < MAX_UNIFIED) next.add(id)
    return next
  })
  const clearSelected = () => setSelected(new Set())

  const [sortBy, order] = sort.split(':') as [
    NonNullable<MaterialListParams['sort_by']>, 'asc' | 'desc',
  ]

  useEffect(() => { setOffset(0) }, [filterGame, filterPlatform, filterTag, debouncedSearch, sort])

  // 只拉「已分析完成」的素材（后端 analysis_status=done）。纯读，零额外 ST/LLM 配额。
  const listParams: MaterialListParams & { app_id?: string } = {
    analysis_status: 'done',
    app_id: filterGame || undefined,
    platform: filterPlatform || undefined,
    tag: filterTag || undefined,
    q: debouncedSearch || undefined,
    sort_by: sortBy, order,
  }
  const { data: paged, isLoading, isError, refetch } = useQuery({
    queryKey: ['materials', 'analysis', filterGame, filterPlatform, filterTag, debouncedSearch, sort, offset],
    queryFn: () => materialsApi.listPaged({ ...listParams, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })
  const rows: MaterialOut[] = paged?.items ?? []
  const hasFilters = Boolean(filterGame || filterPlatform || filterTag || debouncedSearch)
  const total = paged?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const pageIds = rows.map(m => m.id)
  const allPageSelected = pageIds.length > 0 && pageIds.every(id => selected.has(id))
  const toggleSelectAllPage = () => setSelected(prev => {
    const next = new Set(prev)
    if (allPageSelected) pageIds.forEach(id => next.delete(id))
    else for (const id of pageIds) { if (next.size >= MAX_UNIFIED) break; next.add(id) }
    return next
  })

  const { data: allGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })
  const gameMap = useMemo(() => Object.fromEntries(allGames.map(g => [g.app_id, g])), [allGames])

  const { data: tagCounts = [] } = useQuery({
    queryKey: ['materialTags', filterGame],
    queryFn: () => materialsApi.tags(filterGame || undefined),
  })

  const platLabel = (p: string) =>
    t.materials.platforms[p as keyof typeof t.materials.platforms] || PLATFORM_CONFIG[p]?.label || p
  const PLATFORM_TABS = ['', 'youtube', 'tiktok', 'meta', 'other']
  const sortOptions = [
    { value: 'analyzed_at:desc', label: t.materialAnalysis.sortRecent },
    { value: 'analyzed_at:asc', label: t.materialAnalysis.sortOldest },
    { value: 'analysis_cost_usd:desc', label: t.materialAnalysis.sortCostDesc },
    { value: 'title:asc', label: t.materialAnalysis.sortTitle },
  ]

  const thumb = (m: MaterialOut) => {
    const frame = m.analysis_frames?.[0]?.url
    if (frame) return <img src={frame} alt="" loading="lazy" className="w-full h-full object-cover" />
    if (m.source === 'upload' && m.stream_url && m.material_type === 'video')
      return <video src={m.stream_url} preload="metadata" className="w-full h-full object-cover" muted />
    return <div className="w-full h-full grid place-items-center text-muted/40"><FilmIcon size={20} /></div>
  }

  const exportCsv = async () => {
    // 导出整套匹配的已分析素材（不只当前页）。limit=200 是后端硬上限；纯读 DB，零额外配额。
    const all = await materialsApi.listPaged({ ...listParams, limit: 200, offset: 0 }).catch(() => null)
    if (!all || all.items.length === 0) { toast.error(t.common.noExportData); return }
    const date = new Date().toISOString().slice(0, 10)
    downloadCsv(`material-analysis-${date}.csv`, all.items, [
      { header: t.csv.game, get: (m: MaterialOut) => gameMap[m.app_id]?.name || m.app_id },
      { header: t.csv.title, get: (m: MaterialOut) => m.title },
      { header: t.csv.platform, get: (m: MaterialOut) => (m.platform ? platLabel(m.platform) : '') },
      { header: t.csv.type, get: (m: MaterialOut) => m.material_type },
      { header: t.csv.brief, get: (m: MaterialOut) => m.analysis_brief ?? '' },
      { header: t.csv.hooks, get: (m: MaterialOut) => (m.analysis_hooks ?? []).map(h => `${formatTs(h.ts)} ${h.kind}${h.note ? `: ${h.note}` : ''}`).join(' | ') },
      { header: t.csv.aiTags, get: (m: MaterialOut) => (m.analysis_tags ?? []).join(';') },
      { header: t.csv.tags, get: (m: MaterialOut) => m.tags.join(';') },
      { header: t.csv.analysisCost, get: (m: MaterialOut) => m.analysis_cost_usd ?? '' },
      { header: t.csv.analysisModel, get: (m: MaterialOut) => m.analysis_model ?? '' },
      { header: t.csv.analyzedAt, get: (m: MaterialOut) => m.analyzed_at ?? '' },
      { header: t.csv.url, get: (m: MaterialOut) => m.url ?? m.file_name ?? '' },
    ])
    toast.success(t.common.exported(all.items.length))
  }

  return (
    <div className="min-h-full px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto">
      <PageHeader
        eyebrow="Creative Intel · AI"
        title={t.materialAnalysis.title}
        subtitle={t.materialAnalysis.subtitle}
        stats={[
          { label: 'ANALYZED', value: <span className="text-primary font-bold">{total}</span> },
          { label: 'GAME', value: filterGame ? (gameMap[filterGame]?.name ?? filterGame) : 'ALL' },
          { label: 'TAG', value: filterTag ? <span className="text-accent">{filterTag}</span> : '—' },
          { label: 'PAGE', value: `${page} / ${pages}` },
        ]}
      >
        <button onClick={exportCsv}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
        <button onClick={() => navigate('/materials')}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
          <ArrowLeft size={14} />
          <span className="hidden sm:inline">{t.materialAnalysis.backToLibrary}</span>
        </button>
      </PageHeader>

      {/* ══ FILTERS ══════════════════════════════════════════ */}
      <div className="reveal reveal-2 mt-6 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center flex-1 min-w-[220px] max-w-md rounded-lg border border-default bg-surface/60 focus-within:border-accent transition-colors">
            <span className="pl-3 pr-1 text-muted"><Search size={15} /></span>
            <input type="text" placeholder={t.materialAnalysis.searchPlaceholder} value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-transparent py-2.5 pr-3 text-sm text-primary placeholder:text-muted focus:outline-none" />
          </div>
          <div className="w-48">
            <Select aria-label={t.materials.gameFilterAll} value={filterGame} onChange={setFilterGame}
              options={[{ value: '', label: t.materials.gameFilterAll },
                ...allGames.map(g => ({ value: g.app_id, label: g.name }))]} />
          </div>
          <div className="w-40">
            <Select aria-label={t.materialAnalysis.sortLabel} value={sort} onChange={setSort} options={sortOptions} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 p-1 rounded-lg border border-default bg-surface/60">
            {PLATFORM_TABS.map(p => {
              const label = p === '' ? t.materials.platforms.all : platLabel(p)
              const activeTab = filterPlatform === p
              return (
                <button key={p} onClick={() => setFilterPlatform(p)}
                  className={`px-3 py-1.5 rounded-md font-data text-[11px] tracking-wide transition-colors ${activeTab ? 'bg-accent/15 text-accent' : 'text-secondary hover:text-primary hover:bg-elevated'}`}>
                  {label}
                </button>
              )
            })}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-muted pr-1">
            <TagIcon size={13} /> {t.materials.tagFilterLabel}
          </span>
          <button onClick={() => setFilterTag('')}
            className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${filterTag === '' ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
            {t.materials.tagFilterAll}
          </button>
          {tagCounts.length === 0 ? (
            <span className="text-xs text-muted/60">{t.materials.noTags}</span>
          ) : tagCounts.map(({ tag, count }) => {
            const on = filterTag === tag
            return (
              <button key={tag} onClick={() => setFilterTag(on ? '' : tag)} title={tag}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border transition-colors ${on ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
                <span className="max-w-[160px] truncate">{tag}</span>
                <span className="font-data text-[10px] text-muted">{count}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* ══ SELECTION ACTION BAR ══════════════════════════════ */}
      {selected.size > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-accent/40 bg-accent/10 px-4 py-3">
          <span className="flex items-center gap-1.5 text-xs text-accent font-data">
            <Sparkles size={13} /> {t.materialAnalysis.unified.selectedCount(selected.size)}
          </span>
          <span className="text-[11px] text-muted">
            {selected.size < 2 ? t.materialAnalysis.unified.minHint : t.materialAnalysis.unified.maxHint(MAX_UNIFIED)}
          </span>
          <div className="flex items-center gap-2 ml-auto">
            <button onClick={clearSelected}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary transition-colors">
              <X size={12} /> {t.materialAnalysis.unified.clear}
            </button>
            <button onClick={() => setUnifiedOpen(true)} disabled={selected.size < 2}
              className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-data bg-accent/20 border border-accent/50 text-accent hover:bg-accent/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
              <Layers size={13} /> {t.materialAnalysis.unified.action}
            </button>
          </div>
        </div>
      )}

      {/* ══ REPORT TABLE ══════════════════════════════════════ */}
      <div className="reveal reveal-3 mt-6">
        {isError ? (
          <QueryError onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-28 rounded-xl border border-default bg-surface/60 animate-pulse" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="hud relative flex flex-col items-center justify-center py-24 rounded-2xl border border-default bg-surface/40 text-center">
            <Radio size={26} className="text-muted/50 mb-3" />
            <div className="eyebrow text-muted">No Signal</div>
            <p className="text-secondary text-sm mt-2">
              {hasFilters ? t.materialAnalysis.noResult : t.materialAnalysis.empty}
            </p>
            <p className="text-muted text-xs mt-1 max-w-sm">
              {hasFilters ? t.materialAnalysis.noResultHint : t.materialAnalysis.emptyHint}
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-default bg-surface/60">
            {/* header row */}
            <div className="hidden md:grid grid-cols-[2rem_3rem_11rem_9rem_1fr] gap-4 px-4 py-2.5 border-b border-default eyebrow text-muted">
              <span className="flex items-center">
                <input type="checkbox" checked={allPageSelected} onChange={toggleSelectAllPage}
                  aria-label={t.materialAnalysis.unified.selectAll}
                  className="w-4 h-4 accent-[var(--accent)] cursor-pointer" />
              </span>
              <span>{t.materialAnalysis.colNo}</span>
              <span>{t.materialAnalysis.colApp}</span>
              <span>{t.materialAnalysis.colMaterial}</span>
              <span>{t.materialAnalysis.colAnalysis}</span>
            </div>
            {rows.map((m, i) => {
              const game = gameMap[m.app_id]
              return (
                <div key={m.id}
                  className={`group grid grid-cols-1 md:grid-cols-[2rem_3rem_11rem_9rem_1fr] gap-3 md:gap-4 px-4 py-4 border-b border-default last:border-0 transition-colors ${selected.has(m.id) ? 'bg-accent/[0.06]' : 'hover:bg-elevated/40'}`}>
                  {/* checkbox */}
                  <div className="flex items-start md:items-center">
                    <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggleSelect(m.id)}
                      aria-label={t.materialAnalysis.unified.selectRow}
                      className="w-4 h-4 accent-[var(--accent)] cursor-pointer" />
                  </div>
                  {/* 整行点击进入整页详情（display:contents 让 Link 横跨剩余列）。
                      抽屉只保留在素材库做即时触发，本页直达「完整解析」整页 */}
                  <Link to={`/materials/${m.id}/analysis`} className="contents text-left">
                  {/* No. */}
                  <div className="hidden md:block font-data text-accent text-sm pt-1">
                    {String(offset + i + 1).padStart(2, '0')}
                  </div>
                  {/* App */}
                  <div className="flex items-center gap-2.5 min-w-0">
                    {game?.icon_url ? (
                      <img src={game.icon_url} alt="" className="w-9 h-9 rounded-lg border border-default shrink-0 object-cover" />
                    ) : (
                      <div className="w-9 h-9 rounded-lg border border-default shrink-0 grid place-items-center text-muted/50"><FilmIcon size={15} /></div>
                    )}
                    <div className="min-w-0">
                      <div className="text-sm text-primary truncate">{game?.name ?? m.app_id}</div>
                      <div className="text-[11px] text-muted truncate">{m.platform ? platLabel(m.platform) : '—'}</div>
                    </div>
                  </div>
                  {/* Material thumbnail */}
                  <div className="hud relative w-full md:w-36 aspect-video rounded-lg border border-default overflow-hidden bg-black shrink-0">
                    {thumb(m)}
                    <span className="absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-data bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 backdrop-blur-sm">
                      <Sparkles size={9} /> AI
                    </span>
                  </div>
                  {/* AI analysis */}
                  <div className="min-w-0 space-y-2">
                    <div className="font-display font-bold text-primary text-sm leading-tight line-clamp-1">{m.title}</div>
                    <p className="text-xs text-secondary leading-relaxed line-clamp-2">
                      {m.analysis_brief || t.materialAnalysis.noBrief}
                    </p>
                    {m.analysis_hooks && m.analysis_hooks.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {m.analysis_hooks.slice(0, 4).map((h, j) => (
                          <span key={j} className="inline-flex items-center gap-1 text-[10px] font-data px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">
                            <span className="text-accent/70">{formatTs(h.ts)}</span>{h.kind}
                          </span>
                        ))}
                      </div>
                    )}
                    {m.analysis_tags && m.analysis_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {m.analysis_tags.slice(0, 6).map(tag => (
                          <span key={tag} className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-default bg-elevated text-secondary">
                            <TagIcon size={9} />{tag}
                          </span>
                        ))}
                      </div>
                    )}
                    <span className="inline-flex items-center gap-1 text-[11px] text-accent opacity-0 group-hover:opacity-100 transition-opacity">
                      {t.materialAnalysis.open} <ChevronRight size={13} />
                    </span>
                  </div>
                  </Link>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="reveal reveal-4 mt-7">
        <Pagination total={total} offset={offset} pageSize={PAGE_SIZE} onOffsetChange={setOffset} />
      </div>

      <UnifiedDirectionsModal open={unifiedOpen} materialIds={[...selected]}
        onClose={() => setUnifiedOpen(false)} />
    </div>
  )
}
