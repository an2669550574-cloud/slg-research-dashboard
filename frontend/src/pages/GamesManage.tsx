import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { gamesApi } from '../lib/api'
import { useT } from '../i18n'
import { Plus, Trash2, Search, Loader2, Check, Pencil } from 'lucide-react'

type LookupResult = {
  name?: string
  publisher?: string
  icon_url?: string
  release_date?: string
  description?: string
}

type FormState = {
  app_id: string
  name: string
  publisher: string
  icon_url: string
  platform: string
  country: string
  release_date: string
  description: string
}

const EMPTY_FORM: FormState = {
  app_id: '',
  name: '',
  publisher: '',
  icon_url: '',
  platform: 'ios',
  country: 'US',
  release_date: '',
  description: '',
}

type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; appId: string }

export default function GamesManage() {
  const t = useT()
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [lookup, setLookup] = useState<LookupResult | null>(null)
  const [search, setSearch] = useState('')

  const isEditing = mode.kind === 'edit'
  const isOpen = mode.kind !== 'closed'

  const { data: games = [], isLoading } = useQuery({
    queryKey: ['games', 'manage'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  const lookupMut = useMutation({
    mutationFn: (appId: string) => gamesApi.lookup(appId),
    onSuccess: (data: LookupResult) => {
      setLookup(data)
      setForm(f => ({
        ...f,
        name: data.name || f.name,
        publisher: data.publisher || f.publisher,
        icon_url: data.icon_url || f.icon_url,
        release_date: data.release_date || f.release_date,
        description: data.description || f.description,
      }))
      toast.success(t.gamesManage.lookupHit)
    },
    onError: () => setLookup(null),
  })

  const createMut = useMutation({
    mutationFn: (data: FormState) => gamesApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['games', 'manage'] })
      qc.invalidateQueries({ queryKey: ['rankings'] })
      closeForm()
      toast.success(t.gamesManage.added)
    },
  })

  const updateMut = useMutation({
    mutationFn: ({ appId, data }: { appId: string; data: Partial<FormState> }) => gamesApi.update(appId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['games', 'manage'] })
      qc.invalidateQueries({ queryKey: ['rankings'] })
      closeForm()
      toast.success(t.gamesManage.updated)
    },
  })

  const deleteMut = useMutation({
    mutationFn: (appId: string) => gamesApi.delete(appId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['games', 'manage'] })
      qc.invalidateQueries({ queryKey: ['rankings'] })
      toast.success(t.gamesManage.deleted)
    },
  })

  const filtered = games.filter((g: any) => {
    if (!search) return true
    const s = search.toLowerCase()
    return g.name?.toLowerCase().includes(s)
      || g.publisher?.toLowerCase().includes(s)
      || g.app_id?.toLowerCase().includes(s)
  })

  function closeForm() {
    setMode({ kind: 'closed' })
    setForm(EMPTY_FORM)
    setLookup(null)
  }

  function openCreate() {
    setMode({ kind: 'create' })
    setForm(EMPTY_FORM)
    setLookup(null)
  }

  function openEdit(g: any) {
    setMode({ kind: 'edit', appId: g.app_id })
    setForm({
      app_id: g.app_id,
      name: g.name || '',
      publisher: g.publisher || '',
      icon_url: g.icon_url || '',
      platform: g.platform || 'ios',
      country: g.country || 'US',
      release_date: g.release_date || '',
      description: g.description || '',
    })
    setLookup(null)
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (mode.kind === 'create') {
      if (!form.app_id) { toast.error(t.gamesManage.appIdRequired); return }
      createMut.mutate(form)
    } else if (mode.kind === 'edit') {
      // 不传 app_id（不可改），只传可更新字段
      const { app_id: _appId, ...rest } = form
      updateMut.mutate({ appId: mode.appId, data: rest })
    }
  }

  const handleDelete = (game: any) => {
    if (!confirm(t.gamesManage.confirmDelete(game.name || game.app_id))) return
    deleteMut.mutate(game.app_id)
  }

  const submitting = createMut.isPending || updateMut.isPending

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">{t.gamesManage.title}</h1>
          <p className="text-gray-500 text-sm mt-0.5">{t.gamesManage.subtitle}</p>
        </div>
        <button
          onClick={() => isOpen ? closeForm() : openCreate()}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 rounded-lg text-sm text-white transition-colors"
        >
          <Plus size={14} />
          {t.gamesManage.addGame}
        </button>
      </div>

      {isOpen && (
        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-700 rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">
            {isEditing ? t.gamesManage.editGameFormTitle : t.gamesManage.addGameFormTitle}
          </h3>

          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">{t.gamesManage.appIdLabel}</label>
              <input
                required
                disabled={isEditing}
                placeholder={t.gamesManage.appIdPlaceholder}
                value={form.app_id}
                onChange={e => { setForm(f => ({ ...f, app_id: e.target.value })); setLookup(null) }}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500 disabled:opacity-60 disabled:cursor-not-allowed"
              />
            </div>
            <button
              type="button"
              disabled={!form.app_id || lookupMut.isPending}
              onClick={() => lookupMut.mutate(form.app_id)}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors"
            >
              {lookupMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              {t.gamesManage.lookup}
            </button>
          </div>

          {lookup && (
            <div className="flex items-center gap-3 bg-gray-800 border border-emerald-700/40 rounded-lg p-3">
              <Check size={16} className="text-emerald-400 shrink-0" />
              {lookup.icon_url && (
                <img src={lookup.icon_url} alt="" className="w-10 h-10 rounded-lg object-cover" />
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-white truncate">{lookup.name}</div>
                <div className="text-xs text-gray-400 truncate">
                  {lookup.publisher} {lookup.release_date && `· ${lookup.release_date}`}
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <input placeholder={t.gamesManage.namePlaceholder} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              className="col-span-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input placeholder={t.gamesManage.publisherPlaceholder} value={form.publisher} onChange={e => setForm(f => ({ ...f, publisher: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input placeholder={t.gamesManage.iconUrlPlaceholder} value={form.icon_url} onChange={e => setForm(f => ({ ...f, icon_url: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <select value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="ios">iOS</option>
              <option value="android">Android</option>
            </select>
            <input placeholder={t.gamesManage.countryPlaceholder} value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value.toUpperCase() }))}
              maxLength={2}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input type="date" value={form.release_date} onChange={e => setForm(f => ({ ...f, release_date: e.target.value }))}
              className="col-span-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500" />
            <textarea rows={2} placeholder={t.gamesManage.descriptionPlaceholder} value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              className="col-span-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500 resize-none" />
          </div>

          {!isEditing && <p className="text-xs text-gray-500">{t.gamesManage.autoFillHint}</p>}

          <div className="flex justify-end gap-2">
            <button type="button" onClick={closeForm}
              className="px-3 py-1.5 text-sm text-gray-400 hover:text-white">{t.common.cancel}</button>
            <button type="submit" disabled={submitting}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {submitting ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      <div className="relative max-w-sm">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          type="text"
          placeholder={t.gamesManage.searchPlaceholder}
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500"
        />
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-5 py-3 text-left">{t.rankings.game}</th>
              <th className="px-3 py-3 text-left">App ID</th>
              <th className="px-3 py-3 text-left">{t.gamesManage.platformCol}</th>
              <th className="px-3 py-3 text-left">{t.gamesManage.releaseDateCol}</th>
              <th className="px-3 py-3 text-right w-24"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {isLoading ? (
              <tr><td colSpan={5} className="px-5 py-12 text-center text-gray-600 text-sm">{t.common.loading}</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={5} className="px-5 py-12 text-center text-gray-600 text-sm">{t.gamesManage.empty}</td></tr>
            ) : filtered.map((g: any) => (
              <tr key={g.app_id} className="hover:bg-gray-800/50 transition-colors">
                <td className="px-5 py-3">
                  <div className="flex items-center gap-3">
                    {g.icon_url
                      ? <img src={g.icon_url} alt={g.name} className="w-9 h-9 rounded-lg object-cover" />
                      : <div className="w-9 h-9 rounded-lg bg-gray-700 flex items-center justify-center text-gray-400 text-xs">?</div>
                    }
                    <div>
                      <div className="text-sm font-medium text-white">{g.name}</div>
                      <div className="text-xs text-gray-500">{g.publisher}</div>
                    </div>
                  </div>
                </td>
                <td className="px-3 py-3 text-xs text-gray-400 font-mono">{g.app_id}</td>
                <td className="px-3 py-3 text-xs text-gray-400">{g.platform}</td>
                <td className="px-3 py-3 text-xs text-gray-400">{g.release_date || '—'}</td>
                <td className="px-3 py-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => openEdit(g)}
                      className="p-1.5 text-gray-500 hover:text-brand-400 transition-colors"
                      title={t.common.edit}
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      onClick={() => handleDelete(g)}
                      disabled={deleteMut.isPending}
                      className="p-1.5 text-gray-600 hover:text-red-400 transition-colors"
                      title={t.common.delete}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
