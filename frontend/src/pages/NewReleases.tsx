import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { newcomersApi } from '../lib/api'
import { formatRevenue } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Download as DownloadIcon, Sparkles, Info } from 'lucide-react'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { useLocalStorageState } from '../lib/hooks'

export default function NewReleases() {
  const navigate = useNavigate()
  const t = useT()
  // 与排行榜共享市场选择，跨页切换一致
  const [country, setCountry] = useLocalStorageState<Country>('slg.country', 'US')
  const [platform, setPlatform] = useLocalStorageState<Platform>('slg.platform', 'ios')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['newcomers', country, platform],
    queryFn: () => newcomersApi.get({ country, platform }),
  })

  const items = data?.items ?? []
  const comboKey = `${country}/${platform}`
  const asOf = data?.as_of_by_combo?.[comboKey]
  const noBaseline = (data?.combos_without_baseline ?? []).includes(comboKey)

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="New Releases" title={t.newcomers.title} subtitle={t.newcomers.subtitle}>
        <button
          onClick={() => {
            if (items.length === 0) { toast.error(t.common.noExportData); return }
            const date = new Date().toISOString().slice(0, 10)
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
        {data && <span>{t.newcomers.windowHint(data.window, data.topn)}</span>}
        {asOf && <span>· {t.newcomers.asOf(asOf)}</span>}
        {!isLoading && !noBaseline && items.length > 0 && (
          <span className="text-accent">· {t.newcomers.countSuffix(items.length)}</span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
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
      </div>

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
                              <span className="inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-amber-500/15 text-amber-500">
                                {t.newcomers.slgUnknown}
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

      <div className="flex items-start gap-2 text-[11px] text-muted/80 leading-relaxed">
        <Info size={13} className="mt-0.5 shrink-0" />
        <span>{t.newcomers.note}</span>
      </div>
    </div>
  )
}
