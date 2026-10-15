const API_BASE = import.meta.env.VITE_API_URL ?? ''

export interface Period {
  id: number
  label: string
  start_date: string
  end_date: string
  source_doc_id: number | null
}

export interface LineItem {
  line_item: string
  value_eur: string
}

export interface KPIValue {
  metric: string
  value: string
}

export interface PeriodDetail extends Period {
  pnl: LineItem[]
  balance_sheet: LineItem[]
  cash_flow: LineItem[]
  kpis: KPIValue[]
}

export interface FinancialSummary {
  period: Period
  revenue: string | null
  gross_profit: string | null
  gross_margin: number | null
  operating_loss: string | null
  cash: string | null
  debt: string | null
  runway_months: number | null
}

export interface AIInsight {
  section: string
  insight: string
  confidence: number
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

export const api = {
  periods: () => get<Period[]>('/api/periods'),
  summary: () => get<FinancialSummary[]>('/api/summary'),
  periodDetail: (label: string) => get<PeriodDetail>(`/api/periods/${label}`),
  kpis: (label: string) => get<KPIValue[]>(`/api/kpis/${label}`),
  insights: (label: string) => get<AIInsight[]>(`/api/insights/${label}`),
}

export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  const n = typeof value === 'number' ? value : parseFloat(value)
  return Number.isFinite(n) ? n : 0
}

export function formatEur(value: string | number | null | undefined, digits = 0): string {
  const n = toNumber(value)
  return new Intl.NumberFormat('en-IE', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n)
}

export function formatCompactEur(value: string | number | null | undefined): string {
  const n = toNumber(value)
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `€${(n / 1_000_000).toFixed(2)}m`
  if (abs >= 1_000) return `€${(n / 1_000).toFixed(1)}k`
  return formatEur(n)
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits)}%`
}
