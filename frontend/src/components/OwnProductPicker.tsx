import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Settings2 } from 'lucide-react'
import { productsApi } from '../lib/api'
import { useT } from '../i18n'
import { Select } from './Select'

interface Props {
  /** 当前选中的产品 id；null = 手动输入（不绑定任何档案） */
  selectedId: number | null
  /** brief 为 null 表示选了"手动输入"，调用方应保留文本框现值不覆盖 */
  onPick: (id: number | null, brief: string | null) => void
  /** 首次拿到产品列表且尚未选择时，自动带入默认产品（迁移面板默认行为） */
  autoSelectDefault?: boolean
  className?: string
}

/** 创意迁移面板顶部的「自家产品」选择器：从已存档案里选一条带入 brief，
 *  省去每次手输。选「手动输入」则解除绑定、文本框可自由编辑。 */
export function OwnProductPicker({ selectedId, onPick, autoSelectDefault, className }: Props) {
  const t = useT()
  const tp = t.ownProductPicker
  const { data: products } = useQuery({ queryKey: ['ownProducts'], queryFn: productsApi.list })

  // 仅自动带入一次：避免用户切到"手动输入"后又被默认值抢回去
  const didAuto = useRef(false)
  useEffect(() => {
    if (!autoSelectDefault || didAuto.current || selectedId !== null || !products?.length) return
    const def = products.find(p => p.is_default) ?? products[0]
    if (def) {
      didAuto.current = true
      onPick(def.id, def.brief)
    }
  }, [products, autoSelectDefault, selectedId, onPick])

  const options = [
    { value: '', label: tp.manual },
    ...(products ?? []).map(p => ({
      value: String(p.id),
      label: p.is_default ? `${p.name} · ${t.productsManage.defaultBadge}` : p.name,
    })),
  ]

  const handleChange = (v: string) => {
    if (v === '') { onPick(null, null); return }
    const p = products?.find(pr => String(pr.id) === v)
    if (p) onPick(p.id, p.brief)
  }

  return (
    <div className={className}>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-xs text-muted">{tp.label}</label>
        <Link to="/products" target="_blank"
          className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-accent transition-colors">
          <Settings2 size={11} /> {tp.manage}
        </Link>
      </div>
      <Select
        aria-label={tp.label}
        value={selectedId === null ? '' : String(selectedId)}
        onChange={handleChange}
        options={options}
      />
    </div>
  )
}
