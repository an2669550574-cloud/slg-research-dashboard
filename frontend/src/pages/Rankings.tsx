import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { gamesApi } from '../lib/api'
import { formatNumber, formatRevenue } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Loader2, RefreshCw, Search, Download as DownloadIcon } from 'lucide-react'
import { COUNTRIES, PLATFORMS, platformLabel, type Country, type Platform } from '../lib/markets'
import { GameIcon } from '../components/GameIcon'
import { QueryError } from '../components/QueryError'
import { PageHeader } from '../components/PageHeader'
import { useLocalStorageState } from '../lib/hooks'

const REFRESH_COOLDOWN_SEC = 30

export default function Rankings() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  const [country, setCountry] = useLocalStorageState<Country>('slg.country', 'US')
  const [platform, setPlatform] = useLocalStorageState<Platform>('slg.platform', 'ios')
  // 商店没有 SLG 子类，策略畅销榜混入非 SLG；默认只看 SLG 竞品，可切「全部策略」
  const [slgOnly, setSlgOnly] = useLocalStorageState<boolean>('slg.slgOnly', true)
  const [search, setSearch] = useState('')
  const [cooldownLeft, setCooldownLeft] = useState(0)
  const cooling = cooldownLeft > 0

  const { data: rankings = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['rankings', country, platform],
    queryFn: () => gamesApi.rankings(country, platform),
  })

  // 强制刷榜:绕过 L1+L2 缓存,会消耗一次 ST 配额。与 Dashboard 同款 cooldown
  // (30s)防止误连点;queryKey 与 Dashboard/Compare 共享 ['rankings',c,p] 会自动
  // 同步全站的"今日榜"数据。
  const refreshMut = useMutation({
    mutationFn: () => gamesApi.refreshRankings(country, platform),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rankings', country, platform] })
      qc.invalidateQueries({ queryKey: ['quota'] })
      toast.success(t.common.refreshed)
      setCooldownLeft(REFRESH_COOLDOWN_SEC)
    },
  })
  useEffect(() => {
    if (cooldownLeft <= 0) return
    const id = setTimeout(() => setCooldownLeft(n => n - 1), 1000)
    return () => clearTimeout(id)
  }, [cooldownLeft])

  const board = slgOnly ? rankings.filter(g => g.is_slg) : rankings
  const filtered = board.filter(g =>
    (g.name ?? '').toLowerCase().includes(search.toLowerCase()) ||
    (g.publisher ?? '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader eyebrow="Live Board" title={t.rankings.title} subtitle={t.rankings.subtitle}>
        <button
          onClick={() => {
            if (filtered.length === 0) { toast.error(t.common.noExportData); return }
            const date = new Date().toISOString().slice(0, 10)
            downloadCsv(`rankings-${country}-${platform}-${date}.csv`, filtered, [
              { header: t.csv.rank, get: r => r.rank },
              { header: t.csv.appId, get: r => r.app_id },
              { header: t.csv.gameName, get: r => r.name },
              { header: t.csv.publisher, get: r => r.publisher },
              { header: t.csv.revenueUsd, get: r => r.revenue },
              { header: t.csv.downloadsToday, get: r => r.downloads },
              { header: t.csv.date, get: r => r.date },
            ])
            toast.success(t.common.exported(filtered.length))
          }}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
        >
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
        <button
          onClick={() => refreshMut.mutate()}
          disabled={refreshMut.isPending || cooling}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed glow-accent transition-all"
        >
          {refreshMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {cooling ? t.common.refreshCooldown(cooldownLeft) : t.common.refresh}
        </button>
      </PageHeader>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[180px] max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder={t.rankings.searchPlaceholder}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-elevated border border-default rounded-lg pl-9 pr-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"
          />
        </div>
        <div className="flex gap-1 bg-elevated rounded-lg p-1">
          {([true, false] as const).map(v => (
            <button
              key={String(v)}
              onClick={() => setSlgOnly(v)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${slgOnly === v ? 'bg-brand-600 text-white' : 'text-secondary hover:text-primary'}`}
            >
              {v ? t.rankings.slgOnly : t.rankings.allStrategy}
            </button>
          ))}
        </div>
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
        ) : (
        <>
        <div className="overflow-x-auto">
        <table className="w-full min-w-[600px]">
          <thead>
            <tr className="border-b border-default text-xs text-muted uppercase tracking-wider">
              <th className="px-5 py-3 text-left w-12">{t.rankings.rank}</th>
              <th className="px-3 py-3 text-left">{t.rankings.game}</th>
              <th className="px-3 py-3 text-right">{t.rankings.todayRevenue}</th>
              <th className="px-3 py-3 text-right">{t.rankings.todayDownloads}</th>
              <th className="px-3 py-3 text-right w-10"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-default">
            {isLoading
              ? Array.from({ length: 10 }).map((_, i) => (
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
                    <td className="px-3 py-4"><div className="w-20 h-4 bg-elevated rounded ml-auto" /></td>
                    <td className="px-3 py-4"><div className="w-16 h-4 bg-elevated rounded ml-auto" /></td>
                    <td className="px-3 py-4"></td>
                  </tr>
                ))
              : filtered.map(g => (
                  <tr
                    key={g.app_id}
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
                          <div className="text-sm font-medium text-primary">{g.name}</div>
                          <div className="text-xs text-muted">{g.publisher}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3.5 text-right">
                      <span className="text-sm font-medium text-emerald-400">{g.revenue == null ? <span className="text-muted">—</span> : formatRevenue(g.revenue)}</span>
                    </td>
                    <td className="px-3 py-3.5 text-right">
                      <span className="text-sm text-secondary">{g.downloads == null ? <span className="text-muted">—</span> : formatNumber(g.downloads)}</span>
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
        {!isLoading && filtered.length === 0 && (
          <div className="py-16 text-center text-muted text-sm">{t.common.noResult}</div>
        )}
        </>
        )}
      </div>
    </div>
  )
}
