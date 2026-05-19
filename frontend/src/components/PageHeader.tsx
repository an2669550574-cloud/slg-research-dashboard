import type { ReactNode } from 'react'

/**
 * 全站统一的「情报终端」页眉母题:等宽眉标 + 琥珀脉冲点、Bricolage 展示
 * 大标题、可选遥测条(mono 状态读出)、扫光分隔线、右侧动作槽。
 * 把它当设计系统入口——所有页面共用,保证视觉一致。
 */
export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-muted/70">{label}</span>
      <span className="text-accent">▸</span>
      <span className="text-secondary">{value}</span>
    </span>
  )
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  stats,
  children,
}: {
  eyebrow: string
  title: string
  subtitle?: string
  stats?: { label: string; value: ReactNode }[]
  children?: ReactNode
}) {
  return (
    <header className="reveal reveal-1">
      <div className="flex items-center gap-2.5 eyebrow text-muted">
        <span className="w-1.5 h-1.5 rounded-full bg-signal pulse-dot inline-block" />
        {eyebrow}
      </div>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
        <div>
          <h1 className="font-display text-[34px] sm:text-[46px] leading-[0.92] font-extrabold text-primary">
            {title}
          </h1>
          {subtitle && <p className="text-secondary text-sm mt-2.5 max-w-xl">{subtitle}</p>}
        </div>
        {children && <div className="flex items-center gap-2.5">{children}</div>}
      </div>
      {stats && stats.length > 0 && (
        <div className="mt-5 flex flex-wrap items-center gap-x-7 gap-y-2 font-data text-[11px]">
          {stats.map((s, i) => <Stat key={i} label={s.label} value={s.value} />)}
        </div>
      )}
      <div className="scan-rule mt-4" />
    </header>
  )
}
