import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Upload, FileText, Film, Image as ImageIcon, Sparkles, Trash2 } from 'lucide-react'
import { productsApi } from '../lib/api'
import { useT } from '../i18n'
import type { OwnProductMaterial } from '../lib/types'

interface Props {
  productId: number
  /** AI 解析出的 brief 草稿回填到父表单的文本框 */
  onBriefDraft: (brief: string) => void
}

const TYPE_ICON = { video: Film, image: ImageIcon, text: FileText } as const

/** 「我方产品」编辑态的素材区：上传宣传片/截图、粘贴商店描述，
 *  再让 AI 综合这些素材反推产品特点、生成 brief 草稿回填文本框。
 *  所有 hooks 写在任何 early return 之前（抽屉/弹层类硬规则）。 */
export function ProductMaterials({ productId, onBriefDraft }: Props) {
  const t = useT()
  const tp = t.productsManage
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [textOpen, setTextOpen] = useState(false)
  const [textTitle, setTextTitle] = useState('')
  const [textBody, setTextBody] = useState('')
  const [uploadPct, setUploadPct] = useState<number | null>(null)

  const { data: materials = [] } = useQuery({
    queryKey: ['ownProductMaterials', productId],
    queryFn: () => productsApi.materials(productId),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['ownProductMaterials', productId] })

  const uploadMut = useMutation({
    mutationFn: (form: FormData) => productsApi.uploadMaterial(productId, form, setUploadPct),
    onSuccess: () => invalidate(),
    onSettled: () => setUploadPct(null),
  })
  const textMut = useMutation({
    mutationFn: (data: { title?: string; text_content: string }) => productsApi.addTextMaterial(productId, data),
    onSuccess: () => { invalidate(); setTextOpen(false); setTextTitle(''); setTextBody('') },
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => productsApi.deleteMaterial(productId, id),
    onSuccess: () => { invalidate(); toast.success(tp.materialDeleted) },
  })
  const analyzeMut = useMutation({
    mutationFn: () => productsApi.analyze(productId),
    onSuccess: (r) => { onBriefDraft(r.brief); toast.success(tp.analyzeOk(r.material_count, r.cost_usd)) },
  })

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const fd = new FormData()
    fd.append('file', f)
    uploadMut.mutate(fd)
    e.target.value = ''  // 允许重传同名文件
  }

  const submitText = () => {
    if (!textBody.trim()) { toast.error(tp.textRequired); return }
    textMut.mutate({ title: textTitle.trim() || undefined, text_content: textBody })
  }

  const typeLabel = (a: OwnProductMaterial['asset_type']) =>
    a === 'video' ? tp.typeVideo : a === 'image' ? tp.typeImage : tp.typeText
  const uploading = uploadPct !== null
  const inputClass = "bg-elevated border border-default rounded-lg px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-brand-500"

  return (
    <div className="border-t border-default pt-4 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-primary">{tp.materialsTitle}</h4>
        <p className="text-xs text-muted mt-0.5">{tp.materialsHint}</p>
      </div>

      {materials.length > 0 && (
        <div className="space-y-1.5">
          {materials.map(m => {
            const Icon = TYPE_ICON[m.asset_type]
            const name = m.title || m.file_name || (m.text_content?.slice(0, 60)) || '—'
            return (
              <div key={m.id} className="flex items-center gap-2.5 bg-elevated border border-default rounded-lg px-3 py-2">
                {m.asset_type === 'image' && m.preview_url ? (
                  <img src={m.preview_url} alt="" className="w-9 h-9 rounded object-cover shrink-0" />
                ) : (
                  <span className="w-9 h-9 rounded bg-surface flex items-center justify-center shrink-0">
                    <Icon size={16} className="text-muted" />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-primary truncate">{name}</div>
                  <div className="text-[10px] text-muted">{typeLabel(m.asset_type)}</div>
                </div>
                <button type="button" onClick={() => { if (confirm(tp.confirmDeleteMaterial)) deleteMut.mutate(m.id) }}
                  disabled={deleteMut.isPending} title={t.common.delete}
                  className="p-1.5 text-muted hover:text-red-400 transition-colors shrink-0"><Trash2 size={14} /></button>
              </div>
            )
          })}
        </div>
      )}

      {textOpen && (
        <div className="space-y-2 bg-elevated border border-default rounded-lg p-3">
          <input value={textTitle} onChange={e => setTextTitle(e.target.value)}
            placeholder={tp.textTitlePlaceholder} className={`w-full ${inputClass}`} />
          <textarea rows={4} value={textBody} onChange={e => setTextBody(e.target.value)}
            placeholder={tp.textPlaceholder} className={`w-full resize-y ${inputClass}`} />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setTextOpen(false)}
              className="px-3 py-1.5 text-sm text-secondary hover:text-primary">{t.common.cancel}</button>
            <button type="button" onClick={submitText} disabled={textMut.isPending}
              className="px-3 py-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 rounded-lg text-sm text-white transition-colors">{tp.save}</button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileRef} type="file" accept="video/*,image/*" onChange={handleFile} className="hidden" />
        <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-secondary border border-default hover:border-brand-500 hover:text-primary disabled:opacity-50 transition-colors">
          <Upload size={14} /> {uploading ? `${tp.uploading} ${uploadPct}%` : tp.uploadMedia}
        </button>
        <button type="button" onClick={() => setTextOpen(o => !o)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-secondary border border-default hover:border-brand-500 hover:text-primary transition-colors">
          <FileText size={14} /> {tp.addText}
        </button>
        <button type="button"
          onClick={() => { if (materials.length === 0) { toast.error(tp.analyzeNeedMaterial); return } analyzeMut.mutate() }}
          disabled={analyzeMut.isPending || materials.length === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 ml-auto rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent disabled:opacity-50 disabled:cursor-not-allowed transition-all">
          <Sparkles size={14} /> {analyzeMut.isPending ? tp.analyzing : tp.analyze}
        </button>
      </div>
    </div>
  )
}
