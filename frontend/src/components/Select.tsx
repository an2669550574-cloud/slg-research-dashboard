import { Listbox, ListboxButton, ListboxOptions, ListboxOption } from '@headlessui/react'
import { Check, ChevronDown } from 'lucide-react'
import clsx from 'clsx'

export interface SelectOption {
  value: string
  label: string
}

/**
 * 与暗/亮主题 token 一致的下拉，替代原生 <select>（原生展开是白底大蓝条，
 * 在暗色 UI 里最扎眼）。Headless UI 负责无障碍：键盘上下选择、Esc 关闭、
 * 焦点管理、点击外部关闭。视觉刻意贴齐表单 inputClass，作为后续推广模板。
 *
 * value === '' 视为"未选/占位"，按钮文字走 muted 色（语义同原 placeholder）。
 */
export function Select({
  value,
  onChange,
  options,
  className,
  disabled,
  'aria-label': ariaLabel,
}: {
  value: string
  onChange: (v: string) => void
  options: SelectOption[]
  className?: string
  disabled?: boolean
  'aria-label'?: string
}) {
  const selected = options.find(o => o.value === value)
  const isPlaceholder = value === '' || !selected

  return (
    <Listbox value={value} onChange={onChange} disabled={disabled}>
      <ListboxButton
        aria-label={ariaLabel}
        className={clsx(
          'flex w-full items-center justify-between gap-2 rounded-lg border border-default',
          'bg-elevated px-3 py-2 text-sm text-left transition-colors',
          'focus:outline-none focus:border-brand-500 data-[open]:border-brand-500',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          isPlaceholder ? 'text-muted' : 'text-primary',
          className,
        )}
      >
        <span className="truncate">{selected?.label ?? ''}</span>
        <ChevronDown size={14} className="shrink-0 text-muted" />
      </ListboxButton>
      <ListboxOptions
        anchor="bottom start"
        transition
        className={clsx(
          'z-50 w-[var(--button-width)] mt-1 max-h-60 overflow-auto rounded-lg p-1',
          'border border-default bg-surface shadow-xl shadow-black/20',
          'focus:outline-none [--anchor-gap:4px]',
          'transition data-[closed]:opacity-0 data-[closed]:-translate-y-1 duration-100 ease-out',
        )}
      >
        {options.map(o => (
          <ListboxOption
            key={o.value}
            value={o.value}
            className={clsx(
              'flex cursor-pointer items-center justify-between gap-2 rounded-md px-2.5 py-2',
              'text-sm text-secondary select-none',
              'data-[focus]:bg-elevated data-[focus]:text-primary',
              'data-[selected]:text-primary data-[selected]:font-medium',
            )}
          >
            <span className="truncate">{o.label}</span>
            {o.value === value && <Check size={14} className="shrink-0 text-brand-500" />}
          </ListboxOption>
        ))}
      </ListboxOptions>
    </Listbox>
  )
}
