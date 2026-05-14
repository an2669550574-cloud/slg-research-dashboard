import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { gamesApi, historyApi, materialsApi } from '../lib/api'
import { formatNumber, formatRevenue, EVENT_TYPE_CONFIG, PLATFORM_CONFIG } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import {
  ArrowLeft, RefreshCw, Plus, Trash2, ExternalLink, Download as DownloadIcon, Loader2
} from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

const EMPTY_EVENT = { event_date: '', event_type: 'version', title: '', description: '' }

const inputClass = "bg-base border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

function eventTypeLabel(t: ReturnType<typeof useT>, kind: string): string {
  return t.events[kind as keyof typeof t.events] || kind
}

function Timeline({ appId }: { appId: string }) {
  const t = useT()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(EMPTY_EVENT)

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['history', appId],
    queryFn: () => historyApi.get(appId),
  })
  const syncMut = useMutation({
    mutationFn: () => historyApi.sync(appId),
    onSuccess: (data: any) => {
      qc.invalidateQueries({ queryKey: ['history', appId] })
      toast.success(data?.message || t.gameDetail.aiSyncedToast)
    },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => historyApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['history', appId] })
      toast.success(t.gameDetail.eventDeletedToast)
    },
  })
  const createMut = useMutation({
    mutationFn: (data: any) => historyApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['history', appId] })
      setShowForm(false)
      setForm(EMPTY_EVENT)
      toast.success(t.gameDetail.eventAddedToast)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMut.mutate({ ...form, app_id: appId, source: 'manual' })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-primary">{t.gameDetail.timelineTitle}</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              if (events.length === 0) { toast.error(t.gameDetail.timelineExportEmpty); return }
              downloadCsv(`timeline-${appId}.csv`, events, [
                { header: t.csv.date, get: (e: any) => e.event_date },
                { header: t.csv.type, get: (e: any) => eventTypeLabel(t, e.event_type) },
                { header: t.csv.title, get: (e: any) => e.title },
                { header: t.csv.description, get: (e: any) => e.description },
                { header: t.csv.source, get: (e: any) => e.source },
              ])
              toast.success(t.common.exported(events.length))
            }}
            className="flex items-center gap-1.5 px-2 py-1.5 text-secondary hover:text-primary text-xs transition-colors"
            title={t.common.export}
          >
            <DownloadIcon size={12} />
          </button>
          <button
            onClick={() => setShowForm(s => !s)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-elevated hover:bg-elevated/70 rounded-lg text-xs text-primary transition-colors"
          >
            <Plus size={12} />
            {t.gameDetail.addEventButton}
          </button>
          <button
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-xs text-white transition-colors"
          >
            {syncMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {t.gameDetail.aiSyncButton}
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-elevated rounded-xl p-4 mb-4 space-y-3 border border-default">
          <div className="grid grid-cols-2 gap-3">
            <input
              required
              type="date"
              value={form.event_date}
              onChange={e => setForm(f => ({ ...f, event_date: e.target.value }))}
              className={inputClass}
            />
            <select
              value={form.event_type}
              onChange={e => setForm(f => ({ ...f, event_type: e.target.value }))}
              className={inputClass}
            >
              {Object.keys(EVENT_TYPE_CONFIG).map(k => (
                <option key={k} value={k}>{eventTypeLabel(t, k)}</option>
              ))}
            </select>
            <input
              required
              placeholder={t.gameDetail.eventTitle}
              value={form.title}
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              className={`col-span-2 ${inputClass}`}
            />
            <textarea
              rows={3}
              placeholder={t.gameDetail.eventDescription}
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              className={`col-span-2 resize-none ${inputClass}`}
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => { setShowForm(false); setForm(EMPTY_EVENT) }}
              className="px-3 py-1.5 text-sm text-secondary hover:text-primary"
            >
              {t.common.cancel}
            </button>
            <button
              type="submit"
              disabled={createMut.isPending}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors"
            >
              {createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex gap-4 animate-pulse">
              <div className="w-16 h-4 bg-elevated rounded shrink-0" />
              <div className="flex-1 space-y-2">
                <div className="w-48 h-4 bg-elevated rounded" />
                <div className="w-full h-3 bg-elevated rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <div className="py-12 text-center">
          <p className="text-muted text-sm mb-3">{t.gameDetail.timelineEmpty}</p>
          <button
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="text-brand-500 text-sm hover:text-brand-400"
          >
            {t.gameDetail.timelineEmptyHint}
          </button>
        </div>
      ) : (
        <div className="relative">
          <div className="absolute left-[5.5rem] top-0 bottom-0 w-px bg-default" />
          <div className="space-y-0">
            {events.map((e: any) => {
              const cfg = EVENT_TYPE_CONFIG[e.event_type] || EVENT_TYPE_CONFIG.version
              return (
                <div key={e.id} className="flex gap-4 group pb-6">
                  <div className="w-20 shrink-0 text-right pt-0.5">
                    <span className="text-xs text-muted">{e.event_date}</span>
                  </div>
                  <div className="relative flex items-start gap-3 flex-1">
                    <div className={`w-2.5 h-2.5 rounded-full mt-1.5 shrink-0 relative z-10 ${cfg.bg}`} />
                    <div className="flex-1 bg-elevated/50 rounded-xl p-3 border border-default hover:border-default transition-colors">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <span className={`text-xs font-medium ${cfg.color} mr-2`}>{eventTypeLabel(t, e.event_type)}</span>
                          <span className="text-sm font-medium text-primary">{e.title}</span>
                        </div>
                        <button
                          onClick={() => deleteMut.mutate(e.id)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-muted hover:text-red-400"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                      {e.description && (
                        <p className="text-xs text-secondary mt-1.5 leading-relaxed">{e.description}</p>
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

function MaterialsPanel({ appId }: { appId: string }) {
  const t = useT()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', url: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })

  const { data: materials = [] } = useQuery({
    queryKey: ['materials', appId],
    queryFn: () => materialsApi.list(appId),
  })
  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials', appId] })
      setShowForm(false)
      setForm({ title: '', url: '', platform: 'youtube', material_type: 'video', tags: '', notes: '' })
      toast.success(t.gameDetail.addedMaterialToast)
    },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials', appId] })
      toast.success(t.gameDetail.deletedMaterialToast)
    },
  })

  const typeLabel = (kind: string) => t.materials.types[kind as keyof typeof t.materials.types] || kind

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMut.mutate({ ...form, app_id: appId, tags: form.tags ? form.tags.split(',').map((s: string) => s.trim()) : [] })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-primary">{t.gameDetail.materialsTitle}</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-elevated hover:bg-elevated/70 rounded-lg text-xs text-primary transition-colors"
        >
          <Plus size={12} />
          {t.materials.addMaterial}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-elevated rounded-xl p-4 mb-4 space-y-3 border border-default">
          <div className="grid grid-cols-2 gap-3">
            <input required placeholder={t.materials.titlePlaceholder} value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              className={`col-span-2 ${inputClass}`} />
            <input required placeholder={t.materials.urlPlaceholder} value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
              className={`col-span-2 ${inputClass}`} />
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
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-secondary hover:text-primary">{t.common.cancel}</button>
            <button type="submit" disabled={createMut.isPending}
              className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">
              {createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {materials.length === 0 ? (
        <div className="py-10 text-center text-muted text-sm">{t.materials.emptyHint}</div>
      ) : (
        <div className="space-y-2">
          {materials.map((m: any) => {
            const platCfg = PLATFORM_CONFIG[m.platform] || PLATFORM_CONFIG.other
            return (
              <div key={m.id} className="group flex items-start gap-3 bg-elevated/50 rounded-xl p-3 border border-default hover:border-default transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={`text-xs font-medium ${platCfg.color}`}>{platCfg.label}</span>
                    <span className="text-xs text-muted">·</span>
                    <span className="text-xs text-muted">{typeLabel(m.material_type)}</span>
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
                <div className="flex items-center gap-2 shrink-0">
                  <a href={m.url} target="_blank" rel="noopener noreferrer"
                    className="p-1.5 text-muted hover:text-brand-400 transition-colors"
                    onClick={e => e.stopPropagation()}>
                    <ExternalLink size={14} />
                  </a>
                  <button onClick={() => deleteMut.mutate(m.id)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-muted hover:text-red-400">
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

type RangeMode = { kind: 'preset'; days: number } | { kind: 'custom'; start: string; end: string }

export default function GameDetail() {
  const { appId } = useParams<{ appId: string }>()
  const navigate = useNavigate()
  const t = useT()
  const [range, setRange] = useState<RangeMode>({ kind: 'preset', days: 30 })
  const [showCustom, setShowCustom] = useState(false)
  const today = new Date().toISOString().slice(0, 10)
  const monthAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10)
  const [customStart, setCustomStart] = useState(monthAgo)
  const [customEnd, setCustomEnd] = useState(today)

  const queryParams = range.kind === 'preset'
    ? { days: range.days, country: 'US', platform: 'ios' }
    : { start_date: range.start, end_date: range.end, country: 'US', platform: 'ios' }

  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: ['metrics', appId, range],
    queryFn: () => gamesApi.metrics(appId!, queryParams),
    enabled: !!appId,
  })

  // 游戏元信息从 games 表读：今日榜单 rankings 只覆盖 Top N，
  // 游戏掉出榜单时详情页头部不应该空白
  const { data: game } = useQuery({
    queryKey: ['games', 'detail', appId],
    queryFn: () => gamesApi.get(appId!),
    enabled: !!appId,
  })

  // 今日榜单数据只用于显示当日 rank/revenue/downloads 三个数字
  const { data: rankings } = useQuery({
    queryKey: ['rankings', 'US', 'ios'],
    queryFn: () => gamesApi.rankings('US', 'ios'),
  })
  const todayStats = rankings?.find((g: any) => g.app_id === appId)

  const chartTooltipStyle = {
    contentStyle: { background: 'rgb(var(--bg-elevated))', border: '1px solid rgb(var(--border-default))', borderRadius: 8 },
    labelStyle: { color: 'rgb(var(--text-primary))' },
  }

  const chartCards = [
    { key: 'revenue' as const, dataKey: 'value', label: t.gameDetail.chartRevenue, color: '#8b5cf6', formatter: (v: any) => formatRevenue(v) },
    { key: 'downloads' as const, dataKey: 'value', label: t.gameDetail.chartDownloads, color: '#10b981', formatter: (v: any) => formatNumber(v) },
    { key: 'rankings' as const, dataKey: 'rank', label: t.gameDetail.chartRanking, color: '#f59e0b', formatter: (v: any) => `#${v}` },
  ]

  return (
    <div className="p-6 space-y-6">
      <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-secondary hover:text-primary transition-colors">
        <ArrowLeft size={16} /> {t.common.back}
      </button>

      {game && (
        <div className="flex items-center gap-4">
          {game.icon_url
            ? <img src={game.icon_url} alt={game.name} className="w-16 h-16 rounded-2xl object-cover" />
            : <div className="w-16 h-16 rounded-2xl bg-elevated" />
          }
          <div>
            <h1 className="text-xl font-bold text-primary">{game.name}</h1>
            <p className="text-muted text-sm mt-0.5">{game.publisher}</p>
            {todayStats && (
              <div className="flex items-center gap-3 mt-2">
                <span className="text-xs text-yellow-400 font-medium">{t.gameDetail.rankPrefix} #{todayStats.rank}</span>
                <span className="text-xs text-emerald-400">{formatRevenue(todayStats.revenue ?? 0)} / {t.gameDetail.today}</span>
                <span className="text-xs text-secondary">{formatNumber(todayStats.downloads ?? 0)} {t.dashboard.downloadsSuffix}</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {[7, 30, 90].map(d => {
          const active = range.kind === 'preset' && range.days === d
          return (
            <button key={d}
              onClick={() => { setRange({ kind: 'preset', days: d }); setShowCustom(false) }}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${active ? 'bg-brand-600 text-white' : 'bg-elevated text-secondary hover:text-primary'}`}>
              {t.common.days(d)}
            </button>
          )
        })}
        <button
          onClick={() => setShowCustom(s => !s)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${range.kind === 'custom' ? 'bg-brand-600 text-white' : 'bg-elevated text-secondary hover:text-primary'}`}
        >
          {t.common.custom}{range.kind === 'custom' ? `: ${range.start} → ${range.end}` : ''}
        </button>
        {showCustom && (
          <div className="flex items-center gap-2 bg-elevated border border-default rounded-lg px-3 py-1.5">
            <input
              type="date"
              value={customStart}
              max={customEnd}
              onChange={e => setCustomStart(e.target.value)}
              className="bg-transparent text-xs text-primary focus:outline-none"
            />
            <span className="text-muted text-xs">→</span>
            <input
              type="date"
              value={customEnd}
              min={customStart}
              max={today}
              onChange={e => setCustomEnd(e.target.value)}
              className="bg-transparent text-xs text-primary focus:outline-none"
            />
            <button
              onClick={() => {
                if (!customStart || !customEnd) { toast.error(t.common.pickRange); return }
                setRange({ kind: 'custom', start: customStart, end: customEnd })
                setShowCustom(false)
              }}
              className="px-2 py-1 bg-brand-600 hover:bg-brand-700 rounded text-xs text-white"
            >
              {t.common.apply}
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-3 gap-4">
        {chartCards.map(({ key, dataKey, label, color, formatter }) => (
          <div key={key} className="bg-surface border border-default rounded-xl p-4">
            <h3 className="text-xs font-medium text-secondary mb-3">{label}</h3>
            {metricsLoading ? (
              <div className="h-28 flex items-center justify-center text-muted text-xs">{t.common.loading}</div>
            ) : (
              <ResponsiveContainer width="100%" height={110}>
                <AreaChart data={metrics?.[key] || []} margin={{ top: 0, right: 0, left: -30, bottom: 0 }}>
                  <defs>
                    <linearGradient id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={color} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border-default))" />
                  <XAxis dataKey="date" tick={false} />
                  <YAxis tick={{ fill: 'rgb(var(--text-muted))', fontSize: 10 }} reversed={key === 'rankings'} />
                  <Tooltip {...chartTooltipStyle} formatter={(v: any) => [formatter(v), label]} />
                  <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2}
                    fill={`url(#grad-${key})`} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-surface border border-default rounded-xl p-5">
          <Timeline appId={appId!} />
        </div>
        <div className="bg-surface border border-default rounded-xl p-5">
          <MaterialsPanel appId={appId!} />
        </div>
      </div>
    </div>
  )
}
