import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { gamesApi, historyApi, materialsApi } from '../lib/api'
import { formatNumber, formatRevenue, EVENT_TYPE_CONFIG, PLATFORM_CONFIG } from '../lib/utils'
import {
  ArrowLeft, RefreshCw, Plus, Trash2, ExternalLink,
  ChevronDown, ChevronUp, Loader2
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, LineChart, Line
} from 'recharts'

// ── Timeline ──────────────────────────────────────────────────────────────────
function Timeline({ appId }: { appId: string }) {
  const qc = useQueryClient()
  const { data: events = [], isLoading } = useQuery({
    queryKey: ['history', appId],
    queryFn: () => historyApi.get(appId),
  })
  const syncMut = useMutation({
    mutationFn: () => historyApi.sync(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['history', appId] }),
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => historyApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['history', appId] }),
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-300">发展历程</h2>
        <button
          onClick={() => syncMut.mutate()}
          disabled={syncMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-xs text-white transition-colors"
        >
          {syncMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          AI 自动同步
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex gap-4 animate-pulse">
              <div className="w-16 h-4 bg-gray-800 rounded shrink-0" />
              <div className="flex-1 space-y-2">
                <div className="w-48 h-4 bg-gray-800 rounded" />
                <div className="w-full h-3 bg-gray-800 rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <div className="py-12 text-center">
          <p className="text-gray-600 text-sm mb-3">暂无历程数据</p>
          <button
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="text-brand-500 text-sm hover:text-brand-400"
          >
            点击 AI 自动同步 →
          </button>
        </div>
      ) : (
        <div className="relative">
          <div className="absolute left-[5.5rem] top-0 bottom-0 w-px bg-gray-800" />
          <div className="space-y-0">
            {events.map((e: any, i: number) => {
              const cfg = EVENT_TYPE_CONFIG[e.event_type] || EVENT_TYPE_CONFIG.version
              return (
                <div key={e.id} className="flex gap-4 group pb-6">
                  <div className="w-20 shrink-0 text-right pt-0.5">
                    <span className="text-xs text-gray-500">{e.event_date}</span>
                  </div>
                  <div className="relative flex items-start gap-3 flex-1">
                    <div className={`w-2.5 h-2.5 rounded-full mt-1.5 shrink-0 relative z-10 ${cfg.bg}`} />
                    <div className="flex-1 bg-gray-800/50 rounded-xl p-3 border border-gray-800 hover:border-gray-700 transition-colors">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <span className={`text-xs font-medium ${cfg.color} mr-2`}>{cfg.label}</span>
                          <span className="text-sm font-medium text-white">{e.title}</span>
                        </div>
                        <button
                          onClick={() => deleteMut.mutate(e.id)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-600 hover:text-red-400"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                      {e.description && (
                        <p className="text-xs text-gray-400 mt-1.5 leading-relaxed">{e.description}</p>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Materials ─────────────────────────────────────────────────────────────────
function MaterialsPanel({ appId }: { appId: string }) {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', url: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })

  const { data: materials = [] } = useQuery({
    queryKey: ['materials', appId],
    queryFn: () => materialsApi.list(appId),
  })
  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['materials', appId] }); setShowForm(false); setForm({ title: '', url: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' }) },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['materials', appId] }),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMut.mutate({ ...form, app_id: appId, tags: form.tags ? form.tags.split(',').map(t => t.trim()) : [] })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-300">素材库</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded-lg text-xs text-white transition-colors"
        >
          <Plus size={12} />
          添加素材
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-gray-800 rounded-xl p-4 mb-4 space-y-3 border border-gray-700">
          <div className="grid grid-cols-2 gap-3">
            <input required placeholder="素材标题 *" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              className="col-span-2 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input required placeholder="链接 URL *" value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
              className="col-span-2 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <select value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
              className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="youtube">YouTube</option>
              <option value="tiktok">TikTok</option>
              <option value="meta">Meta Ads</option>
              <option value="other">其他</option>
            </select>
            <select value={form.material_type} onChange={e => setForm(f => ({ ...f, material_type: e.target.value }))}
              className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500">
              <option value="video">视频</option>
              <option value="image">图片</option>
              <option value="playable">试玩广告</option>
            </select>
            <input placeholder="标签（逗号分隔）" value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))}
              className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
            <input placeholder="备注" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500" />
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-400 hover:text-white">取消</button>
            <button type="submit" disabled={createMut.isPending}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {createMut.isPending ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      )}

      {materials.length === 0 ? (
        <div className="py-10 text-center text-gray-600 text-sm">暂无素材，点击"添加素材"开始收集</div>
      ) : (
        <div className="space-y-2">
          {materials.map((m: any) => {
            const platCfg = PLATFORM_CONFIG[m.platform] || PLATFORM_CONFIG.other
            return (
              <div key={m.id} className="group flex items-start gap-3 bg-gray-800/50 rounded-xl p-3 border border-gray-800 hover:border-gray-700 transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={`text-xs font-medium ${platCfg.color}`}>{platCfg.label}</span>
                    <span className="text-xs text-gray-600">·</span>
                    <span className="text-xs text-gray-500">{m.material_type === 'video' ? '视频' : m.material_type === 'image' ? '图片' : '试玩广告'}</span>
                  </div>
                  <div className="text-sm font-medium text-white truncate">{m.title}</div>
                  {m.notes && <div className="text-xs text-gray-500 mt-0.5 truncate">{m.notes}</div>}
                  {m.tags?.length > 0 && (
                    <div className="flex gap-1 mt-1.5 flex-wrap">
                      {m.tags.map((t: string) => (
                        <span key={t} className="px-1.5 py-0.5 bg-gray-700 rounded text-xs text-gray-400">{t}</span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <a href={m.url} target="_blank" rel="noopener noreferrer"
                    className="p-1.5 text-gray-500 hover:text-brand-400 transition-colors"
                    onClick={e => e.stopPropagation()}>
                    <ExternalLink size={14} />
                  </a>
                  <button onClick={() => deleteMut.mutate(m.id)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-gray-600 hover:text-red-400">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function GameDetail() {
  const { appId } = useParams<{ appId: string }>()
  const navigate = useNavigate()
  const [days, setDays] = useState(30)

  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: ['metrics', appId, days],
    queryFn: () => gamesApi.metrics(appId!, days),
    enabled: !!appId,
  })

  const { data: rankings } = useQuery({
    queryKey: ['rankings'],
    queryFn: () => gamesApi.rankings(),
  })

  const game = rankings?.find((g: any) => g.app_id === appId)

  const chartTooltipStyle = {
    contentStyle: { background: '#111827', border: '1px solid #374151', borderRadius: 8 },
    labelStyle: { color: '#f9fafb' },
  }

  return (
    <div className="p-6 space-y-6">
      <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors">
        <ArrowLeft size={16} /> 返回
      </button>

      {game && (
        <div className="flex items-center gap-4">
          {game.icon_url
            ? <img src={game.icon_url} alt={game.name} className="w-16 h-16 rounded-2xl object-cover" />
            : <div className="w-16 h-16 rounded-2xl bg-gray-700" />
          }
          <div>
            <h1 className="text-xl font-bold text-white">{game.name}</h1>
            <p className="text-gray-500 text-sm mt-0.5">{game.publisher}</p>
            <div className="flex items-center gap-3 mt-2">
              <span className="text-xs text-yellow-400 font-medium">排名 #{game.rank}</span>
              <span className="text-xs text-emerald-400">{formatRevenue(game.revenue)} / 今日</span>
              <span className="text-xs text-gray-400">{formatNumber(game.downloads)} 下载</span>
            </div>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        {[7, 30, 90].map(d => (
          <button key={d} onClick={() => setDays(d)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${days === d ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'}`}>
            {d} 天
          </button>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { key: 'revenue', label: '收入趋势（$）', color: '#8b5cf6', formatter: (v: any) => formatRevenue(v) },
          { key: 'downloads', label: '下载量趋势', color: '#10b981', formatter: (v: any) => formatNumber(v) },
          { key: 'rankings', label: '排名趋势（越低越好）', color: '#f59e0b', formatter: (v: any) => `#${v}` },
        ].map(({ key, label, color, formatter }) => (
          <div key={key} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <h3 className="text-xs font-medium text-gray-400 mb-3">{label}</h3>
            {metricsLoading ? (
              <div className="h-28 flex items-center justify-center text-gray-600 text-xs">加载中...</div>
            ) : (
              <ResponsiveContainer width="100%" height={110}>
                <AreaChart data={metrics?.[key] || []} margin={{ top: 0, right: 0, left: -30, bottom: 0 }}>
                  <defs>
                    <linearGradient id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={color} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="date" tick={false} />
                  <YAxis tick={{ fill: '#4b5563', fontSize: 10 }} reversed={key === 'rankings'} />
                  <Tooltip {...chartTooltipStyle} formatter={(v: any) => [formatter(v), label]} />
                  <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2}
                    fill={`url(#grad-${key})`} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <Timeline appId={appId!} />
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <MaterialsPanel appId={appId!} />
        </div>
      </div>
    </div>
  )
}
