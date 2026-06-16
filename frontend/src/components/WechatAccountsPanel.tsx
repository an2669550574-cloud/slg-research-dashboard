import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Search, Plus, Trash2, Loader2, Check, ChevronRight } from 'lucide-react'
import { wechatAccountsApi } from '../lib/api'
import type { WechatAccountCandidate } from '../lib/types'
import { useT } from '../i18n'

/** 看板维护订阅公众号：搜名字→选候选→订阅；列表可启停/移除。新品监测日报按启用号搜文章。 */
export function WechatAccountsPanel() {
  const t = useT()
  const tt = t.wechatAccounts
  const qc = useQueryClient()
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<WechatAccountCandidate[] | null>(null)
  // 已订阅列表折叠：用户未手动操作前，按数量自动决定（多则默认收起，免得页面太长）
  const [listOpenOverride, setListOpenOverride] = useState<boolean | null>(null)

  const { data: accounts = [] } = useQuery({
    queryKey: ['wechatAccounts'],
    queryFn: wechatAccountsApi.list,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['wechatAccounts'] })

  const searchMut = useMutation({
    mutationFn: (q: string) => wechatAccountsApi.search(q),
    onSuccess: setCandidates,
  })
  const createMut = useMutation({
    mutationFn: wechatAccountsApi.create,
    onSuccess: (acc) => { invalidate(); toast.success(tt.added(acc.name)); setCandidates(null); setQuery('') },
  })
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => wechatAccountsApi.setEnabled(id, enabled),
    onSuccess: invalidate,
  })
  const removeMut = useMutation({
    mutationFn: (id: number) => wechatAccountsApi.remove(id),
    onSuccess: () => { invalidate(); toast.success(tt.removed) },
  })

  const subscribed = new Set(accounts.map(a => a.fakeid))
  const runSearch = () => { const q = query.trim(); if (q) searchMut.mutate(q) }
  const listOpen = listOpenOverride ?? accounts.length <= 8

  return (
    <div className="bg-surface border border-default rounded-xl p-4 space-y-3">
      <div>
        <h3 className="text-sm font-medium text-primary">{tt.title}</h3>
        <p className="text-[11px] text-muted mt-0.5">{tt.hint}</p>
      </div>

      {/* 搜索行 */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') runSearch() }}
            placeholder={tt.searchPlaceholder}
            className="w-full pl-8 pr-3 py-2 rounded-lg bg-elevated border border-default text-xs text-primary placeholder:text-muted focus:border-strong outline-none"
          />
        </div>
        <button
          onClick={runSearch}
          disabled={searchMut.isPending || !query.trim()}
          className="px-3.5 py-2 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors disabled:opacity-50 flex items-center gap-1.5"
        >
          {searchMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
          {searchMut.isPending ? tt.searching : tt.search}
        </button>
      </div>

      {/* 候选（搜索结果）*/}
      {searchMut.isError && <div className="text-[11px] text-amber-400">{tt.disabled}</div>}
      {candidates !== null && (
        candidates.length === 0
          ? <div className="text-[11px] text-muted">{tt.noCandidates}</div>
          : <div className="space-y-1.5">
              {candidates.map(c => {
                const has = subscribed.has(c.fakeid)
                return (
                  <div key={c.fakeid} className="flex items-center gap-3 px-2.5 py-1.5 rounded-lg bg-elevated/60">
                    <div className="min-w-0 flex-1">
                      <div className="text-xs text-primary truncate">{c.nickname}</div>
                      {c.alias && <div className="text-[10px] text-muted font-data truncate">{c.alias}</div>}
                    </div>
                    <button
                      onClick={() => !has && createMut.mutate({ fakeid: c.fakeid, name: c.nickname })}
                      disabled={has || createMut.isPending}
                      className="shrink-0 px-2.5 py-1 rounded text-[11px] flex items-center gap-1 border transition-colors disabled:opacity-60 border-default text-secondary hover:border-strong hover:text-primary"
                    >
                      {has ? <><Check className="w-3 h-3" />{tt.already}</> : <><Plus className="w-3 h-3" />{tt.subscribe}</>}
                    </button>
                  </div>
                )
              })}
            </div>
      )}

      {/* 已订阅列表（可折叠） */}
      <div className="pt-1">
        {accounts.length === 0 ? (
          <div className="text-[11px] text-muted">{tt.empty}</div>
        ) : (
          <>
            <button
              onClick={() => setListOpenOverride(!listOpen)}
              aria-expanded={listOpen}
              className="flex items-center gap-1.5 w-full text-left text-[11px] text-muted hover:text-secondary transition-colors py-0.5"
            >
              <ChevronRight className={`w-3.5 h-3.5 shrink-0 transition-transform ${listOpen ? 'rotate-90' : ''}`} />
              {tt.subscribedCount(accounts.length)}
            </button>
            {listOpen && (
              <div className="space-y-1.5 mt-1.5">
                {accounts.map(a => (
                  <div key={a.id} className="flex items-center gap-3 px-2.5 py-1.5 rounded-lg border border-default">
                    <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.enabled ? 'bg-emerald-400' : 'bg-muted'}`} />
                    <span className={`text-xs flex-1 truncate ${a.enabled ? 'text-primary' : 'text-muted line-through'}`}>{a.name}</span>
                    <button
                      onClick={() => toggleMut.mutate({ id: a.id, enabled: !a.enabled })}
                      disabled={toggleMut.isPending}
                      className="shrink-0 text-[11px] text-secondary hover:text-primary px-1.5 py-0.5"
                    >
                      {a.enabled ? tt.disable : tt.enable}
                    </button>
                    <button
                      onClick={() => { if (confirm(tt.removeConfirm(a.name))) removeMut.mutate(a.id) }}
                      className="shrink-0 text-muted hover:text-red-400 p-1"
                      aria-label={tt.remove}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
