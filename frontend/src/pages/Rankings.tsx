import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { gamesApi } from '../lib/api'
import { formatNumber, formatRevenue } from '../lib/utils'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Search, Download as DownloadIcon } from 'lucide-react'

const COUNTRIES = ['US', 'GB', 'DE', 'JP', 'KR', 'AU', 'CA', 'FR']
const PLATFORMS = ['ios', 'android']

export default function Rankings() {
  const navigate = useNavigate()
  const t = useT()
  const [country, setCountry] = useState('US')
  const [platform, setPlatform] = useState('ios')
  const [search, setSearch] = useState('')

  const { data: rankings = [], isLoading } = useQuery({
    queryKey: ['rankings', country, platform],
    queryFn: () => gamesApi.rankings(country, platform),
  })

  const filtered = rankings.filter((g: any) =>
    g.name.toLowerCase().includes(search.toLowerCase()) ||
    (g.publisher || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">{t.rankings.title}</h1>
          <p className="text-gray-500 text-sm mt-0.5">{t.rankings.subtitle}</p>
        </div>
        <button
          onClick={() => {
            if (filtered.length === 0) { toast.error(t.common.noExportData); return }
            const date = new Date().toISOString().slice(0, 10)
            downloadCsv(`rankings-${country}-${platform}-${date}.csv`, filtered, [
              { header: t.csv.rank, get: (r: any) => r.rank },
              { header: t.csv.appId, get: (r: any) => r.app_id },
              { header: t.csv.gameName, get: (r: any) => r.name },
              { header: t.csv.publisher, get: (r: any) => r.publisher },
              { header: t.csv.revenueUsd, get: (r: any) => r.revenue },
              { header: t.csv.downloadsToday, get: (r: any) => r.downloads },
              { header: t.csv.date, get: (r: any) => r.date },
            ])
            toast.success(t.common.exported(filtered.length))
          }}
          className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
        >
          <DownloadIcon size={14} />
          {t.common.export}
        </button>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder={t.rankings.searchPlaceholder}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-brand-500"
          />
        </div>
        <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
          {PLATFORMS.map(p => (
            <button
              key={p}
              onClick={() => setPlatform(p)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${platform === p ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {p === 'ios' ? 'iOS' : 'Android'}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
          {COUNTRIES.map(c => (
            <button
              key={c}
              onClick={() => setCountry(c)}
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${country === c ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-5 py-3 text-left w-12">{t.rankings.rank}</th>
              <th className="px-3 py-3 text-left">{t.rankings.game}</th>
              <th className="px-3 py-3 text-right">{t.rankings.todayRevenue}</th>
              <th className="px-3 py-3 text-right">{t.rankings.todayDownloads}</th>
              <th className="px-3 py-3 text-right w-10"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {isLoading
              ? Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="animate-pulse">
                    <td className="px-5 py-4"><div className="w-6 h-4 bg-gray-800 rounded" /></td>
                    <td className="px-3 py-4">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 bg-gray-800 rounded-xl" />
                        <div className="space-y-1.5">
                          <div className="w-32 h-3.5 bg-gray-800 rounded" />
                          <div className="w-20 h-3 bg-gray-800 rounded" />
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-4"><div className="w-20 h-4 bg-gray-800 rounded ml-auto" /></td>
                    <td className="px-3 py-4"><div className="w-16 h-4 bg-gray-800 rounded ml-auto" /></td>
                    <td className="px-3 py-4"></td>
                  </tr>
                ))
              : filtered.map((g: any, idx: number) => (
                  <tr
                    key={g.app_id}
                    className="hover:bg-gray-800/50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/game/${g.app_id}`)}
                  >
                    <td className="px-5 py-3.5">
                      <span className={`text-sm font-bold ${g.rank <= 3 ? 'text-yellow-400' : g.rank <= 10 ? 'text-gray-300' : 'text-gray-600'}`}>
                        #{g.rank}
                      </span>
                    </td>
                    <td className="px-3 py-3.5">
                      <div className="flex items-center gap-3">
                        {g.icon_url
                          ? <img src={g.icon_url} alt={g.name} className="w-10 h-10 rounded-xl object-cover" />
                          : <div className="w-10 h-10 rounded-xl bg-gray-700 flex items-center justify-center text-gray-400 text-xs">?</div>
                        }
                        <div>
                          <div className="text-sm font-medium text-white">{g.name}</div>
                          <div className="text-xs text-gray-500">{g.publisher}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3.5 text-right">
                      <span className="text-sm font-medium text-emerald-400">{formatRevenue(g.revenue)}</span>
                    </td>
                    <td className="px-3 py-3.5 text-right">
                      <span className="text-sm text-gray-300">{formatNumber(g.downloads)}</span>
                    </td>
                    <td className="px-3 py-3.5 text-right">
                      <span className="text-xs text-brand-500">{t.common.detail}</span>
                    </td>
                  </tr>
                ))
            }
          </tbody>
        </table>
        {!isLoading && filtered.length === 0 && (
          <div className="py-16 text-center text-gray-600 text-sm">{t.common.noResult}</div>
        )}
      </div>
    </div>
  )
}
