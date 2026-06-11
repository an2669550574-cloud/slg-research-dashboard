import { useState } from 'react'
import { useQuery, useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { newcomersApi, publishersApi } from '../lib/api'
import { formatRevenue, formatNumber } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Download as DownloadIcon, Sparkles, Info, FilePlus2, Globe2, Building2, Store, RefreshCw, Star } from 'lucide-react'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { useLocalStorageState } from '../lib/hooks'
import type { NewcomerItem, PublisherNewcomersOut } from '../lib/types'

export default function NewReleases() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  // 与排行榜共享市场选择，跨页切换一致
  const [country, setCountry] = useLocalStorageState<Country>('slg.country', 'US')
  const [platform, setPlatform] = useLocalStorageState<Platform>('slg.platform', 'ios')
  // 全市场新面孔（TopN 空降）/ 厂商新品（已建档主体 × 任意名次首次出现）
  const [view, setView] = useState<'market' | 'publisher'>('market')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['newcomers', country, platform],
    queryFn: () => newcomersApi.get({ country, platform }),
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
    mutationFn: (g: NewcomerItem) => publishersApi.create({
      name: g.publisher?.trim() || g.name,
      is_slg: true,
      brief: t.newcomers.triageBrief(g.name, `${g.country}/${g.platform}`),
      app_ids: [{ app_id: g.app_id, note: g.name }],
    }),
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ['newcomers'] })
      qc.invalidateQueries({ queryKey: ['publishers'] })
      toast.success(t.newcomers.triaged(e.name))
    },
  })
  const handleTriage = (g: NewcomerItem) => {
    if (!window.confirm(t.newcomers.triageConfirm(g.publisher?.trim() || g.name))) return
    triageMut.mutate(g)
  }

  const items = data?.items ?? []
  const comboKey = `${country}/${platform}`
  const asOf = data?.as_of_by_combo?.[comboKey]
  const noBaseline = (data?.combos_without_baseline ?? []).includes(comboKey)

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
            downloadCsv(`newcomers-${country}-${platform}-${date}.csv`, items, [
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
            {data && <span>{t.newcomers.windowHint(data.window, data.topn)}</span>}
            {asOf && <span>· {t.newcomers.asOf(asOf)}</span>}
            {!isLoading && !noBaseline && items.length > 0 && (
              <span className="text-accent">· {t.newcomers.countSuffix(items.length)}</span>
            )}
          </>
        ) : (
          <>
            {pubQuery.data && <span>{t.newcomers.publisherWindowHint(pubQuery.data.window)}</span>}
            {!pubQuery.isLoading && (pubQuery.data?.items.length ?? 0) > 0 && (
              <span className="text-accent">· {t.newcomers.countSuffix(pubQuery.data!.items.length)}</span>
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
              {PLATFORMS.map(p => (
                <button
                  key={p}
                  onClick={() => setPlatform(p)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${platform === p ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {platformLabel(p)}
                </button>
              ))}
            </div>
            <div className="flex gap-1 bg-elevated rounded-lg p-1">
              {COUNTRIES.map(c => (
                <button
                  key={c}
                  onClick={() => setCountry(c)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${country === c ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
                >
                  {c}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {view === 'publisher' ? (
        <>
          <AppstoreReleasesSection />
          <PublisherNewcomersTable query={pubQuery} />
        </>
      ) : (
      <div className="bg-surface border border-default rounded-xl overflow-hidden">
        {isError ? (
          <QueryError onRetry={() => refetch()} />
        ) : noBaseline && !isLoading ? (
          <div className="py-16 px-6 text-center text-muted text-sm">{t.newcomers.noBaseline}</div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px]">
                <thead>
                  <tr className="border-b border-default text-xs text-muted uppercase tracking-wider">
                    <th className="px-5 py-3 text-left w-12">{t.newcomers.rank}</th>
                    <th className="px-3 py-3 text-left">{t.newcomers.game}</th>
                    <th className="px-3 py-3 text-left w-36">SLG</th>
                    <th className="px-3 py-3 text-right">{t.newcomers.revenue}</th>
                    <th className="px-3 py-3 text-right w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-default">
                  {isLoading
                    ? Array.from({ length: 6 }).map((_, i) => (
                        <tr key={i} className="animate-pulse">
                          <td className="px-5 py-4"><div className="w-6 h-4 bg-elevated rounded" /></td>
                          <td className="px-3 py-4">
                            <div className="flex items-center gap-3">
                              <div className="w-10 h-10 bg-elevated rounded-xl" />
                              <div className="space-y-1.5">
                                <div className="w-32 h-3.5 bg-elevated rounded" />
                                <div className="w-20 h-3 bg-elevated rounded" />
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-4"><div className="w-20 h-4 bg-elevated rounded" /></td>
                          <td className="px-3 py-4"><div className="w-16 h-4 bg-elevated rounded ml-auto" /></td>
                          <td className="px-3 py-4"></td>
                        </tr>
                      ))
                    : items.map(g => (
                        <tr
                          key={`${g.country}/${g.platform}/${g.app_id}`}
                          className="hover:bg-elevated/50 cursor-pointer transition-colors"
                          onClick={() => navigate(`/game/${g.app_id}`)}
                        >
                          <td className="px-5 py-3.5">
                            <span className={`text-sm font-bold ${g.rank == null ? 'text-muted' : g.rank <= 3 ? 'text-yellow-400' : g.rank <= 10 ? 'text-primary' : 'text-muted'}`}>
                              #{g.rank ?? '—'}
                            </span>
                          </td>
                          <td className="px-3 py-3.5">
                            <div className="flex items-center gap-3">
                              <GameIcon src={g.icon_url} name={g.name ?? g.app_id} className="w-10 h-10 rounded-xl" />
                              <div>
                                <div className="text-sm font-medium text-primary flex items-center gap-1.5">
                                  <Sparkles size={13} className="text-accent shrink-0" />
                                  {g.name}
                                </div>
                                <div className="text-xs text-muted">{g.publisher}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-3.5">
                            {g.is_slg ? (
                              <span className="inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-brand-600/15 text-brand-500">
                                {t.newcomers.slgKnown}
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5">
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
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className="text-sm font-medium text-emerald-400">
                              {g.revenue == null ? <span className="text-muted">—</span> : formatRevenue(g.revenue)}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className="text-xs text-brand-500">{t.common.detail}</span>
                          </td>
                        </tr>
                      ))
                  }
                </tbody>
              </table>
            </div>
            {!isLoading && items.length === 0 && (
              <div className="py-16 text-center text-muted text-sm">{t.newcomers.empty}</div>
            )}
          </>
        )}
      </div>
      )}

      <div className="flex items-start gap-2 text-[11px] text-muted/80 leading-relaxed">
        <Info size={13} className="mt-0.5 shrink-0" />
        <span>{view === 'market' ? t.newcomers.note : t.newcomers.publisherNote}</span>
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
                  {it.storefronts.length > 0 && (
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

function PublisherNewcomersTable({ query }: { query: UseQueryResult<PublisherNewcomersOut> }) {
  const t = useT()
  const navigate = useNavigate()
  const { data, isLoading, isError, refetch } = query
  const items = data?.items ?? []

  return (
    <div className="bg-surface border border-default rounded-xl overflow-hidden">
      {isError ? (
        <QueryError onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="py-16 text-center text-muted text-sm">{t.common.loading}</div>
      ) : items.length === 0 ? (
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
              {items.map(g => (
                <tr
                  key={`${g.country}/${g.platform}/${g.app_id}`}
                  className="hover:bg-elevated/50 cursor-pointer transition-colors"
                  onClick={() => navigate(`/game/${g.app_id}`)}
                >
                  <td className="px-5 py-3.5">
                    <span className="inline-flex items-center gap-1.5 text-xs text-primary">
                      <Building2 size={12} className="text-accent shrink-0" />
                      <span className="truncate max-w-[150px]">{g.entity_name}</span>
                    </span>
                  </td>
                  <td className="px-3 py-3.5">
                    <div className="flex items-center gap-3">
                      <GameIcon src={g.icon_url} name={g.name ?? g.app_id} className="w-10 h-10 rounded-xl" />
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
                  <td className="px-3 py-3.5 text-xs text-secondary font-data">{g.country}/{g.platform}</td>
                  <td className="px-3 py-3.5 text-right">
                    <span className="text-sm font-bold text-primary">#{g.rank ?? '—'}</span>
                  </td>
                  <td className="px-3 py-3.5 text-right">
                    <span className="text-sm font-medium text-emerald-400">
                      {g.revenue == null ? <span className="text-muted">—</span> : formatRevenue(g.revenue)}
                    </span>
                  </td>
                  <td className="px-3 py-3.5 text-right text-xs text-muted font-data">{g.as_of}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
