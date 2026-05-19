import { useEffect, useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { materialsApi, gamesApi } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { ExternalLink, Trash2, Plus, Search, Download as DownloadIcon, Upload, Film as FilmIcon, Library } from 'lucide-react'
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
  "w-full bg-elevated/70 border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 transition-colors"

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

  // 任一筛选条件变化都回到第一页
  useEffect(() => { setOffset(0) }, [debouncedSearch, filterPlatform])

  const { data: paged, isLoading, isError, refetch } = useQuery({
    queryKey: ['materials', debouncedSearch, filterPlatform, offset],
    queryFn: () => materialsApi.listPaged({
      limit: PAGE_SIZE,
      offset,
      q: debouncedSearch || undefined,
      platform: filterPlatform || undefined,
    }),
    placeholderData: keepPreviousData,
  })
  const materials: MaterialOut[] = paged?.items ?? []
  const total = paged?.total ?? 0

  // 关联游戏名映射，依然走 /games/ 全表（管理面板规模小，limit=200 已够）
  const { data: allGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  const resetForm = () => {
    setShowForm(false)
    setForm({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
    setMode('link')
    setFile(null)
    setProgress(0)
    qc.invalidateQueries({ queryKey: ['materials'] })
    toast.success(t.materials.addedToast)
  }
  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: resetForm,
  })
  const uploadMut = useMutation({
    mutationFn: (fd: FormData) => materialsApi.upload(fd, setProgress),
    onSuccess: resetForm,
    onError: () => setProgress(0),
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials'] })
      toast.success(t.materials.deletedToast)
    },
  })

  const gameMap = useMemo(() => Object.fromEntries(allGames.map(g => [g.app_id, g])), [allGames])
  const typeLabel = (kind: string) => t.materials.types[kind as keyof typeof t.materials.types] || kind

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const tags = form.tags ? form.tags.split(',').map((s: string) => s.trim()) : []
    if (mode === 'link') {
      createMut.mutate({ ...form, tags })
      return
    }
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
    // 导出整套匹配结果（不只当前页）。limit=200 是后端硬上限；
    // 实际素材库一般 <100 条，超出时会截断并提示用户。
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

  return (
    <div className="min-h-full px-4 sm:px-7 py-5 sm:py-7 max-w-[1400px] mx-auto">
      {/* ── 控制台头部 ─────────────────────────────────────────── */}
      <header className="reveal reveal-1 flex flex-wrap items-end justify-between gap-4 pb-5 border-b border-default">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.22em] text-muted">
            <Library size={13} className="text-accent" />
            Creative Intelligence
          </div>
          <h1 className="font-display text-[28px] sm:text-[32px] leading-none font-extrabold text-primary mt-2">
            {t.materials.title}
          </h1>
          <p className="text-secondary text-sm mt-2">{t.materials.subtitle}</p>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="hidden sm:flex items-baseline gap-1.5 mr-1 px-3 py-1.5 rounded-lg border border-default bg-surface/60">
            <span className="font-display text-xl font-bold text-primary tabular-nums">{total}</span>
            <span className="text-[11px] text-muted">{t.materials.countSuffix}</span>
          </div>
          <button onClick={exportCsv}
            className="flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
            <DownloadIcon size={14} />
            <span className="hidden sm:inline">{t.common.export}</span>
          </button>
          <button onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-accent hover:brightness-110 glow-accent transition-all">
            <Plus size={15} />
            {t.materials.addMaterial}
          </button>
        </div>
      </header>

      {showForm && (
        <form onSubmit={handleSubmit}
          className="reveal mt-5 rounded-2xl border border-strong bg-surface shadow-pop p-5 sm:p-6 space-y-4">
          <h3 className="font-display text-base font-bold text-primary">{t.materials.addMaterialFormTitle}</h3>

          <input required placeholder={t.materials.titlePlaceholder} value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))} className={inputClass} />

          <div className="inline-flex gap-1 bg-elevated rounded-lg p-1 border border-default">
            {(['link', 'upload'] as const).map(md => (
              <button type="button" key={md} onClick={() => setMode(md)}
                className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors ${mode === md ? 'bg-accent text-white' : 'text-secondary hover:text-primary'}`}>
                {md === 'link' ? t.materials.sourceLink : t.materials.sourceUpload}
              </button>
            ))}
          </div>

          {mode === 'link' ? (
            <input required placeholder={t.materials.urlPlaceholder} value={form.url}
              onChange={e => setForm(f => ({ ...f, url: e.target.value }))} className={inputClass} />
          ) : (
            <div className="space-y-2">
              <label className="flex items-center justify-center gap-2 px-3 py-6 bg-elevated/50 border border-dashed border-strong rounded-xl text-sm text-secondary cursor-pointer hover:text-primary hover:border-accent hover:bg-elevated transition-colors">
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
              <div className="text-xs text-muted">{t.materials.maxHint}</div>
              {uploadMut.isPending && (
                <div className="space-y-1">
                  <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
                    <div className="h-full bg-accent transition-all duration-300" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="text-xs text-muted text-right tabular-nums">{t.materials.uploading} {progress}%</div>
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Select aria-label={t.materials.selectGame} value={form.app_id}
              onChange={v => setForm(f => ({ ...f, app_id: v }))}
              options={[{ value: '', label: t.materials.selectGame },
                ...allGames.map(g => ({ value: g.app_id, label: g.name }))]} />
            <Select value={form.platform}
              onChange={v => setForm(f => ({ ...f, platform: v }))}
              options={[{ value: 'youtube', label: 'YouTube' }, { value: 'tiktok', label: 'TikTok' },
                { value: 'meta', label: 'Meta Ads' }, { value: 'other', label: t.materials.platforms.other }]} />
            <Select value={form.material_type}
              onChange={v => setForm(f => ({ ...f, material_type: v }))}
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
              className="px-5 py-2 bg-accent hover:brightness-110 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-all">
              {uploadMut.isPending ? `${t.materials.uploading} ${progress}%`
                : createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {/* ── 工具条:搜索 + 平台分段 ───────────────────────────── */}
      <div className="reveal reveal-2 mt-5 flex flex-wrap items-center gap-3 p-2 rounded-xl border border-default bg-surface/50">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input type="text" placeholder={t.materials.searchPlaceholder} value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-transparent text-sm text-primary placeholder:text-muted focus:outline-none" />
        </div>
        <div className="flex gap-1">
          {PLATFORM_TABS.map(p => {
            const label = p === ''
              ? t.materials.platforms.all
              : (t.materials.platforms[p as keyof typeof t.materials.platforms] || PLATFORM_CONFIG[p]?.label || p)
            const active = filterPlatform === p
            return (
              <button key={p} onClick={() => setFilterPlatform(p)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${active ? 'bg-accent/15 text-accent ring-1 ring-accent/40' : 'text-secondary hover:text-primary hover:bg-elevated'}`}>
                {label}
              </button>
            )
          })}
        </div>
      </div>

      {/* ── 卡片网格 ─────────────────────────────────────────── */}
      <div className="reveal reveal-3 mt-5">
        {isError ? (
          <QueryError onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="rounded-xl border border-default bg-surface overflow-hidden">
                <div className="aspect-video bg-elevated animate-pulse" />
                <div className="p-4 space-y-2">
                  <div className="h-3 w-1/3 bg-elevated rounded animate-pulse" />
                  <div className="h-4 w-3/4 bg-elevated rounded animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        ) : materials.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="grid place-items-center w-14 h-14 rounded-2xl border border-default bg-surface text-muted mb-4">
              <FilmIcon size={22} />
            </div>
            <p className="text-secondary text-sm">
              {debouncedSearch || filterPlatform ? t.common.noResult : t.materials.empty}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {materials.map(m => {
              const platCfg = (m.platform && PLATFORM_CONFIG[m.platform]) || PLATFORM_CONFIG.other
              const game = gameMap[m.app_id]
              const href = (m.source === 'upload' ? m.stream_url : m.url) as string | undefined
              const hasPreview = m.source === 'upload' && !!m.stream_url
              return (
                <div key={m.id}
                  className="group relative flex flex-col rounded-xl border border-default bg-surface overflow-hidden shadow-card transition-all duration-200 hover:border-strong hover:-translate-y-0.5">
                  {/* 媒体区 */}
                  <div className="relative aspect-video bg-gradient-to-br from-elevated to-surface overflow-hidden">
                    {hasPreview ? (
                      <MaterialPreview m={m} fill />
                    ) : (
                      <div className="absolute inset-0 grid place-items-center">
                        <FilmIcon size={28} className="text-muted/60" />
                      </div>
                    )}
                    <span className={`absolute top-2.5 left-2.5 px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide backdrop-blur-sm bg-base/70 ${platCfg.color}`}>
                      {platCfg.label}
                    </span>
                    {href && (
                      <a href={href} target="_blank" rel="noopener noreferrer" title={t.materials.openFile}
                        className="absolute top-2.5 right-2.5 p-1.5 rounded-md bg-base/70 backdrop-blur-sm text-secondary hover:text-accent opacity-0 group-hover:opacity-100 transition-opacity">
                        <ExternalLink size={14} />
                      </a>
                    )}
                  </div>
                  {/* 信息区 */}
                  <div className="flex flex-col gap-2 p-4">
                    <div className="flex items-center gap-2 text-[11px] text-muted">
                      <span className="uppercase tracking-wide">{typeLabel(m.material_type)}</span>
                      {game && (
                        <>
                          <span className="text-muted">·</span>
                          <button onClick={() => navigate(`/game/${m.app_id}`)}
                            className="text-accent hover:underline truncate max-w-[140px]">
                            {game.name}
                          </button>
                        </>
                      )}
                    </div>
                    <div className="text-sm font-semibold text-primary leading-snug line-clamp-2">{m.title}</div>
                    {m.notes && <div className="text-xs text-muted truncate">{m.notes}</div>}
                    {m.tags?.length > 0 && (
                      <div className="flex gap-1.5 flex-wrap pt-0.5">
                        {m.tags.map((tag: string) => (
                          <span key={tag} className="px-2 py-0.5 rounded-md bg-elevated border border-default text-[11px] text-secondary">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <button onClick={() => deleteMut.mutate(m.id)} aria-label="delete"
                    className="absolute bottom-3 right-3 p-1.5 rounded-md text-muted hover:text-red-400 hover:bg-base/60 opacity-0 group-hover:opacity-100 transition-all">
                    <Trash2 size={14} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="reveal reveal-4 mt-6">
        <Pagination total={total} offset={offset} pageSize={PAGE_SIZE} onOffsetChange={setOffset} />
      </div>
    </div>
  )
}
