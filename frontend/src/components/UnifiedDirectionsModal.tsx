import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X, Sparkles, Layers, RefreshCw, Loader2, Copy, Download } from 'lucide-react'
import { materialsApi } from '../lib/api'
import type { AdaptModel } from '../lib/api'
import { Select } from './Select'
import { OwnProductPicker } from './OwnProductPicker'
import { unifiedToMarkdown, downloadText, type UnifiedData } from '../lib/markdown'
import { useT } from '../i18n'

interface Props {
  open: boolean
  materialIds: number[]
  onClose: () => void
}

const MODELS: AdaptModel[] = ['claude-sonnet-4.5', 'claude-opus-4.7']

export function UnifiedDirectionsModal({ open, materialIds, onClose }: Props) {
  const t = useT()
  const tu = t.materialAnalysis.unified
  // ⚠️ 所有 hooks 必须在 early return 之前（drawer 同款约束，见 feedback-react-hooks-early-return）
  const [ourProduct, setOurProduct] = useState('')
  const [productId, setProductId] = useState<number | null>(null)
  const [model, setModel] = useState<AdaptModel>('claude-sonnet-4.5')
  const [result, setResult] = useState<UnifiedData | null>(null)
  const [resultMeta, setResultMeta] = useState<{ cost: number; model: string } | null>(null)

  // 预估成本（干跑，不烧配额）。queryKey 只含 ids+model：成本不依赖产品 brief，
  // 故打开弹窗即可看到金额，无需先填写。
  const estimate = useQuery({
    queryKey: ['unifiedEstimate', materialIds, model],
    queryFn: () => materialsApi.unifiedDirectionsEstimate(materialIds, ourProduct, model),
    enabled: open && materialIds.length >= 2,
    staleTime: 60_000,
  })

  const gen = useMutation({
    mutationFn: () => materialsApi.unifiedDirections(materialIds, ourProduct, model),
    onSuccess: res => {
      setResult(res.data as UnifiedData)
      setResultMeta({ cost: res.cost_usd, model: res.model })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || tu.failed)
    },
  })

  if (!open) return null

  const handleGenerate = () => {
    if (!ourProduct.trim()) { toast.error(tu.productRequired); return }
    gen.mutate()
  }
  const reset = () => { setResult(null); setResultMeta(null) }
  const close = () => { reset(); onClose() }

  const buildMarkdown = () =>
    result ? unifiedToMarkdown(result, {
      cost: resultMeta?.cost,
      model: resultMeta?.model,
      productBrief: ourProduct,
    }) : ''

  const handleCopy = async () => {
    const md = buildMarkdown()
    if (!md) return
    try {
      await navigator.clipboard.writeText(md)
      toast.success(tu.copied)
    } catch {
      toast.error(tu.copyFailed)
    }
  }

  const handleDownload = () => {
    const md = buildMarkdown()
    if (!md) return
    downloadText(`unified-directions-${new Date().toISOString().slice(0, 10)}.md`, md)
  }

  const est = estimate.data
  const cp = result?.common_patterns

  const modelOptions = MODELS.map(m => ({
    value: m,
    label: m === 'claude-sonnet-4.5' ? tu.modelSonnet : tu.modelOpus,
  }))

  return (
    <>
      <div className="fixed inset-0 z-40 bg-base/70 backdrop-blur-sm" onClick={close} />
      <div className="fixed inset-0 z-50 grid place-items-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-2xl max-h-[86vh] overflow-y-auto rounded-2xl border border-strong bg-surface shadow-2xl"
          role="dialog" aria-label={tu.title}>
          {/* header */}
          <header className="sticky top-0 z-10 bg-surface/95 backdrop-blur border-b border-default px-5 py-4 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 eyebrow text-accent">
                <Layers size={12} /> CROSS-MATERIAL
              </div>
              <div className="mt-1 font-display font-bold text-primary text-base leading-tight">{tu.title}</div>
              <p className="text-xs text-muted mt-0.5">{tu.subtitle}</p>
            </div>
            <button onClick={close} className="p-1.5 text-muted hover:text-primary transition-colors" aria-label={tu.cancel}>
              <X size={18} />
            </button>
          </header>

          <div className="px-5 py-4 space-y-4">
            {/* selected count */}
            <div className="flex items-center gap-2 text-xs text-secondary">
              <Sparkles size={13} className="text-accent" />
              {tu.selectedCount(materialIds.length)}
            </div>

            {!result ? (
              <>
                {/* product picker + brief */}
                <OwnProductPicker
                  selectedId={productId}
                  onPick={(id, brief) => { setProductId(id); if (brief !== null) setOurProduct(brief) }}
                  autoSelectDefault
                />
                <div>
                  <label className="block text-xs text-muted mb-1.5">{tu.productLabel}</label>
                  <textarea value={ourProduct} onChange={e => { setOurProduct(e.target.value); setProductId(null) }}
                    placeholder={tu.productPlaceholder} rows={4}
                    className="w-full rounded-lg border border-default bg-base/40 px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent transition-colors resize-y" />
                </div>

                {/* model + estimate */}
                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-56">
                    <label className="block text-xs text-muted mb-1.5">{tu.modelLabel}</label>
                    <Select aria-label={tu.modelLabel} value={model}
                      onChange={v => setModel(v as AdaptModel)} options={modelOptions} />
                  </div>
                  <div className="flex-1 min-w-[140px] rounded-lg border border-default bg-base/40 px-3 py-2">
                    <div className="text-[10px] eyebrow text-muted">{tu.estimateLabel}</div>
                    <div className="font-data text-sm text-primary">
                      {estimate.isFetching || !est ? (
                        <span className="text-muted">{tu.estimating}</span>
                      ) : (
                        <span className="text-accent">~${est.estimated_cost_usd.toFixed(4)}</span>
                      )}
                    </div>
                  </div>
                </div>
                <p className="text-[11px] text-muted">{tu.estimateNote}</p>

                {/* actions */}
                <div className="flex items-center justify-end gap-2 pt-1">
                  <button onClick={close}
                    className="px-3.5 py-2 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary transition-colors">
                    {tu.cancel}
                  </button>
                  <button onClick={handleGenerate} disabled={gen.isPending}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-data bg-accent/15 border border-accent/40 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
                    {gen.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                    {gen.isPending ? tu.generating : tu.generate}
                  </button>
                </div>
              </>
            ) : (
              <>
                {/* result: cost line */}
                {resultMeta && (
                  <div className="font-data text-xs text-accent">
                    {tu.resultCost(resultMeta.cost.toFixed(4), resultMeta.model)}
                  </div>
                )}

                {/* common patterns */}
                {cp && (
                  <div className="rounded-lg border border-default bg-base/40 p-4 space-y-2">
                    <div className="eyebrow text-accent flex items-center gap-1.5"><Layers size={11} />{tu.commonPatterns}</div>
                    {cp.shared_structure && <Field label={tu.sharedStructure} value={cp.shared_structure} />}
                    {cp.shared_pacing && <Field label={tu.sharedPacing} value={cp.shared_pacing} />}
                    {cp.shared_hooks && cp.shared_hooks.length > 0 && (
                      <div>
                        <div className="text-[11px] text-muted mb-1">{tu.sharedHooks}</div>
                        <div className="flex flex-wrap gap-1.5">
                          {cp.shared_hooks.map((h, i) => (
                            <span key={i} className="text-[11px] px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">{h}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {cp.notable_variations && <Field label={tu.notableVariations} value={cp.notable_variations} />}
                  </div>
                )}

                {/* directions */}
                {result.directions && result.directions.length > 0 && (
                  <div className="space-y-3">
                    <div className="eyebrow text-muted">{tu.directions}</div>
                    {result.directions.map((d, i) => (
                      <div key={i} className="rounded-lg border border-default bg-surface p-4 space-y-2">
                        <div className="flex items-center gap-2">
                          <span className="font-data text-accent text-xs">{String(i + 1).padStart(2, '0')}</span>
                          <span className="font-display font-bold text-primary text-sm">{d.name}</span>
                        </div>
                        {d.concept && <p className="text-xs text-secondary leading-relaxed">{d.concept}</p>}
                        {d.opening_3sec && <Field label="0-3s" value={d.opening_3sec} />}
                        {d.borrows_from_refs && <Field label={tu.sharedStructure} value={d.borrows_from_refs} />}
                        {d.fit_to_self_product && <Field label={tu.productLabel} value={d.fit_to_self_product} />}
                        {d.key_hooks && d.key_hooks.length > 0 && (
                          <div className="flex flex-wrap gap-1.5">
                            {d.key_hooks.map((h, j) => (
                              <span key={j} className="inline-flex items-center gap-1 text-[10px] font-data px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">
                                <span className="text-accent/70">{h.ts_est}</span>{h.kind}
                              </span>
                            ))}
                          </div>
                        )}
                        {d.ending_cta && <Field label="CTA" value={d.ending_cta} />}
                        {d.risk_notes && <p className="text-[11px] text-amber-400/80 leading-relaxed">⚠ {d.risk_notes}</p>}
                      </div>
                    ))}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <button onClick={handleCopy}
                    className="flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary transition-colors">
                    <Copy size={13} /> {tu.copyMd}
                  </button>
                  <button onClick={handleDownload}
                    className="flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary transition-colors">
                    <Download size={13} /> {tu.downloadMd}
                  </button>
                  <button onClick={reset}
                    className="flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs text-secondary border border-default hover:border-strong hover:text-primary transition-colors ml-auto">
                    <RefreshCw size={13} /> {tu.regenerate}
                  </button>
                  <button onClick={close}
                    className="px-4 py-2 rounded-lg text-xs font-data bg-accent/15 border border-accent/40 text-accent hover:bg-accent/25 transition-colors">
                    {tu.cancel}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-xs">
      <span className="text-muted">{label}：</span>
      <span className="text-secondary leading-relaxed">{value}</span>
    </div>
  )
}
