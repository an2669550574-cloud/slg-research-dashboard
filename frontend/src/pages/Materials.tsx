import { useEffect, useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { materialsApi, gamesApi } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { ExternalLink, Trash2, Plus, Search, Download as DownloadIcon, Upload, Film as FilmIcon, Radio } from 'lucide-react'
import { MaterialPreview } from '../components/MaterialPreview'
import { Select } from '../components/Select'
import { useNavigate } from 'react-router-dom'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Pagination } from '../components/Pagination'
import { QueryError } from '../components/QueryError'
import { useDebouncedValue } from '../lib/hooks'
import type { MaterialOut } from '../lib/types'

const PAGE_SIZE = 12
const MAX_UPLOAD = 200 * 1024 * 1024
const ACCEPT = '.mp4,.webm,.mov,.m4v,.jpg,.jpeg,.png,.gif,.webp'

const inputClass =
  "w-full bg-elevated/60 border border-default rounded-lg px-3 py-2.5 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 transition-colors"

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-muted/70">{label}</span>
      <span className="text-accent">▸</span>
      <span className="text-secondary">{value}</span>
    </span>
  )
}

export default function Materials() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterPlatform, setFilterPlatform] = useState('')
  const [offset, setOffset] = useState(0)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
  const [mode, setMode] = useState<'link' | 'upload'>('link')
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState(0)
  const debouncedSearch = useDebouncedValue(search)

  useEffect(() => { setOffset(0) }, [debouncedSearch, filterPlatform])

  const { data: paged, isLoading, isError, refetch } = useQuery({
    queryKey: ['materials', debouncedSearch, filterPlatform, offset],
    queryFn: () => materialsApi.listPaged({
      limit: PAGE_SIZE, offset,
      q: debouncedSearch || undefined,
      platform: filterPlatform || undefined,
    }),
    placeholderData: keepPreviousData,
  })
  const materials: MaterialOut[] = paged?.items ?? []
  const total = paged?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const { data: allGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  const resetForm = () => {
    setShowForm(false)
    setForm({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
    setMode('link'); setFile(null); setProgress(0)
    qc.invalidateQueries({ queryKey: ['materials'] })
    toast.success(t.materials.addedToast)
  }
  const createMut = useMutation({ mutationFn: (data: any) => materialsApi.create(data), onSuccess: resetForm })
  const uploadMut = useMutation({
    mutationFn: (fd: FormData) => materialsApi.upload(fd, setProgress),
    onSuccess: resetForm, onError: () => setProgress(0),
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['materials'] }); toast.success(t.materials.deletedToast) },
  })

  const gameMap = useMemo(() => Object.fromEntries(allGames.map(g => [g.app_id, g])), [allGames])
  const typeLabel = (kind: string) => t.materials.types[kind as keyof typeof t.materials.types] || kind
  const platLabel = (p: string) =>
    t.materials.platforms[p as keyof typeof t.materials.platforms] || PLATFORM_CONFIG[p]?.label || p

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const tags = form.tags ? form.tags.split(',').map((s: string) => s.trim()) : []
    if (mode === 'link') { createMut.mutate({ ...form, tags }); return }
    if (!file) { toast.error(t.materials.chooseFile); return }
    if (file.size > MAX_UPLOAD) { toast.error(t.materials.fileTooLarge(200)); return }
    const fd = new FormData()
    fd.append('file', file)
    fd.append('title', form.title)
    fd.append('app_id', form.app_id)
    fd.append('platform', form.platform)
    fd.append('material_type', form.material_type)
    fd.append('tags', tags.join(','))
    if (form.notes) fd.append('notes', form.notes)
    uploadMut.mutate(fd)
  }

  const exportCsv = async () => {
    // 导出整套匹配结果（不只当前页）。limit=200 是后端硬上限。
    const all = await materialsApi.listPaged({
      limit: 200, offset: 0,
      q: debouncedSearch || undefined,
      platform: filterPlatform || undefined,
    }).catch(() => null)
    if (!all || all.items.length === 0) { toast.error(t.common.noExportData); return }
    const date = new Date().toISOString().slice(0, 10)
    downloadCsv(`materials-${date}.csv`, all.items, [
      { header: t.csv.game, get: (m: MaterialOut) => gameMap[m.app_id]?.name || m.app_id },
      { header: t.csv.title, get: (m: MaterialOut) => m.title },
      { header: t.csv.platform, get: (m: MaterialOut) => m.platform ?? '' },
      { header: t.csv.type, get: (m: MaterialOut) => m.material_type },
      { header: t.csv.url, get: (m: MaterialOut) => m.url ?? m.file_name ?? '' },
      { header: t.csv.tags, get: (m: MaterialOut) => m.tags.join(';') },
      { header: t.csv.notes, get: (m: MaterialOut) => m.notes ?? '' },
      { header: t.csv.createdAt, get: (m: MaterialOut) => m.created_at },
    ])
    toast.success(t.common.exported(all.items.length))
  }

  const PLATFORM_TABS = ['', 'youtube', 'tiktok', 'meta', 'other']

  const AssetCard = ({ m, n, featured }: { m: MaterialOut; n: number; featured?: boolean }) => {
    const platCfg = (m.platform && PLATFORM_CONFIG[m.platform]) || PLATFORM_CONFIG.other
    const game = gameMap[m.app_id]
    const href = (m.source === 'upload' ? m.stream_url : m.url) as string | undefined
    const hasPreview = m.source === 'upload' && !!m.stream_url
    const media = (
      <div className="hud relative aspect-video w-full bg-gradient-to-br from-elevated to-base overflow-hidden">
        {hasPreview ? <MaterialPreview m={m} fill /> : (
          <div className="absolute inset-0 grid place-items-center text-muted/40">
            <FilmIcon size={featured ? 40 : 26} />
          </div>
        )}
        <span className="absolute top-3 left-3 font-data text-[10px] tracking-wider px-2 py-0.5 rounded bg-base/75 backdrop-blur-sm text-secondary border border-default">
          {(m.platform ? platLabel(m.platform) : platCfg.label).toUpperCase()}
        </span>
        {href && (
          <a href={href} target="_blank" rel="noopener noreferrer" title={t.materials.openFile}
            className="absolute top-3 right-3 p-1.5 rounded bg-base/75 backdrop-blur-sm text-secondary hover:text-accent opacity-0 group-hover:opacity-100 transition-opacity">
            <ExternalLink size={14} />
          </a>
        )}
      </div>
    )
    const meta = (
      <div className="flex flex-col gap-2 p-4">
        <div className="font-data text-[10px] text-muted flex items-center gap-2">
          <span className="text-accent">{String(n).padStart(2, '0')}</span>
          <span className="text-muted/40">/</span>
          <span className="uppercase tracking-wider">{typeLabel(m.material_type)}</span>
          {game && (
            <>
              <span className="text-muted/50">·</span>
              <button onClick={() => navigate(`/game/${m.app_id}`)}
                className="text-accent hover:underline truncate max-w-[150px] normal-case tracking-normal">
                {game.name}
              </button>
            </>
          )}
        </div>
        <div className={`font-display font-bold text-primary leading-tight line-clamp-2 ${featured ? 'text-xl' : 'text-[15px]'}`}>
          {m.title}
        </div>
        {m.notes && <div className="text-xs text-muted line-clamp-1">{m.notes}</div>}
        {m.tags?.length > 0 && (
          <div className="flex gap-1.5 flex-wrap pt-0.5">
            {m.tags.map((tag: string) => (
              <span key={tag} className="font-data px-2 py-0.5 rounded bg-elevated border border-default text-[10px] text-secondary">{tag}</span>
            ))}
          </div>
        )}
      </div>
    )
    return (
      <div className={`group relative flex rounded-xl border border-default bg-surface/80 overflow-hidden shadow-card transition-all duration-200 hover:border-strong hover:-translate-y-0.5 ${featured ? 'flex-col lg:flex-row' : 'flex-col'}`}>
        <div className={featured ? 'lg:w-3/5 shrink-0' : ''}>{media}</div>
        <div className="flex-1 flex flex-col justify-between">
          {meta}
        </div>
        <button onClick={() => deleteMut.mutate(m.id)} aria-label="delete"
          className="absolute bottom-3 right-3 p-1.5 rounded text-muted hover:text-red-400 hover:bg-base/60 opacity-0 group-hover:opacity-100 transition-all">
          <Trash2 size={14} />
        </button>
      </div>
    )
  }

  return (
    <div className="min-h-full px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto">
      {/* ══ MASTHEAD ══════════════════════════════════════════ */}
      <header className="reveal reveal-1">
        <div className="flex items-center gap-2.5 eyebrow text-muted">
          <span className="w-1.5 h-1.5 rounded-full bg-signal pulse-dot inline-block" />
          Creative&nbsp;Intel
        </div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="font-display text-[38px] sm:text-[50px] leading-[0.92] font-extrabold text-primary">
              {t.materials.title}
            </h1>
            <p className="text-secondary text-sm mt-2.5 max-w-md">{t.materials.subtitle}</p>
          </div>
          <div className="flex items-center gap-2.5">
            <button onClick={exportCsv}
              className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
              <DownloadIcon size={14} />
              <span className="hidden sm:inline">{t.common.export}</span>
            </button>
            <button onClick={() => setShowForm(!showForm)}
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent transition-all">
              <Plus size={15} />
              {t.materials.addMaterial}
            </button>
          </div>
        </div>
        {/* 遥测条 */}
        <div className="mt-5 flex flex-wrap items-center gap-x-7 gap-y-2 font-data text-[11px]">
          <Stat label="ASSETS" value={<span className="text-primary font-bold">{total}</span>} />
          <Stat label="FILTER" value={(filterPlatform ? platLabel(filterPlatform) : 'ALL').toUpperCase()} />
          <Stat label="QUERY" value={debouncedSearch ? `"${debouncedSearch}"` : '—'} />
          <Stat label="PAGE" value={`${page} / ${pages}`} />
        </div>
        <div className="scan-rule mt-4" />
      </header>

      {showForm && (
        <form onSubmit={handleSubmit}
          className="reveal mt-6 rounded-2xl border border-strong bg-surface shadow-pop p-5 sm:p-6 space-y-4">
          <div className="eyebrow text-muted">{t.materials.addMaterialFormTitle}</div>
          <input required placeholder={t.materials.titlePlaceholder} value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))} className={inputClass} />
          <div className="inline-flex gap-1 bg-elevated rounded-lg p-1 border border-default">
            {(['link', 'upload'] as const).map(md => (
              <button type="button" key={md} onClick={() => setMode(md)}
                className={`px-3.5 py-1.5 rounded-md font-data text-xs transition-colors ${mode === md ? 'bg-accent text-white' : 'text-secondary hover:text-primary'}`}>
                {md === 'link' ? t.materials.sourceLink : t.materials.sourceUpload}
              </button>
            ))}
          </div>
          {mode === 'link' ? (
            <input required placeholder={t.materials.urlPlaceholder} value={form.url}
              onChange={e => setForm(f => ({ ...f, url: e.target.value }))} className={inputClass} />
          ) : (
            <div className="space-y-2">
              <label className="flex items-center justify-center gap-2 px-3 py-7 bg-elevated/40 border border-dashed border-strong rounded-xl text-sm text-secondary cursor-pointer hover:text-primary hover:border-accent hover:bg-elevated transition-colors">
                <Upload size={18} className="shrink-0 text-accent" />
                <span className="truncate">
                  {file ? `${file.name} (${(file.size / 1048576).toFixed(1)}MB)` : t.materials.chooseFile}
                </span>
                <input type="file" accept={ACCEPT} className="hidden"
                  onChange={e => {
                    const f = e.target.files?.[0] ?? null
                    if (f && f.size > MAX_UPLOAD) { toast.error(t.materials.fileTooLarge(200)); return }
                    setFile(f)
                    if (f && !form.title) setForm(s => ({ ...s, title: f.name.replace(/\.[^.]+$/, '') }))
                  }} />
              </label>
              <div className="font-data text-[11px] text-muted">{t.materials.maxHint}</div>
              {uploadMut.isPending && (
                <div className="space-y-1">
                  <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
                    <div className="h-full bg-accent transition-all duration-300" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="font-data text-[11px] text-muted text-right">{t.materials.uploading} {progress}%</div>
                </div>
              )}
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Select aria-label={t.materials.selectGame} value={form.app_id}
              onChange={v => setForm(f => ({ ...f, app_id: v }))}
              options={[{ value: '', label: t.materials.selectGame },
                ...allGames.map(g => ({ value: g.app_id, label: g.name }))]} />
            <Select value={form.platform} onChange={v => setForm(f => ({ ...f, platform: v }))}
              options={[{ value: 'youtube', label: 'YouTube' }, { value: 'tiktok', label: 'TikTok' },
                { value: 'meta', label: 'Meta Ads' }, { value: 'other', label: t.materials.platforms.other }]} />
            <Select value={form.material_type} onChange={v => setForm(f => ({ ...f, material_type: v }))}
              options={[{ value: 'video', label: t.materials.types.video },
                { value: 'image', label: t.materials.types.image },
                { value: 'playable', label: t.materials.types.playable }]} />
            <input placeholder={t.materials.tagsPlaceholder} value={form.tags}
              onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} className={inputClass} />
          </div>
          <input placeholder={t.materials.notesPlaceholder} value={form.notes}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} className={inputClass} />
          <div className="flex justify-end gap-2 border-t border-default pt-4">
            <button type="button" onClick={() => { setShowForm(false); setFile(null); setMode('link') }}
              className="px-4 py-2 text-sm text-secondary hover:text-primary transition-colors">{t.common.cancel}</button>
            <button type="submit" disabled={createMut.isPending || uploadMut.isPending}
              className="px-5 py-2 bg-accent hover:brightness-110 disabled:opacity-50 rounded-lg text-sm font-semibold text-white transition-all">
              {uploadMut.isPending ? `${t.materials.uploading} ${progress}%`
                : createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {/* ══ TOOLBAR ══════════════════════════════════════════ */}
      <div className="reveal reveal-2 mt-6 flex flex-wrap items-center gap-3">
        <div className="flex items-center flex-1 min-w-[220px] max-w-md rounded-lg border border-default bg-surface/60 focus-within:border-accent transition-colors">
          <span className="pl-3 pr-1 text-muted"><Search size={15} /></span>
          <input type="text" placeholder={t.materials.searchPlaceholder} value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-transparent py-2.5 pr-3 text-sm text-primary placeholder:text-muted focus:outline-none" />
        </div>
        <div className="flex gap-1 p-1 rounded-lg border border-default bg-surface/60">
          {PLATFORM_TABS.map(p => {
            const label = p === '' ? t.materials.platforms.all : platLabel(p)
            const active = filterPlatform === p
            return (
              <button key={p} onClick={() => setFilterPlatform(p)}
                className={`px-3 py-1.5 rounded-md font-data text-[11px] tracking-wide transition-colors ${active ? 'bg-accent/15 text-accent' : 'text-secondary hover:text-primary hover:bg-elevated'}`}>
                {label}
              </button>
            )
          })}
        </div>
      </div>

      {/* ══ GRID ══════════════════════════════════════════════ */}
      <div className="reveal reveal-3 mt-6">
        {isError ? (
          <QueryError onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="rounded-xl border border-default bg-surface/60 overflow-hidden">
                <div className="aspect-video bg-elevated animate-pulse" />
                <div className="p-4 space-y-2">
                  <div className="h-2.5 w-1/3 bg-elevated rounded animate-pulse" />
                  <div className="h-4 w-3/4 bg-elevated rounded animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        ) : materials.length === 0 ? (
          <div className="hud relative flex flex-col items-center justify-center py-24 rounded-2xl border border-default bg-surface/40 text-center">
            <Radio size={26} className="text-muted/50 mb-3" />
            <div className="eyebrow text-muted">No Signal</div>
            <p className="text-secondary text-sm mt-2">
              {debouncedSearch || filterPlatform ? t.common.noResult : t.materials.empty}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="reveal reveal-3"><AssetCard m={materials[0]} n={offset + 1} featured /></div>
            {materials.length > 1 && (
              <div className="reveal reveal-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {materials.slice(1).map((m, i) => (
                  <AssetCard key={m.id} m={m} n={offset + i + 2} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="reveal reveal-4 mt-7">
        <Pagination total={total} offset={offset} pageSize={PAGE_SIZE} onOffsetChange={setOffset} />
      </div>
    </div>
  )
}
