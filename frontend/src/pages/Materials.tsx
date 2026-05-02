import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { materialsApi, gamesApi } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { ExternalLink, Trash2, Plus, Search, Filter } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

export default function Materials() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterPlatform, setFilterPlatform] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })

  const { data: materials = [], isLoading } = useQuery({
    queryKey: ['materials'],
    queryFn: () => materialsApi.list(),
  })
  const { data: rankings = [] } = useQuery({
    queryKey: ['rankings'],
    queryFn: () => gamesApi.rankings(),
  })

  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['materials'] }); setShowForm(false); setForm({ title: '', url: '', app_id: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' }) },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['materials'] }),
  })

  const filtered = materials.filter((m: any) => {
    const matchSearch = !search || m.title.toLowerCase().includes(search.toLowerCase())
    const matchPlatform = !filterPlatform || m.platform === filterPlatform
    return matchSearch && matchPlatform
  })

  const gameMap = Object.fromEntries(rankings.map((g: any) => [g.app_id, g]))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMut.mutate({ ...form, tags: form.tags ? form.tags.split(',').map((t: string) => t.trim()) : [] })
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">素材库</h1>
          <p className="text-gray-500 text-sm mt-0.5">全部游戏的优质广告素材链接汇总</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 rounded-lg text-sm text-white transition-colors"
        >
          <Plus size={14} />
          添加素材
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-700 rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-semibold text-white mb-3">添加新素材</h3>
          <div className="grid grid-cols-2 gap-3">
            <input required placeholder="素材标题 *" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              className="col-span-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input required placeholder="链接 URL *" value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
              className="col-span-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <select value={form.app_id} onChange={e => setForm(f => ({ ...f, app_id: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="">选择关联游戏（可选）</option>
              {rankings.map((g: any) => <option key={g.app_id} value={g.app_id}>{g.name}</option>)}
            </select>
            <select value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="youtube">YouTube</option>
              <option value="tiktok">TikTok</option>
              <option value="meta">Meta Ads</option>
              <option value="other">其他</option>
            </select>
            <select value={form.material_type} onChange={e => setForm(f => ({ ...f, material_type: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="video">视频</option>
              <option value="image">图片</option>
              <option value="playable">试玩广告</option>
            </select>
            <input placeholder="标签（逗号分隔）" value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input placeholder="备注" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-400 hover:text-white">取消</button>
            <button type="submit" disabled={createMut.isPending}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {createMut.isPending ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      )}

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder="搜索素材标题..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500"
          />
        </div>
        <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
          {['', 'youtube', 'tiktok', 'meta', 'other'].map(p => (
            <button key={p} onClick={() => setFilterPlatform(p)}
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${filterPlatform === p ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'}`}>
              {p === '' ? '全部' : PLATFORM_CONFIG[p]?.label || p}
            </button>
          ))}
        </div>
      </div>

      <div className="text-xs text-gray-600">{filtered.length} 条素材</div>

      {isLoading ? (
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-24 bg-gray-900 rounded-xl animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-20 text-center text-gray-600 text-sm">暂无素材</div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {filtered.map((m: any) => {
            const platCfg = PLATFORM_CONFIG[m.platform] || PLATFORM_CONFIG.other
            const game = gameMap[m.app_id]
            return (
              <div key={m.id} className="group bg-gray-900 border border-gray-800 hover:border-gray-700 rounded-xl p-4 transition-colors">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs font-medium ${platCfg.color}`}>{platCfg.label}</span>
                      <span className="text-xs text-gray-600">·</span>
                      <span className="text-xs text-gray-500">{m.material_type === 'video' ? '视频' : m.material_type === 'image' ? '图片' : '试玩广告'}</span>
                      {game && (
                        <>
                          <span className="text-xs text-gray-600">·</span>
                          <button
                            onClick={() => navigate(`/game/${m.app_id}`)}
                            className="text-xs text-brand-500 hover:text-brand-400 truncate max-w-[100px]"
                          >
                            {game.name}
                          </button>
                        </>
                      )}
                    </div>
                    <div className="text-sm font-medium text-white truncate">{m.title}</div>
                    {m.notes && <div className="text-xs text-gray-500 mt-0.5 truncate">{m.notes}</div>}
                    {m.tags?.length > 0 && (
                      <div className="flex gap-1 mt-1.5 flex-wrap">
                        {m.tags.map((t: string) => (
                          <span key={t} className="px-1.5 py-0.5 bg-gray-800 rounded text-xs text-gray-400">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <a href={m.url} target="_blank" rel="noopener noreferrer"
                      className="p-1.5 text-gray-500 hover:text-brand-400 transition-colors">
                      <ExternalLink size={14} />
                    </a>
                    <button onClick={() => deleteMut.mutate(m.id)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-gray-600 hover:text-red-400">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
