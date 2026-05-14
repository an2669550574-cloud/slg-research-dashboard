import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { materialsApi, gamesApi } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { ExternalLink, Trash2, Plus, Search, Download as DownloadIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Pagination } from '../components/Pagination'
import { useDebouncedValue } from '../lib/hooks'
import type { MaterialOut } from '../lib/types'

const PAGE_SIZE = 12

export default function Materials() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterPlatform, setFilterPlatform] = useState('')
  const [offset, setOffset] = useState(0)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
  const debouncedSearch = useDebouncedValue(search)

  // 任一筛选条件变化都回到第一页
  useEffect(() => { setOffset(0) }, [debouncedSearch, filterPlatform])

  const { data: paged, isLoading } = useQuery({
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

  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials'] })
      setShowForm(false)
      setForm({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
      toast.success(t.materials.addedToast)
    },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials'] })
      toast.success(t.materials.deletedToast)
    },
  })

  const gameMap = Object.fromEntries(allGames.map(g => [g.app_id, g]))

  const typeLabel = (kind: string) => t.materials.types[kind as keyof typeof t.materials.types] || kind

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMut.mutate({ ...form, tags: form.tags ? form.tags.split(',').map((t: string) => t.trim()) : [] })
  }

  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">{t.materials.title}</h1>
          <p className="text-muted text-sm mt-0.5">{t.materials.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              // 导出整套匹配结果（不只当前页）。limit=200 是后端硬上限；
              // 实际素材库一般 <100 条，超出时会截断并提示用户。
              const all = await materialsApi.listPaged({
                limit: 200,
                offset: 0,
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
                { header: t.csv.url, get: (m: MaterialOut) => m.url },
                { header: t.csv.tags, get: (m: MaterialOut) => m.tags.join(';') },
                { header: t.csv.notes, get: (m: MaterialOut) => m.notes ?? '' },
                { header: t.csv.createdAt, get: (m: MaterialOut) => m.created_at },
              ])
              toast.success(t.common.exported(all.items.length))
            }}
            className="flex items-center gap-2 px-3 py-2 bg-elevated hover:bg-elevated/70 rounded-lg text-sm text-primary transition-colors"
          >
            <DownloadIcon size={14} />
            {t.common.export}
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 rounded-lg text-sm text-white transition-colors"
          >
            <Plus size={14} />
            {t.materials.addMaterial}
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-surface border border-default rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-semibold text-primary mb-3">{t.materials.addMaterialFormTitle}</h3>
          <div className="grid grid-cols-2 gap-3">
            <input required placeholder={t.materials.titlePlaceholder} value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              className={`col-span-2 ${inputClass}`} />
            <input required placeholder={t.materials.urlPlaceholder} value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
              className={`col-span-2 ${inputClass}`} />
            <select value={form.app_id} onChange={e => setForm(f => ({ ...f, app_id: e.target.value }))} className={inputClass}>
              <option value="">{t.materials.selectGame}</option>
              {allGames.map((g: any) => <option key={g.app_id} value={g.app_id}>{g.name}</option>)}
            </select>
            <select value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))} className={inputClass}>
              <option value="youtube">YouTube</option>
              <option value="tiktok">TikTok</option>
              <option value="meta">Meta Ads</option>
              <option value="other">{t.materials.platforms.other}</option>
            </select>
            <select value={form.material_type} onChange={e => setForm(f => ({ ...f, material_type: e.target.value }))} className={inputClass}>
              <option value="video">{t.materials.types.video}</option>
              <option value="image">{t.materials.types.image}</option>
              <option value="playable">{t.materials.types.playable}</option>
            </select>
            <input placeholder={t.materials.tagsPlaceholder} value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} className={inputClass} />
            <input placeholder={t.materials.notesPlaceholder} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} className={inputClass} />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-secondary hover:text-primary">{t.common.cancel}</button>
            <button type="submit" disabled={createMut.isPending}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder={t.materials.searchPlaceholder}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className={`w-full pl-9 pr-3 py-2 ${inputClass}`}
          />
        </div>
        <div className="flex gap-1 bg-elevated rounded-lg p-1">
          {['', 'youtube', 'tiktok', 'meta', 'other'].map(p => {
            const label = p === ''
              ? t.materials.platforms.all
              : (t.materials.platforms[p as keyof typeof t.materials.platforms] || PLATFORM_CONFIG[p]?.label || p)
            return (
              <button key={p} onClick={() => setFilterPlatform(p)}
                className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${filterPlatform === p ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}>
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="text-xs text-muted">{total} {t.materials.countSuffix}</div>

      {isLoading ? (
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-24 bg-surface rounded-xl animate-pulse" />
          ))}
        </div>
      ) : materials.length === 0 ? (
        <div className="py-20 text-center text-muted text-sm">
          {debouncedSearch || filterPlatform ? t.common.noResult : t.materials.empty}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {materials.map(m => {
            const platCfg = (m.platform && PLATFORM_CONFIG[m.platform]) || PLATFORM_CONFIG.other
            const game = gameMap[m.app_id]
            return (
              <div key={m.id} className="group bg-surface border border-default hover:border-default rounded-xl p-4 transition-colors">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs font-medium ${platCfg.color}`}>{platCfg.label}</span>
                      <span className="text-xs text-muted">·</span>
                      <span className="text-xs text-muted">{typeLabel(m.material_type)}</span>
                      {game && (
                        <>
                          <span className="text-xs text-muted">·</span>
                          <button
                            onClick={() => navigate(`/game/${m.app_id}`)}
                            className="text-xs text-brand-500 hover:text-brand-400 truncate max-w-[100px]"
                          >
                            {game.name}
                          </button>
                        </>
                      )}
                    </div>
                    <div className="text-sm font-medium text-primary truncate">{m.title}</div>
                    {m.notes && <div className="text-xs text-muted mt-0.5 truncate">{m.notes}</div>}
                    {m.tags?.length > 0 && (
                      <div className="flex gap-1 mt-1.5 flex-wrap">
                        {m.tags.map((tag: string) => (
                          <span key={tag} className="px-1.5 py-0.5 bg-elevated rounded text-xs text-secondary">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <a href={m.url} target="_blank" rel="noopener noreferrer"
                      className="p-1.5 text-muted hover:text-brand-400 transition-colors">
                      <ExternalLink size={14} />
                    </a>
                    <button onClick={() => deleteMut.mutate(m.id)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-muted hover:text-red-400">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <Pagination total={total} offset={offset} pageSize={PAGE_SIZE} onOffsetChange={setOffset} />
    </div>
  )
}
