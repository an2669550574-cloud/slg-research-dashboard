import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { newcomersApi, publishersApi } from '../lib/api'
import { formatRevenue, formatNumber } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Download as DownloadIcon, Sparkles, Info, FilePlus2, Globe2, Building2, Store, RefreshCw, Star, X, ExternalLink, Repeat, Clock, Ban, ChevronDown } from 'lucide-react'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { WechatAccountsPanel } from '../components/WechatAccountsPanel'
import { useLocalStorageState } from '../lib/hooks'
import type { NewcomerHistoryItem, PublisherNewcomersOut } from '../lib/types'
import { groupByApp, groupPublisherByApp, type GroupedNewcomer } from '../lib/newcomerGrouping'

export default function NewReleases() {
  const t = useT()
  const qc = useQueryClient()
  // 全市场新面孔（检出历史，跨市场合并）/ 厂商新品（已建档主体 × 任意名次首次出现）
  const [view, setView] = useState<'market' | 'publisher'>('market')
  // digest 深链：?focus=<app_id>&view=<market|publisher> 进页定位高亮该卡（A4）。
  // 用 mount effect 从 URL 同步（避开 lazy 路由 + Suspense 下 useState 惰性初始化的取值竞态）。
  const [focusId, setFocusId] = useState<string | null>(null)
  // 历史视图筛选：默认全市场全平台合并（卡片自带 combo 徽标），Top100 / 90 天。
  const [mktPlatform, setMktPlatform] = useLocalStorageState<'all' | Platform>('slg.nc.platform', 'all')
  const [mktCountry, setMktCountry] = useLocalStorageState<'all' | Country>('slg.nc.country', 'all')
  const [topn, setTopn] = useLocalStorageState<50 | 100>('slg.nc.topn', 100)
  const [days, setDays] = useLocalStorageState<30 | 90>('slg.nc.days', 90)
  // 信号筛选：真首发(默认) / 回归 / 全部。PR #93 把回归识别出来后默认隐藏，
  // 回归独立 tab 给运营回看「老游戏卷土重来」的情报信号。
  const [signal, setSignal] = useLocalStorageState<'true_new' | 'reentry' | 'all'>('slg.nc.signal', 'true_new')
  // 榜类型筛选：收入榜(默认) / 下载榜 / 全部（ADR 0001）。下载榜按装机速度抓更早期新品。
  const [chart, setChart] = useLocalStorageState<'grossing' | 'free' | 'all'>('slg.nc.chart', 'grossing')
  const [selected, setSelected] = useState<GroupedNewcomer | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['newcomerHistory', mktCountry, mktPlatform, topn, days, signal, chart],
    queryFn: () => newcomersApi.history({
      days,
      topn: topn === 100 ? undefined : topn,
      country: mktCountry === 'all' ? undefined : mktCountry,
      platform: mktPlatform === 'all' ? undefined : mktPlatform,
      signal,
      chart,
    }),
    enabled: view === 'market',
  })
  const pubQuery = useQuery({
    queryKey: ['publisherNewcomers'],
    queryFn: () => newcomersApi.publishers(),
    enabled: view === 'publisher',
  })

  // 一键建档：把"新厂商待识别"的新面孔转成待调研厂商主体（钉住该 app_id，建档后即识别为 SLG）。
  // 复用 POST /publishers/（支持建档时带 app_ids）——零新接口、零迁移。A↔B 闭环。
  const triageMut = useMutation({
    mutationFn: (g: NewcomerHistoryItem) => publishersApi.create({
      name: g.publisher?.trim() || g.name,
      is_slg: true,
      brief: t.newcomers.triageBrief(g.name, `${g.country}/${g.platform}`),
      app_ids: [{ app_id: g.app_id, note: g.name }],
    }),
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ['newcomers'] })
      qc.invalidateQueries({ queryKey: ['newcomerHistory'] })
      qc.invalidateQueries({ queryKey: ['publishers'] })
      toast.success(t.newcomers.triaged(e.name))
    },
  })
  const handleTriage = (g: NewcomerHistoryItem) => {
    if (!window.confirm(t.newcomers.triageConfirm(g.publisher?.trim() || g.name))) return
    triageMut.mutate(g)
  }

  // 一键忽略：把确认非 SLG 的噪声新品写入 publisher_ignores（与缺口卡同一名单）。
  // 有发行商名 → publisher 粒度（corp_squash 归一，覆盖该厂全部新品）；无名退回 app_id 粒度。
  // /history 读时按忽略名单过滤，故 invalidate 后该行立即消失。A↔B 双动作闭环。
  const ignoreMut = useMutation({
    mutationFn: ({ g, scope }: { g: NewcomerHistoryItem; scope: 'publisher' | 'app_id' }) => {
      const pub = g.publisher?.trim()
      return publishersApi.addIgnore(scope === 'publisher' && pub
        ? { kind: 'publisher', raw_value: pub, label: pub, note: t.newcomers.ignoreNote }
        : { kind: 'app_id', raw_value: g.app_id, label: g.name, note: t.newcomers.ignoreNote })
    },
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ['newcomerHistory'] })
      qc.invalidateQueries({ queryKey: ['publishers'] })
      toast.success(t.newcomers.ignored(row.label || row.value))
    },
  })
  const handleIgnore = (g: NewcomerHistoryItem, scope: 'publisher' | 'app_id') => {
    const pub = g.publisher?.trim()
    const msg = scope === 'publisher' && pub
      ? t.newcomers.ignoreConfirm(pub)
      : t.newcomers.ignoreConfirmApp(g.name)
    if (!window.confirm(msg)) return
    ignoreMut.mutate({ g, scope })
  }

  // CSV 仍导逐市场全量行（不丢粒度）；卡片按 app_id 跨市场合并展示。
  const items = data?.items ?? []
  const groups = useMemo(() => groupByApp(items), [items])
  // 厂商新品视角的去重后计数（表格合并在子组件内，这里只为表头计数与之一致）。
  const pubGroupCount = useMemo(
    () => groupPublisherByApp(pubQuery.data?.items ?? []).length,
    [pubQuery.data],
  )

  // digest 深链：mount 时一次性从 URL 读 focus/view。focusId 不主动清除——高亮靠 CSS
  // focus-flash 的 box-shadow 自行淡出（animation forwards），focusId 仅作滚动定位锚点。
  // （试过用 setTimeout 清 focusId，在 StrictMode 下会过早把高亮抹掉，遂改纯 CSS 淡出。）
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search)
    if (sp.get('view') === 'publisher') setView('publisher')
    const f = sp.get('focus')
    if (f) setFocusId(f)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 深链定位：数据渲染后滚动到该卡一次（scrolledRef 防重复）。用 instant 滚动并重试——
  // 滚动容器 <main> 首帧可能还没布局完（clientHeight=0），scrollIntoView 此时是空操作；
  // 故轮询到容器有高度再滚，最多 ~1.5s。smooth 会被频繁重渲染打断，用 instant。
  const scrolledRef = useRef(false)
  useEffect(() => {
    if (!focusId) { scrolledRef.current = false; return }
    if (scrolledRef.current) return
    const loading = view === 'market' ? isLoading : pubQuery.isLoading
    if (loading) return
    let tries = 0
    let timer = 0
    const attempt = () => {
      const el = document.querySelector<HTMLElement>(`[data-app-id="${CSS.escape(focusId)}"]`)
      const scroller = el?.closest<HTMLElement>('.overflow-y-auto')
      if (el && scroller && scroller.clientHeight > 0) {
        el.scrollIntoView({ block: 'center' })
        scrolledRef.current = true
        return
      }
      if (tries++ < 15) timer = window.setTimeout(attempt, 100)
    }
    attempt()
    return () => window.clearTimeout(timer)
  }, [focusId, view, isLoading, pubQuery.isLoading])

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="New Releases" title={t.newcomers.title} subtitle={t.newcomers.subtitle}>
        <button
          onClick={() => {
            const date = new Date().toISOString().slice(0, 10)
            if (view === 'publisher') {
              const pubItems = pubQuery.data?.items ?? []
              if (pubItems.length === 0) { toast.error(t.common.noExportData); return }
              downloadCsv(`publisher-newcomers-${date}.csv`, pubItems, [
                { header: t.newcomers.entityCol, get: r => r.entity_name },
                { header: t.csv.appId, get: r => r.app_id },
                { header: t.csv.gameName, get: r => r.name },
                { header: t.csv.publisher, get: r => r.publisher },
                { header: t.newcomers.marketCol, get: r => `${r.country}/${r.platform}` },
                { header: t.newcomers.rank, get: r => r.rank },
                { header: t.csv.revenueUsd, get: r => r.revenue },
                { header: t.csv.date, get: r => r.as_of },
              ])
              toast.success(t.common.exported(pubItems.length))
              return
            }
            if (items.length === 0) { toast.error(t.common.noExportData); return }
            downloadCsv(`newcomers-${date}.csv`, items, [
              { header: t.newcomers.marketCol, get: r => `${r.country}/${r.platform}` },
              { header: t.newcomers.rank, get: r => r.rank },
              { header: t.csv.appId, get: r => r.app_id },
              { header: t.csv.gameName, get: r => r.name },
              { header: t.csv.publisher, get: r => r.publisher },
              { header: t.csv.revenueUsd, get: r => r.revenue },
              { header: t.newcomers.csvSlg, get: r => (r.is_slg ? t.newcomers.slgKnown : t.newcomers.slgUnknown) },
              { header: t.csv.date, get: r => r.as_of },
            ])
            toast.success(t.common.exported(items.length))
          }}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
        >
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
      </PageHeader>

      {/* 判定口径 + 数据截至 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 font-data text-[11px] text-muted">
        {view === 'market' ? (
          <>
            <span>{t.newcomers.historyHint(days, topn)}</span>
            {!isLoading && groups.length > 0 && (
              <span className="text-accent">· {t.newcomers.countSuffix(groups.length)}</span>
            )}
          </>
        ) : (
          <>
            {pubQuery.data && <span>{t.newcomers.publisherWindowHint(pubQuery.data.window)}</span>}
            {!pubQuery.isLoading && pubGroupCount > 0 && (
              <span className="text-accent">· {t.newcomers.countSuffix(pubGroupCount)}</span>
            )}
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 bg-elevated rounded-lg p-1">
          {(['market', 'publisher'] as const).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
            >
              {v === 'market' ? <Globe2 size={12} /> : <Building2 size={12} />}
              {v === 'market' ? t.newcomers.viewMarket : t.newcomers.viewPublishers}
            </button>
          ))}
        </div>
        {view === 'market' && (
          <>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {(['all', ...PLATFORMS] as const).map(p => (
                <button
                  key={p}
                  onClick={() => setMktPlatform(p)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${mktPlatform === p ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {p === 'all' ? t.newcomers.allLabel : platformLabel(p)}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {(['all', ...COUNTRIES] as const).map(c => (
                <button
                  key={c}
                  onClick={() => setMktCountry(c)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${mktCountry === c ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {c === 'all' ? t.newcomers.allLabel : c}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {([50, 100] as const).map(n => (
                <button
                  key={n}
                  onClick={() => setTopn(n)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium font-data transition-colors ${topn === n ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  Top {n}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {([30, 90] as const).map(n => (
                <button
                  key={n}
                  onClick={() => setDays(n)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium font-data transition-colors ${days === n ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {t.newcomers.rangeDays(n)}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1" title={t.newcomers.signalHint}>
              {(['true_new', 'reentry', 'all'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setSignal(s)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${signal === s ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {s === 'true_new' && <Sparkles size={11} />}
                  {s === 'reentry' && <Repeat size={11} />}
                  {s === 'true_new' ? t.newcomers.signalTrueNew : s === 'reentry' ? t.newcomers.signalReentry : t.newcomers.signalAll}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1" title={t.newcomers.chartHint}>
              {(['grossing', 'free', 'all'] as const).map(ch => (
                <button
                  key={ch}
                  onClick={() => setChart(ch)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${chart === ch ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {ch === 'free' && <DownloadIcon size={11} />}
                  {ch === 'grossing' ? t.newcomers.chartGrossing : ch === 'free' ? t.newcomers.chartFree : t.newcomers.chartAll}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {view === 'market' && data?.as_of_by_combo && <StaleCombosWarning asOfByCombo={data.as_of_by_combo} today={data.today} />}

      {view === 'publisher' ? (
        <>
          <AppstoreReleasesSection />
          <PublisherNewcomersTable query={pubQuery} focusId={focusId} />
        </>
      ) : (
      <div>
        {isError ? (
          <div className="bg-surface border border-default rounded-xl overflow-hidden"><QueryError onRetry={() => refetch()} /></div>
        ) : isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="bg-surface border border-default rounded-xl p-4 animate-pulse">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 bg-elevated rounded-xl" />
                  <div className="space-y-1.5 flex-1">
                    <div className="w-32 h-3.5 bg-elevated rounded" />
                    <div className="w-20 h-3 bg-elevated rounded" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : groups.length === 0 ? (
          <div className="bg-surface border border-default rounded-xl py-16 text-center text-muted text-sm">{t.newcomers.historyEmpty}</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {groups.map(gr => {
              const g = gr.rep
              const multi = gr.markets.length > 1
              return (
              <div
                key={gr.app_id}
                data-app-id={gr.app_id}
                onClick={() => setSelected(gr)}
                className={`bg-surface border border-default rounded-xl p-4 cursor-pointer transition-colors space-y-3 ${focusId === gr.app_id ? 'focus-flash' : 'hover:border-strong'}`}
              >
                <div className="flex items-start gap-3">
                  <GameIcon src={g.icon_url} name={g.name} className="w-12 h-12 rounded-xl shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-primary flex items-center gap-1.5">
                      <Sparkles size={13} className="text-accent shrink-0" />
                      <span className="truncate">{g.name}</span>
                    </div>
                    <div className="text-xs text-muted truncate">{g.publisher}</div>
                  </div>
                  <div className="text-right shrink-0 space-y-1">
                    {multi ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold font-data bg-elevated text-secondary border border-default" title={gr.markets.map(m => `${m.country}/${m.platform} #${m.rank ?? '—'}`).join(' · ')}>
                        <Globe2 size={10} />{t.newcomers.marketsBadge(gr.markets.length)}
                      </span>
                    ) : (
                      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold font-data bg-elevated text-secondary border border-default">
                        {g.country} · {platformLabel(g.platform as Platform)}
                      </span>
                    )}
                    <div className={`text-sm font-bold font-data ${gr.bestRank == null ? 'text-muted' : gr.bestRank <= 10 ? 'text-yellow-400' : gr.bestRank <= 50 ? 'text-primary' : 'text-muted'}`}>
                      #{gr.bestRank ?? '—'}
                    </div>
                  </div>
                </div>
                {multi && (
                  <div className="flex flex-wrap items-center gap-1">
                    {gr.markets.map(m => (
                      <span key={`${m.country}/${m.platform}`} className="inline-block px-1.5 py-0.5 rounded text-[10px] font-data bg-elevated/60 text-muted border border-default/60">
                        {m.country} · {platformLabel(m.platform as Platform)} #{m.rank ?? '—'}
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-data text-muted">
                  {g.genre && <span className="px-1.5 py-0.5 bg-elevated rounded text-secondary">{g.genre}</span>}
                  {g.rating != null && g.rating > 0 && (
                    <span className="inline-flex items-center gap-0.5 text-amber-400">
                      <Star size={10} className="fill-current" />{g.rating.toFixed(1)}
                      {g.rating_count != null && g.rating_count > 0 && <span className="text-muted">({formatNumber(g.rating_count)})</span>}
                    </span>
                  )}
                  {g.price && <span>{t.newcomers.appstorePrice(g.price)}</span>}
                  {g.release_date && <span>{t.newcomers.appstoreReleasedAt(g.release_date)}</span>}
                  {g.revenue != null && <span className="text-emerald-400">{formatRevenue(g.revenue)}</span>}
                  <span className="ml-auto">{t.newcomers.detectedAt(gr.earliestAsOf)}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  {gr.anyFree && (
                    <span
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-violet-500/15 text-violet-400 border border-violet-500/30"
                      title={t.newcomers.chartHint}
                    >
                      <DownloadIcon size={10} />{t.newcomers.chartFreeBadge}
                    </span>
                  )}
                  {gr.anyReentry && (
                    <span
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/30"
                      title={t.newcomers.reentryHint}
                    >
                      <Repeat size={10} />{t.newcomers.reentryBadge}
                    </span>
                  )}
                  {g.entity_name ? (
                    <span className="inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-brand-600/15 text-brand-500">
                      {t.newcomers.attributedTo(g.entity_name)}
                    </span>
                  ) : g.is_slg ? (
                    <span className="inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-brand-600/15 text-brand-500">
                      {t.newcomers.slgKnown}
                    </span>
                  ) : (
                    <>
                      <span className="inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-amber-500/15 text-amber-500">
                        {t.newcomers.slgUnknown}
                      </span>
                      <button
                        onClick={ev => { ev.stopPropagation(); handleTriage(g) }}
                        disabled={triageMut.isPending}
                        title={t.newcomers.triage}
                        className="inline-flex items-center gap-1 text-[10px] text-brand-400 hover:text-brand-300 border border-brand-500/30 hover:border-brand-500/60 rounded px-1.5 py-0.5 transition-colors disabled:opacity-50"
                      >
                        <FilePlus2 size={11} />{t.newcomers.triage}
                      </button>
                      <IgnoreControl g={g} disabled={ignoreMut.isPending} onIgnore={handleIgnore} />
                    </>
                  )}
                </div>
              </div>
              )
            })}
          </div>
        )}
      </div>
      )}

      {selected && <NewcomerDrawer group={selected} onClose={() => setSelected(null)} />}

      <div className="flex items-start gap-2 text-[11px] text-muted/80 leading-relaxed">
        <Info size={13} className="mt-0.5 shrink-0" />
        <span>{view === 'market' ? t.newcomers.note : t.newcomers.publisherNote}</span>
      </div>
    </div>
  )
}


/** 忽略控件：无发行商名 → 单按钮 app_id 粒度（与旧行为一致）；有名 → 下拉两选项
 *  （忽略整个发行商 / 仅忽略此 app），让运营对「同厂只想滤掉这一款」的场景有 app 粒度。
 *  所有 hooks 在任何 early return 之前（prop 切换不变 hook 数量，避免崩页）。 */
function IgnoreControl({ g, disabled, onIgnore }: {
  g: NewcomerHistoryItem
  disabled: boolean
  onIgnore: (g: NewcomerHistoryItem, scope: 'publisher' | 'app_id') => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const btnCls = 'inline-flex items-center gap-1 text-[10px] text-muted hover:text-secondary border border-default hover:border-strong rounded px-1.5 py-0.5 transition-colors disabled:opacity-50'
  const pub = g.publisher?.trim()

  if (!pub) {
    return (
      <button
        onClick={ev => { ev.stopPropagation(); onIgnore(g, 'app_id') }}
        disabled={disabled}
        title={t.newcomers.ignore}
        className={btnCls}
      >
        <Ban size={11} />{t.newcomers.ignore}
      </button>
    )
  }
  return (
    <div ref={ref} className="relative" onClick={ev => ev.stopPropagation()}>
      <button onClick={() => setOpen(o => !o)} disabled={disabled} title={t.newcomers.ignore} className={btnCls}>
        <Ban size={11} />{t.newcomers.ignore}<ChevronDown size={10} />
      </button>
      {open && (
        <div className="absolute z-20 right-0 mt-1 w-48 bg-elevated border border-default rounded-lg shadow-lg overflow-hidden">
          <button
            onClick={() => { setOpen(false); onIgnore(g, 'publisher') }}
            className="w-full text-left px-3 py-2 text-[11px] text-secondary hover:bg-surface hover:text-primary transition-colors"
          >
            {t.newcomers.ignoreScopePublisher(pub)}
          </button>
          <button
            onClick={() => { setOpen(false); onIgnore(g, 'app_id') }}
            className="w-full text-left px-3 py-2 text-[11px] text-secondary hover:bg-surface hover:text-primary transition-colors border-t border-default"
          >
            {t.newcomers.ignoreScopeApp}
          </button>
        </div>
      )}
    </div>
  )
}


/** 数据新鲜度提示：把陈旧 combo（≥3 天）单独列出，让用户知道某市场榜单同步晚了。
 *  数据来源：/history 的 as_of_by_combo（来自 game_rankings 的 MAX(date) per combo）。
 *  阈值：≥3 天提示，≥14 天加重提示（红）。<3 天不渲染，避免视觉噪声。 */
function StaleCombosWarning({ asOfByCombo, today }: { asOfByCombo: Record<string, string>; today: string }) {
  const t = useT()
  const todayMs = Date.parse(today + 'T00:00:00Z')
  const stale = Object.entries(asOfByCombo)
    .map(([combo, d]) => ({ combo, d, days: Math.floor((todayMs - Date.parse(d + 'T00:00:00Z')) / 86400000) }))
    .filter(x => x.days >= 3)
    .sort((a, b) => b.days - a.days)
  if (stale.length === 0) return null
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[11px] font-data">
      <Clock size={12} className="mt-0.5 shrink-0 text-amber-400" />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-secondary">
        <span className="text-amber-400 font-medium">{t.newcomers.stalenessHeader}</span>
        {stale.map(s => (
          <span key={s.combo} className={s.days >= 14 ? 'text-red-400' : 'text-amber-400/90'}>
            {s.combo} · {t.newcomers.stalenessDaysAgo(s.days)}
          </span>
        ))}
        <span className="basis-full text-muted">{t.newcomers.stalenessNote}</span>
      </div>
    </div>
  )
}


/** 新面孔详情抽屉：免费源富化的描述/截图 + 各市场名次 + 商店页/看板跳转。
 *  hooks 全部在任何条件返回之前（prop 切换时 hook 数量不变）。
 *  跨市场合并后展示代表行（最佳名次行）的富化字段 + 全部市场的逐条检出。 */
function NewcomerDrawer({ group, onClose }: { group: GroupedNewcomer; onClose: () => void }) {
  const t = useT()
  const navigate = useNavigate()
  const item = group.rep
  const multi = group.markets.length > 1
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="absolute right-0 top-0 h-full w-full max-w-[560px] bg-surface border-l border-default overflow-y-auto">
        <div className="sticky top-0 bg-surface/95 backdrop-blur border-b border-default px-5 py-4 flex items-center gap-3">
          <GameIcon src={item.icon_url} name={item.name} className="w-10 h-10 rounded-xl shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-primary truncate">{item.name}</div>
            <div className="text-xs text-muted truncate">{item.publisher}</div>
          </div>
          <button onClick={onClose} className="p-1.5 text-muted hover:text-primary transition-colors"><X size={16} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[11px] font-data">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-elevated rounded text-secondary border border-default">
              {multi ? (<><Globe2 size={10} />{t.newcomers.marketsBadge(group.markets.length)}</>) : (<>{item.country} · {platformLabel(item.platform as Platform)}</>)}
            </span>
            <span className={`font-bold ${group.bestRank != null && group.bestRank <= 10 ? 'text-yellow-400' : 'text-primary'}`}>
              #{group.bestRank ?? '—'}{multi && group.bestRank != null && <span className="ml-1 text-muted font-normal">{t.newcomers.marketBestRank}</span>}
            </span>
            {item.genre && <span className="px-1.5 py-0.5 bg-elevated rounded text-secondary">{item.genre}</span>}
            {item.rating != null && item.rating > 0 && (
              <span className="inline-flex items-center gap-0.5 text-amber-400">
                <Star size={10} className="fill-current" />{item.rating.toFixed(1)}
                {item.rating_count != null && item.rating_count > 0 && <span className="text-muted">({formatNumber(item.rating_count)})</span>}
              </span>
            )}
            {item.price && <span className="text-muted">{t.newcomers.appstorePrice(item.price)}</span>}
            {item.release_date && <span className="text-muted">{t.newcomers.appstoreReleasedAt(item.release_date)}</span>}
            <span className="text-muted ml-auto">{t.newcomers.detectedAt(group.earliestAsOf)}</span>
          </div>
          {multi && (
            <div>
              <div className="text-[11px] text-muted uppercase tracking-wider mb-1.5">{t.newcomers.drawerMarkets}</div>
              <div className="space-y-1">
                {group.markets.map(m => (
                  <div key={`${m.country}/${m.platform}`} className="flex items-center gap-2 text-[11px] font-data text-secondary">
                    <span className="px-1.5 py-0.5 bg-elevated rounded border border-default w-24 shrink-0">
                      {m.country} · {platformLabel(m.platform as Platform)}
                    </span>
                    <span className={`font-bold ${m.rank != null && m.rank <= 10 ? 'text-yellow-400' : 'text-primary'}`}>#{m.rank ?? '—'}</span>
                    {m.is_reentry === true && (
                      <span className="inline-flex items-center gap-0.5 text-cyan-400" title={t.newcomers.reentryHint}>
                        <Repeat size={10} />{t.newcomers.reentryBadge}
                      </span>
                    )}
                    {m.revenue != null && <span className="text-emerald-400">{formatRevenue(m.revenue)}</span>}
                    <span className="text-muted ml-auto">{t.newcomers.detectedAt(m.as_of)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-2">
            {item.store_url && (
              <a href={item.store_url} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-600 text-white hover:bg-brand-500 transition-colors">
                <ExternalLink size={12} />{t.newcomers.openStore}
              </a>
            )}
            <button onClick={() => navigate(`/game/${item.app_id}`)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-default text-secondary hover:text-primary hover:border-strong transition-colors">
              {t.newcomers.openDetail}
            </button>
          </div>
          <div>
            <div className="text-[11px] text-muted uppercase tracking-wider mb-1.5">{t.newcomers.drawerDesc}</div>
            {item.description ? (
              <p className="text-xs text-secondary leading-relaxed whitespace-pre-wrap">{item.description}</p>
            ) : (
              <p className="text-xs text-muted">{t.newcomers.noDesc}</p>
            )}
          </div>
          {item.screenshots.length > 0 && (
            <div>
              <div className="text-[11px] text-muted uppercase tracking-wider mb-1.5">{t.newcomers.drawerShots}</div>
              <div className="flex gap-2 overflow-x-auto pb-2">
                {item.screenshots.map(u => (
                  <img key={u} src={u} alt="" className="h-44 rounded-lg border border-default shrink-0" loading="lazy" />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function AppstoreReleasesSection() {
  const t = useT()
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['appstoreReleases'],
    queryFn: () => newcomersApi.appstore(60),
  })
  const syncMut = useMutation({
    mutationFn: () => newcomersApi.appstoreSync(),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ['appstoreReleases'] })
      qc.invalidateQueries({ queryKey: ['publishers'] })
      toast.success(t.newcomers.appstoreSynced(s.synced, s.baselined, s.new_apps))
    },
    onError: () => toast.error(t.newcomers.appstoreSyncFailed),
  })

  const items = data?.items ?? []

  return (
    <div className="bg-surface border border-default rounded-xl overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-5 py-3 border-b border-default">
        <Store size={14} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-primary">{t.newcomers.appstoreTitle}</span>
        <span className="font-data text-[11px] text-muted">{t.newcomers.appstoreHint(data?.days ?? 60)}</span>
        {data && data.artists_total > 0 && (
          <span className="font-data text-[11px] text-muted">· {t.newcomers.appstoreArtists(data.artists_synced, data.artists_total)}</span>
        )}
        <button
          onClick={() => syncMut.mutate()}
          disabled={syncMut.isPending}
          className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-secondary hover:text-primary border border-default hover:border-strong rounded-lg px-2.5 py-1 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={11} className={syncMut.isPending ? 'animate-spin' : ''} />
          {t.newcomers.appstoreSyncNow}
        </button>
      </div>
      {isError ? (
        <QueryError compact onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="py-8 text-center text-muted text-sm">{t.common.loading}</div>
      ) : (data?.artists_total ?? 0) === 0 ? (
        <div className="py-8 px-6 text-center text-muted text-sm">{t.newcomers.appstoreNoArtists}</div>
      ) : data!.artists_synced === 0 ? (
        <div className="py-8 px-6 text-center text-muted text-sm">{t.newcomers.appstoreNoBaseline(data!.artists_total)}</div>
      ) : items.length === 0 ? (
        <div className="py-8 px-6 text-center text-muted text-sm">{t.newcomers.appstoreEmpty}</div>
      ) : (
        <div className="divide-y divide-default">
          {items.map(it => (
            <div key={`${it.entity_id}-${it.track_id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3">
              <span className="inline-flex items-center gap-1.5 text-xs text-primary w-40 shrink-0">
                <Building2 size={12} className="text-accent shrink-0" />
                <span className="truncate">{it.entity_name}</span>
              </span>
              <GameIcon src={it.artwork_url} name={it.name} className="w-9 h-9 rounded-lg" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-primary flex items-center gap-1.5">
                  <Sparkles size={13} className="text-accent shrink-0" />
                  {it.track_view_url ? (
                    <a href={it.track_view_url} target="_blank" rel="noreferrer"
                      className="truncate hover:underline" onClick={ev => ev.stopPropagation()}>
                      {it.name}
                    </a>
                  ) : <span className="truncate">{it.name}</span>}
                  {it.genre && (
                    <span className="shrink-0 text-[10px] font-medium text-secondary bg-elevated rounded px-1.5 py-0.5">
                      {it.genre}
                    </span>
                  )}
                  {it.platform === 'gp' ? (
                    <span className="shrink-0 text-[10px] font-semibold text-emerald-400 bg-emerald-400/10 border border-emerald-400/30 rounded px-1.5 py-0.5 font-data">
                      Google Play
                    </span>
                  ) : it.storefronts.length > 0 && (
                    it.storefronts.includes('us') ? (
                      <span className="shrink-0 text-[10px] font-medium text-secondary bg-elevated rounded px-1.5 py-0.5 font-data">
                        {t.newcomers.appstoreRegions(it.storefronts.map(s => s.toUpperCase()).join('/'))}
                      </span>
                    ) : (
                      <span className="shrink-0 text-[10px] font-semibold text-amber-400 bg-amber-400/10 border border-amber-400/30 rounded px-1.5 py-0.5 font-data">
                        {t.newcomers.appstoreSoftLaunch(it.storefronts.map(s => s.toUpperCase()).join('/'))}
                      </span>
                    )
                  )}
                </div>
                <div className="text-[11px] text-muted truncate font-data flex items-center gap-x-2">
                  {it.rating != null && it.rating > 0 && (
                    <span className="inline-flex items-center gap-0.5 text-amber-400 shrink-0">
                      <Star size={10} className="fill-current" />
                      {it.rating.toFixed(1)}
                      {it.rating_count != null && it.rating_count > 0 && (
                        <span className="text-muted">· {t.newcomers.appstoreRatingCount(formatNumber(it.rating_count))}</span>
                      )}
                    </span>
                  )}
                  {it.price && <span className="shrink-0">{t.newcomers.appstorePrice(it.price)}</span>}
                  <span className="truncate">{it.bundle_id}{it.artist_label ? ` · ${it.artist_label}` : ''}</span>
                </div>
                {it.description && (
                  <p className="mt-1 text-[11px] leading-snug text-muted line-clamp-2">
                    {it.description}
                  </p>
                )}
              </div>
              {it.release_date && (
                <span className="text-[11px] text-secondary font-data shrink-0">
                  {t.newcomers.appstoreReleasedAt(it.release_date)}
                </span>
              )}
              <span className="text-[11px] text-muted font-data shrink-0">
                {t.newcomers.appstoreFirstSeen(String(it.first_seen_at).slice(0, 10))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PublisherNewcomersTable({ query, focusId }: { query: UseQueryResult<PublisherNewcomersOut>; focusId: string | null }) {
  const t = useT()
  const navigate = useNavigate()
  const { data, isLoading, isError, refetch } = query
  const items = data?.items ?? []
  // D2：同款多市场合并成一行 + 市场徽标（与全市场视图 D1 同轴对称）。
  const groups = useMemo(() => groupPublisherByApp(items), [items])

  return (
    <div className="bg-surface border border-default rounded-xl overflow-hidden">
      {isError ? (
        <QueryError onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="py-16 text-center text-muted text-sm">{t.common.loading}</div>
      ) : groups.length === 0 ? (
        <div className="py-16 px-6 text-center text-muted text-sm">{t.newcomers.publisherEmpty}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-default text-xs text-muted uppercase tracking-wider">
                <th className="px-5 py-3 text-left w-44">{t.newcomers.entityCol}</th>
                <th className="px-3 py-3 text-left">{t.newcomers.game}</th>
                <th className="px-3 py-3 text-left w-24">{t.newcomers.marketCol}</th>
                <th className="px-3 py-3 text-right w-16">{t.newcomers.rank}</th>
                <th className="px-3 py-3 text-right">{t.newcomers.revenue}</th>
                <th className="px-3 py-3 text-right w-24">{t.csv.date}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-default">
              {groups.map(gr => {
                const g = gr.rep
                const multi = gr.markets.length > 1
                return (
                <tr
                  key={gr.app_id}
                  data-app-id={gr.app_id}
                  className={`cursor-pointer transition-colors ${focusId === gr.app_id ? 'bg-accent/10 focus-flash' : 'hover:bg-elevated/50'}`}
                  onClick={() => navigate(`/game/${gr.app_id}`)}
                >
                  <td className="px-5 py-3.5">
                    <span className="inline-flex items-center gap-1.5 text-xs text-primary">
                      <Building2 size={12} className="text-accent shrink-0" />
                      <span className="truncate max-w-[150px]">{g.entity_name}</span>
                    </span>
                  </td>
                  <td className="px-3 py-3.5">
                    <div className="flex items-center gap-3">
                      <GameIcon src={g.icon_url} name={g.name ?? gr.app_id} className="w-10 h-10 rounded-xl" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-primary flex items-center gap-1.5">
                          <Sparkles size={13} className="text-accent shrink-0" />
                          <span className="truncate">{g.name}</span>
                        </div>
                        <div className="text-xs text-muted truncate">
                          {g.publisher}
                          <span className="ml-1.5 text-[10px] text-secondary border border-default rounded px-1 py-px">
                            {g.matched_by === 'app_id' ? t.newcomers.matchedAppId : t.newcomers.matchedAlias}
                          </span>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-3.5 text-xs text-secondary font-data">
                    {multi ? (
                      <span className="inline-flex items-center gap-1" title={gr.markets.map(m => `${m.country}/${m.platform} #${m.rank ?? '—'}`).join(' · ')}>
                        <Globe2 size={11} className="text-accent shrink-0" />{t.newcomers.marketsBadge(gr.markets.length)}
                      </span>
                    ) : `${g.country}/${g.platform}`}
                  </td>
                  <td className="px-3 py-3.5 text-right">
                    <span className="text-sm font-bold text-primary">#{gr.bestRank ?? '—'}</span>
                  </td>
                  <td className="px-3 py-3.5 text-right">
                    <span className="text-sm font-medium text-emerald-400">
                      {g.revenue == null ? <span className="text-muted">—</span> : formatRevenue(g.revenue)}
                    </span>
                  </td>
                  <td className="px-3 py-3.5 text-right text-xs text-muted font-data">{gr.earliestAsOf}</td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <WechatAccountsPanel />
    </div>
  )
}
