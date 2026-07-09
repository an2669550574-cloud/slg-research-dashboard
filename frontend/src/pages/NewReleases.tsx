import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { newcomersApi, publishersApi, gamesApi } from '../lib/api'
import { formatRevenue, formatNumber } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Download as DownloadIcon, Sparkles, Info, FilePlus2, Globe2, Building2, Store, RefreshCw, Star, X, ExternalLink, Repeat, Clock, Ban, ChevronDown, ChevronRight, Youtube, TrendingUp, TrendingDown, Minus, CircleOff, Radar, Activity } from 'lucide-react'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { WechatAccountsPanel } from '../components/WechatAccountsPanel'
import { useLocalStorageState } from '../lib/hooks'
import type { NewcomerHistoryItem, NewcomerTrajectory, PublisherNewcomersOut } from '../lib/types'
import { groupByApp, groupPublisherByApp, type GroupedNewcomer, type GroupedPublisherNewcomer } from '../lib/newcomerGrouping'

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
  // Top 档位真过滤：此前 topn=100 传 undefined（完全不过滤），「Top 100」按钮点亮却混入
  // 101-200 的主体深榜行——标签与口径不符。现 50/100 都真传，另加「全部」档显式看全量。
  const [topn, setTopn] = useLocalStorageState<50 | 100 | 'all'>('slg.nc.topn', 100)
  const [days, setDays] = useLocalStorageState<30 | 90>('slg.nc.days', 90)
  // 信号筛选：真首发(默认) / 回归 / 全部。PR #93 把回归识别出来后默认隐藏，
  // 回归独立 tab 给运营回看「老游戏卷土重来」的情报信号。
  const [signal, setSignal] = useLocalStorageState<'true_new' | 'reentry' | 'all'>('slg.nc.signal', 'true_new')
  // 榜类型筛选：收入榜(默认) / 下载榜 / 全部（ADR 0001）。下载榜按装机速度抓更早期新品。
  const [chart, setChart] = useLocalStorageState<'grossing' | 'free' | 'all'>('slg.nc.chart', 'grossing')
  // SLG 状态筛选：默认只看「已识别 SLG」（发行商在白名单/已建档），把「待识别新厂」
  // (is_slg=false) 折进独立选项——次市场涌入的非 SLG 噪声不再默认刷屏催建档。
  const [slgFilter, setSlgFilter] = useLocalStorageState<'slg' | 'pending' | 'all'>('slg.nc.slgstatus', 'slg')
  // 检出后走势筛选（P0-1）：全部 / 仍在爬升（起飞新品）/ 已掉榜（昙花一现）。默认全部。
  const [trendFilter, setTrendFilter] = useLocalStorageState<'all' | 'climbing' | 'dropped'>('slg.nc.trend', 'all')
  const [selected, setSelected] = useState<GroupedNewcomer | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['newcomerHistory', mktCountry, mktPlatform, topn, days, signal, chart],
    queryFn: () => newcomersApi.history({
      days,
      topn: topn === 'all' ? undefined : topn,
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
  const allGroups = useMemo(() => groupByApp(items), [items])
  // SLG 状态分桶：is_slg 活算（含已建档主体），用代表行判定。计数给筛选 chip 显示
  // 「待识别 N」，让被默认隐藏的待识别量一眼可见（不静默吞）。
  const slgCount = useMemo(() => allGroups.filter(g => g.rep.is_slg).length, [allGroups])
  const pendingCount = allGroups.length - slgCount
  const groups = useMemo(() => {
    let g = allGroups
    if (slgFilter === 'slg') g = g.filter(x => x.rep.is_slg)
    else if (slgFilter === 'pending') g = g.filter(x => !x.rep.is_slg)
    if (trendFilter === 'climbing') g = g.filter(x => x.rep.trajectory?.trend === 'climbing')
    else if (trendFilter === 'dropped') g = g.filter(x => x.rep.trajectory?.trend === 'dropped')
    return g
  }, [allGroups, slgFilter, trendFilter])
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
              {([50, 100, 'all'] as const).map(n => (
                <button
                  key={n}
                  onClick={() => setTopn(n)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium font-data transition-colors ${topn === n ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {n === 'all' ? t.newcomers.allLabel : `Top ${n}`}
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
            <div className="flex gap-1 bg-elevated rounded-lg p-1" title={t.newcomers.slgFilterHint}>
              {(['slg', 'pending', 'all'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setSlgFilter(s)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${slgFilter === s ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {s === 'slg' && <Star size={11} />}
                  {s === 'pending' && <FilePlus2 size={11} />}
                  {s === 'slg'
                    ? `${t.newcomers.slgFilterSlg}${!isLoading && slgCount ? ` ${slgCount}` : ''}`
                    : s === 'pending'
                    ? `${t.newcomers.slgFilterPending}${!isLoading && pendingCount ? ` ${pendingCount}` : ''}`
                    : t.newcomers.slgFilterAll}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1" title={t.newcomers.trendFilterHint}>
              {(['all', 'climbing', 'dropped'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setTrendFilter(s)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${trendFilter === s ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {s === 'climbing' && <TrendingUp size={11} />}
                  {s === 'dropped' && <CircleOff size={11} />}
                  {s === 'all' ? t.newcomers.trendFilterAll : s === 'climbing' ? t.newcomers.trendFilterClimbing : t.newcomers.trendFilterDropped}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {view === 'market' && data?.as_of_by_combo && <StaleCombosWarning asOfByCombo={data.as_of_by_combo} today={data.today} />}

      {view === 'market' && <SubgenrePulse days={days} />}

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
                    <div className="flex justify-end"><TrendBadge traj={g.trajectory} /></div>
                  </div>
                </div>
                {g.summary_cn && (
                  <div className="text-[11px] text-secondary leading-snug line-clamp-2">📝 {g.summary_cn}</div>
                )}
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
                  {gr.subgenre && <span className="px-1.5 py-0.5 bg-elevated rounded text-accent" title={t.newcomers.subgenreHint}>{gr.subgenre}</span>}
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


/** 赛道脉搏（P1-2 stretch）：近 N 天各玩法子品类的新品数（CSS 横条）+ 环比上一个等长窗口
 *  的升温/降温箭头。回答「哪个赛道在冒新品」。默认收起（省地方），窗口跟随页面 days 筛选。
 *  无分类数据整卡不渲染。所有 hooks 在任何 early return 之前。 */
function SubgenrePulse({ days }: { days: number }) {
  const t = useT()
  const [open, setOpen] = useLocalStorageState('slg.nc.pulseOpen', false)
  const { data } = useQuery({
    queryKey: ['subgenrePulse', days],
    queryFn: () => newcomersApi.subgenrePulse(days),
    staleTime: 5 * 60 * 1000,
  })
  if (!data || data.total === 0) return null
  const max = Math.max(...data.buckets.map(b => b.count), 1)
  const hottest = data.buckets[0]?.subgenre ?? ''
  return (
    <section className="border border-default bg-surface rounded-xl">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2.5 px-4 py-3 text-left">
        <span className="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center bg-accent/15">
          <Activity size={14} className="text-accent" />
        </span>
        <span className="font-display text-sm font-semibold text-primary">{t.newcomers.pulseTitle(days)}</span>
        <span className="text-[11px] text-muted truncate">{t.newcomers.pulseSummary(data.total, hottest)}</span>
        <span className="ml-auto text-[11px] text-muted shrink-0">{open ? t.newcomers.pulseCollapse : t.newcomers.pulseExpand}</span>
        {open ? <ChevronDown size={15} className="text-muted shrink-0" /> : <ChevronRight size={15} className="text-muted shrink-0" />}
      </button>
      {open && (
        <div className="border-t border-default px-4 py-3">
          <div className="text-[11px] text-muted mb-3">{t.newcomers.pulseHint(days)}</div>
          <div className="space-y-1.5">
            {data.buckets.map(b => (
              <div key={b.subgenre} className="flex items-center gap-2">
                <span className="w-24 shrink-0 text-xs text-secondary truncate" title={b.subgenre}>{b.subgenre}</span>
                <div className="flex-1 h-4 bg-elevated rounded overflow-hidden">
                  <div className="h-full bg-accent/50 rounded-r" style={{ width: `${Math.max(Math.round((b.count / max) * 100), b.count > 0 ? 4 : 0)}%` }} />
                </div>
                <span className="w-7 text-right text-xs font-data text-primary">{b.count}</span>
                <span className={`w-11 text-right text-[10px] font-data ${b.delta > 0 ? 'text-emerald-400' : b.delta < 0 ? 'text-amber-400' : 'text-muted'}`}>
                  {b.delta > 0 ? `↑${b.delta}` : b.delta < 0 ? `↓${-b.delta}` : '–'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}


/** 检出后走势徽标（P0-1）：把「新品检出即阅后即焚」补成态势——这款现在爬到哪了 / 掉榜没。
 *  climbing/falling/stable 显示「现 #名次」+ 方向箭头；dropped 显示「已掉榜」；
 *  new（检出当天、无后续数据）/ unknown（无轨迹点）不渲染，避免噪声。tooltip 带峰值 + 追踪天数。 */
function TrendBadge({ traj }: { traj: NewcomerTrajectory | null }) {
  const t = useT()
  if (!traj || traj.trend === 'new' || traj.trend === 'unknown') return null
  if (traj.trend === 'dropped') {
    return (
      <span
        title={traj.peak_rank != null && traj.last_seen ? t.newcomers.trendDroppedTooltip(traj.peak_rank, traj.last_seen) : undefined}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-red-500/10 text-red-400 border border-red-500/25"
      >
        <CircleOff size={10} />{t.newcomers.trendDropped}
      </span>
    )
  }
  const cls = traj.trend === 'climbing'
    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
    : traj.trend === 'falling'
    ? 'bg-amber-500/10 text-amber-400 border-amber-500/25'
    : 'bg-elevated text-muted border-default'
  return (
    <span
      title={traj.peak_rank != null && traj.days_tracked != null ? t.newcomers.trendTooltip(traj.peak_rank, traj.days_tracked) : undefined}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium font-data border ${cls}`}
    >
      {traj.trend === 'climbing' ? <TrendingUp size={10} /> : traj.trend === 'falling' ? <TrendingDown size={10} /> : <Minus size={10} />}
      {traj.current_rank != null ? t.newcomers.trendNow(traj.current_rank) : ''}
    </span>
  )
}


/** 一键晋升深度追踪（P0-2）：把新品建为 tracked 竞品 → iOS 自动获版本更新 / 分地区上线
 *  追踪 + 详情页趋势（安卓无版本源、仅趋势）。复用 POST /games/——iOS 数字 app_id 本身
 *  即 trackId，version_tracker 自动识别，无需人工补 ios_track_id。已存在（400）→ 提示已追踪。 */
function PromoteToTrackedButton({ item }: { item: {
  app_id: string; name: string; publisher: string | null; icon_url: string | null
  platform: string; country: string
} }) {
  const t = useT()
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => gamesApi.create({
      app_id: item.app_id, name: item.name, publisher: item.publisher,
      icon_url: item.icon_url, platform: item.platform, country: item.country,
    }),
    onSuccess: (g) => {
      qc.invalidateQueries({ queryKey: ['games'] })
      toast.success(t.newcomers.promoted(g.name))
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 400) toast(t.newcomers.promoteExists)
      else toast.error(t.newcomers.promoteFailed)
    },
  })
  const handle = () => {
    if (!window.confirm(t.newcomers.promoteConfirm(item.name))) return
    mut.mutate()
  }
  return (
    <button onClick={handle} disabled={mut.isPending} title={t.newcomers.promoteHint}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-default text-secondary hover:text-primary hover:border-strong transition-colors disabled:opacity-50">
      <Radar size={12} />{t.newcomers.promote}
    </button>
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


/** 商店详情公共块：版本 / 支持语言 / 简介 / 截图。market 抽屉（落库富化）与 publisher
 *  抽屉（按需实时富化）共用——两者富化字段同名同形（NewcomerHistoryItem / StoreDetail）。
 *  无 hooks 之外的逻辑，纯展示；语言前 14 个码徽标，余下折叠成「等 N 种」。 */
function StoreDetailSection({ d }: { d: {
  version: string | null
  current_version_date: string | null
  languages: string | null
  description: string | null
  /** 中文化（market 抽屉走落库翻译；publisher 实时富化暂无 → 可选）。 */
  summary_cn?: string | null
  description_cn?: string | null
  screenshots: string[]
} }) {
  const t = useT()
  // 描述默认显示中文译文（若有），可切回原文；无译文则恒显原文，按钮不出现。
  const [showOriginal, setShowOriginal] = useState(false)
  const langCodes = d.languages ? d.languages.split(',').filter(Boolean) : []
  const langShown = langCodes.slice(0, 14)
  const langMore = langCodes.length - langShown.length
  const descText = (d.description_cn && !showOriginal) ? d.description_cn : d.description
  return (
    <>
      {d.summary_cn && (
        <div className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-3 py-2 text-xs text-secondary leading-relaxed">
          📝 {d.summary_cn}
        </div>
      )}
      {(d.version || langCodes.length > 0) && (
        <div className="space-y-2 text-[11px] font-data">
          {d.version && (
            <div className="flex items-center gap-2">
              <span className="text-muted uppercase tracking-wider w-16 shrink-0">{t.newcomers.drawerVersion}</span>
              <span className="text-secondary">v{d.version}</span>
              {d.current_version_date && (
                <span className="text-muted">{t.newcomers.drawerVersionUpdated(d.current_version_date)}</span>
              )}
            </div>
          )}
          {langCodes.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-muted uppercase tracking-wider w-16 shrink-0 mt-0.5">{t.newcomers.drawerLanguages}</span>
              <span className="flex flex-wrap items-center gap-1">
                {langShown.map(l => (
                  <span key={l} className="px-1.5 py-0.5 bg-elevated rounded text-secondary">{l.toUpperCase()}</span>
                ))}
                {langMore > 0 && <span className="text-muted">{t.newcomers.drawerLangMore(langMore)}</span>}
              </span>
            </div>
          )}
        </div>
      )}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[11px] text-muted uppercase tracking-wider">{t.newcomers.drawerDesc}</span>
          {d.description_cn && d.description && (
            <button onClick={() => setShowOriginal(v => !v)}
              className="text-[10px] text-muted hover:text-secondary transition-colors">
              {showOriginal ? t.newcomers.descShowCn : t.newcomers.descShowOriginal}
            </button>
          )}
        </div>
        {descText ? (
          <p className="text-xs text-secondary leading-relaxed whitespace-pre-wrap">{descText}</p>
        ) : (
          <p className="text-xs text-muted">{t.newcomers.noDesc}</p>
        )}
      </div>
      {d.screenshots.length > 0 && (
        <div>
          <div className="text-[11px] text-muted uppercase tracking-wider mb-1.5">{t.newcomers.drawerShots}</div>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {d.screenshots.map(u => (
              <img key={u} src={u} alt="" className="h-44 rounded-lg border border-default shrink-0" loading="lazy" />
            ))}
          </div>
        </div>
      )}
    </>
  )
}


/** 实机玩法视频段：定时自动搜来的 YouTube 候选（ADR 0002 切片 1c，零 ST）。
 *  缩略图卡 + 标题 + 频道/日期 + 跳 YT + 人工删噪。无候选则整段不渲染（含未搜/0 命中）。
 *  market / publisher 两抽屉共用，按 app_id 读，与富化来源无关。 */
function NewcomerVideoSection({ appId }: { appId: string }) {
  const t = useT()
  const qc = useQueryClient()
  const { data: videos } = useQuery({
    queryKey: ['newcomer-videos', appId],
    queryFn: () => newcomersApi.videos(appId),
    staleTime: 5 * 60 * 1000,
  })
  const del = useMutation({
    mutationFn: (id: number) => newcomersApi.deleteVideo(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['newcomer-videos', appId] }),
  })
  if (!videos || videos.length === 0) return null
  return (
    <div>
      <div className="text-[11px] text-muted uppercase tracking-wider mb-1.5">{t.newcomers.drawerVideos}</div>
      <div className="space-y-2">
        {videos.map(v => (
          <div key={v.id} className="group flex gap-2.5 items-start">
            <a href={v.url} target="_blank" rel="noreferrer" className="relative shrink-0">
              {v.thumbnail
                ? <img src={v.thumbnail} alt="" className="w-32 h-[72px] object-cover rounded-lg border border-default" loading="lazy" />
                : <div className="w-32 h-[72px] rounded-lg bg-elevated border border-default" />}
              <Youtube size={18} className="absolute inset-0 m-auto text-red-500 drop-shadow" />
            </a>
            <div className="min-w-0 flex-1">
              <a href={v.url} target="_blank" rel="noreferrer"
                 className="text-xs text-secondary hover:text-primary line-clamp-2 leading-snug">{v.title}</a>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
                {v.channel && <span className="truncate">{v.channel}</span>}
                {v.published_at && <span className="shrink-0">{v.published_at}</span>}
              </div>
            </div>
            <button onClick={() => del.mutate(v.id)} disabled={del.isPending}
              title={t.newcomers.videoRemove}
              className="shrink-0 p-1 text-muted hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity">
              <X size={13} />
            </button>
          </div>
        ))}
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
            <TrendBadge traj={item.trajectory} />
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
            <PromoteToTrackedButton item={item} />
          </div>
          <StoreDetailSection d={item} />
          <NewcomerVideoSection appId={item.app_id} />
        </div>
      </div>
    </div>
  )
}

/** 厂商新品详情抽屉：与 market 抽屉对称，但 publisher 检测实时不落库、无富化字段，
 *  故点开时按需实时富化（GET /newcomers/enrich，免费源、零 ST）。loading / 未命中降级。
 *  hooks 全部在任何 return 之前（prop 切换 hook 数量不变，避免崩页）。 */
function PublisherNewcomerDrawer({ group, onClose }: { group: GroupedPublisherNewcomer; onClose: () => void }) {
  const t = useT()
  const navigate = useNavigate()
  const item = group.rep
  const multi = group.markets.length > 1
  const { data: detail, isLoading } = useQuery({
    queryKey: ['enrich', item.app_id, item.platform, item.country],
    queryFn: () => newcomersApi.enrich(item.app_id, item.platform, item.country),
    staleTime: 5 * 60 * 1000,
  })
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
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-brand-500 bg-brand-600/15">
              <Building2 size={10} />{item.entity_name}
            </span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-elevated rounded text-secondary border border-default">
              {multi ? (<><Globe2 size={10} />{t.newcomers.marketsBadge(group.markets.length)}</>) : (<>{item.country} · {platformLabel(item.platform as Platform)}</>)}
            </span>
            <span className={`font-bold ${group.bestRank != null && group.bestRank <= 10 ? 'text-yellow-400' : 'text-primary'}`}>
              #{group.bestRank ?? '—'}{multi && group.bestRank != null && <span className="ml-1 text-muted font-normal">{t.newcomers.marketBestRank}</span>}
            </span>
            {detail?.genre && <span className="px-1.5 py-0.5 bg-elevated rounded text-secondary">{detail.genre}</span>}
            {detail?.rating != null && detail.rating > 0 && (
              <span className="inline-flex items-center gap-0.5 text-amber-400">
                <Star size={10} className="fill-current" />{detail.rating.toFixed(1)}
                {detail.rating_count != null && detail.rating_count > 0 && <span className="text-muted">({formatNumber(detail.rating_count)})</span>}
              </span>
            )}
            {detail?.price && <span className="text-muted">{t.newcomers.appstorePrice(detail.price)}</span>}
            {detail?.release_date && <span className="text-muted">{t.newcomers.appstoreReleasedAt(detail.release_date)}</span>}
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
                    {m.revenue != null && <span className="text-emerald-400">{formatRevenue(m.revenue)}</span>}
                    <span className="text-muted ml-auto">{t.newcomers.detectedAt(m.as_of)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-2">
            {detail?.store_url && (
              <a href={detail.store_url} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-600 text-white hover:bg-brand-500 transition-colors">
                <ExternalLink size={12} />{t.newcomers.openStore}
              </a>
            )}
            <button onClick={() => navigate(`/game/${item.app_id}`)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-default text-secondary hover:text-primary hover:border-strong transition-colors">
              {t.newcomers.openDetail}
            </button>
            <PromoteToTrackedButton item={item} />
          </div>
          {isLoading ? (
            <div className="py-6 text-center text-xs text-muted">{t.newcomers.enrichLoading}</div>
          ) : detail?.found ? (
            <StoreDetailSection d={detail} />
          ) : (
            <p className="text-xs text-muted">{t.newcomers.noDesc}</p>
          )}
          <NewcomerVideoSection appId={item.app_id} />
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
                {it.summary_cn && (
                  <p className="mt-1 text-[11px] leading-snug text-secondary line-clamp-2">
                    📝 {it.summary_cn}
                  </p>
                )}
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
  const { data, isLoading, isError, refetch } = query
  const items = data?.items ?? []
  // D2：同款多市场合并成一行 + 市场徽标（与全市场视图 D1 同轴对称）。
  const groups = useMemo(() => groupPublisherByApp(items), [items])
  // 点击行开详情抽屉（与 market 视图对称）；商店详情按需富化，抽屉内可再跳看板详情页。
  const [selected, setSelected] = useState<GroupedPublisherNewcomer | null>(null)

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
                  onClick={() => setSelected(gr)}
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
                          {g.release_date && (
                            <span className="ml-1.5 text-[10px] text-secondary">· {t.newcomers.appstoreReleasedAt(g.release_date)}</span>
                          )}
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
      {selected && <PublisherNewcomerDrawer group={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
