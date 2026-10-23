import type { LucideIcon } from 'lucide-react'
import { TrendingUp, TrendingDown } from 'lucide-react'

interface KpiCardProps {
  label: string
  value: string
  sublabel?: string
  icon: LucideIcon
  trend?: number | null
  accent?: 'teal' | 'green' | 'peach' | 'default'
  hint?: string
  origin?: string | null
  formula?: string | null
  sublabelOrigin?: string | null
}

export function KpiCard({
  label,
  value,
  sublabel,
  icon: Icon,
  trend,
  accent = 'default',
  hint,
  origin,
  formula,
  sublabelOrigin,
}: KpiCardProps) {
  return (
    <div className={`kpi-card kpi-card--${accent}`}>
      <div className="kpi-card__top">
        <span className="kpi-card__label">{label}</span>
        <span className="kpi-card__icon">
          <Icon size={18} />
        </span>
      </div>
      <div className="kpi-card__value">{value}</div>
      <div className="kpi-card__bottom">
        <span className="data-origin" title={formula ?? undefined}>
          {origin === 'reported' || origin === 'derived' ? origin : 'n/d'}
        </span>
        {trend !== undefined && trend !== null && (
          <span className={`kpi-card__trend ${trend >= 0 ? 'is-up' : 'is-down'}`}>
            {trend >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            {trend >= 0 ? '+' : ''}
            {trend.toFixed(1)}%
          </span>
        )}
        {sublabel && (
          <span className="kpi-card__sub">
            {sublabel}
            {sublabelOrigin && <span className="data-origin">{sublabelOrigin}</span>}
          </span>
        )}
      </div>
      {hint && <div className="kpi-card__hint">{hint}</div>}
    </div>
  )
}
