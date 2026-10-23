import { useEffect, useMemo, useState } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
} from 'recharts'
import {
  Euro,
  Percent,
  Wallet,
  Timer,
  TrendingUp,
  Activity,
  Sparkles,
  RefreshCw,
} from 'lucide-react'
import {
  api,
  type FinancialSummary,
  type PeriodDetail,
  type AIInsight,
  type KPIValue,
  type PeriodCompareResult,
  toNumber,
  formatCompactEur,
  formatPct,
} from '../api/client'
import { KpiCard } from './KpiCard'

const CHART_COLORS = ['#20948B', '#023C28', '#FFBC7D', '#5FB3AC', '#8FBF9F']

const COST_LABELS = ['Cost of sales', 'Distribution costs', 'Administrative expenses']
const DEFAULT_KPI_NOTES: Record<string, string> = {
  revenue_mom: 'Monthly revenue data is not available.',
  sales_channels: 'Revenue by sales channel is not available.',
  bookings: 'Bookings data is not available.',
  customer_accounts: 'Customer account data is not reported for this period.',
  dscr: 'Operating cash flow or debt service inputs are unavailable.',
  runway_months: 'Cash runway requires cash and negative free cash flow.',
}

interface BridgeStep {
  label: string
  value: number
  origin: 'reported' | 'derived'
  total?: boolean
  page?: number
}

interface RatioItem {
  label: string
  value: string
  origin?: string
  formula?: string | null
  note?: string
  tone?: 'neg' | 'pos'
}

function originLabel(origin?: string | null): string {
  if (origin === 'reported' || origin === 'derived') return origin
  return 'n/d'
}

function negativeTone(value: number | null | undefined): 'neg' | undefined {
  return value != null && value < 0 ? 'neg' : undefined
}

function RatioPanel({ title, hint, items }: { title: string; hint?: string; items: RatioItem[] }) {
  return (
    <div className="panel">
      <div className="panel__head">
        <h2>{title}</h2>
        {hint && <span className="panel__hint">{hint}</span>}
      </div>
      <ul className="ratios">
        {items.map((item) => (
          <li key={item.label} className="ratios__row">
            <div className="ratios__main">
              <span className="ratios__label">
                {item.label}
                <span className="data-origin" title={item.formula ?? item.note ?? undefined}>
                  {originLabel(item.origin)}
                </span>
              </span>
              <span className={`ratios__value ${item.tone ? `is-${item.tone}` : ''}`}>
                {item.value}
              </span>
            </div>
            {item.note && <p className="ratios__note">{item.note}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}

interface DashboardProps {
  summary: FinancialSummary[]
  selected: string
}

export function Dashboard({ summary, selected }: DashboardProps) {
  const [detail, setDetail] = useState<PeriodDetail | null>(null)
  const [insights, setInsights] = useState<AIInsight[]>([])
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [comparison, setComparison] = useState<PeriodCompareResult | null>(null)
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const [compareSelection, setCompareSelection] = useState<{
    label_a: string | null
    label_b: string | null
  }>({ label_a: null, label_b: null })

  const loadInsights = () => {
    if (!selected) return
    setInsightsLoading(true)
    api
      .insights(selected)
      .then(setInsights)
      .catch(() =>
        setInsights([{
          section: 'Error',
          insight: 'Could not reach the AI service.',
          data: null,
          interpretation: null,
          confidence: 0,
          verified_claims: 0,
          total_claims: 0,
          sources: [],
          evidence: [],
        }]),
      )
      .finally(() => setInsightsLoading(false))
  }

  const handleCompare = async () => {
    if (!compareSelection.label_a || !compareSelection.label_b || compareSelection.label_a === compareSelection.label_b) return
    setComparisonLoading(true)
    try {
      const result = await api.comparePeriods({
        period_label_a: compareSelection.label_a,
        period_label_b: compareSelection.label_b,
      })
      setComparison(result)
    } catch {
      setComparison({
        label_a: compareSelection.label_a,
        label_b: compareSelection.label_b,
        insight: 'Comparison failed. Please try again.',
        confidence: 0,
        data: null,
        interpretation: null,
        verified_claims: 0,
        total_claims: 0,
        sources: [],
        evidence: [],
      })
    } finally {
      setComparisonLoading(false)
    }
  }

  useEffect(() => {
    if (!selected) return
    let active = true
    api
      .periodDetail(selected)
      .then((d) => {
        if (active) setDetail(d)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [selected])

  useEffect(() => {
    setInsights([])
    setComparison(null)
    setCompareSelection({ label_a: null, label_b: null })
    loadInsights()
  }, [selected])

  const current = useMemo(
    () => summary.find((s) => s.period.label === selected) ?? null,
    [summary, selected],
  )

  const kpiMap = useMemo(() => {
    const map: Record<string, KPIValue> = {}
    detail?.kpis.forEach((k) => (map[k.metric] = k))
    return map
  }, [detail])

  const metricValue = (metric: string): number | null => kpiMap[metric]?.value ?? null
  const metricOrigin = (metric: string): string => originLabel(kpiMap[metric]?.kind)
  const metricFormula = (metric: string): string | null => kpiMap[metric]?.formula ?? null
  const metricNote = (metric: string): string | undefined =>
    kpiMap[metric]?.note ?? DEFAULT_KPI_NOTES[metric]

  const revenue = toNumber(current?.revenue)
  const operatingLoss = toNumber(current?.operating_loss)
  const operatingMargin = current?.operating_margin ?? null
  const comparableSummary = current
    ? summary.filter((period) => period.duration_months === current.duration_months)
    : []

  const compareData = comparableSummary.map((s) => ({
    name: s.period.label,
    Revenue: toNumber(s.revenue),
    'Gross Profit': toNumber(s.gross_profit),
    'Operating Loss': toNumber(s.operating_loss),
  }))

  const marginData = comparableSummary.map((s) => ({
    name: s.period.label,
    'Gross Margin': s.gross_margin,
    'Operating Margin': s.operating_margin,
  }))

  const costData = useMemo(() => {
    if (!detail) return []
    return COST_LABELS.map((label) => ({
      name: label,
      value: toNumber(detail.pnl.find((p) => p.line_item === label)?.value_eur),
    })).filter((cost): cost is { name: string; value: number } =>
      cost.value !== null && cost.value > 0,
    )
  }, [detail])

  const cf = useMemo(() => {
    const map: Record<string, number | null> = {}
    detail?.cash_flow.forEach((c) => (map[c.line_item] = toNumber(c.value_eur)))
    return map
  }, [detail])

  const provenance = detail?.provenance ?? null
  const meta = detail?.metadata ?? null

  const pageRange = (obj?: Record<string, number>): [number, number] | null => {
    const pages = Object.values(obj ?? {}).filter((n) => Number.isFinite(n))
    if (!pages.length) return null
    return [Math.min(...pages), Math.max(...pages)]
  }

  const formatPages = (pages: [number, number] | null) =>
    !pages ? '' : pages[0] === pages[1] ? `p.${pages[0]}` : `pp.${pages[0]}–${pages[1]}`

  const statements = [
    { name: 'P&L', pages: pageRange(provenance?.pnl) },
    { name: 'Balance sheet', pages: pageRange(provenance?.balance_sheet) },
    { name: 'Cash flow', pages: pageRange(provenance?.cash_flow) },
  ].filter((s) => s.pages)

  const bridge = useMemo(() => {
    if (!detail || detail.cash_flow.length === 0) return [] as BridgeStep[]
    const p = detail.provenance
    const depreciation = cf['Depreciation']
    const workingCapital = cf['Working capital movements']
    const interestPaid = cf['Interest paid']
    const capex = cf['Capex']
    if (
      operatingLoss === null ||
      depreciation == null ||
      workingCapital == null ||
      interestPaid == null ||
      capex == null
    ) {
      return [] as BridgeStep[]
    }
    const steps: BridgeStep[] = [
      {
        label: 'Operating loss',
        value: operatingLoss,
        origin: 'reported',
        page: p?.pnl?.['Group operating loss'],
      },
      { label: 'Depreciation', value: depreciation, origin: 'reported', page: p?.cash_flow?.['Depreciation'] },
      {
        label: 'Working capital',
        value: workingCapital,
        origin: 'reported',
        page: p?.cash_flow?.['Working capital movements'],
      },
      { label: 'Interest paid', value: interestPaid, origin: 'reported', page: p?.cash_flow?.['Interest paid'] },
      { label: 'Capex', value: capex, origin: 'reported', page: p?.cash_flow?.['Capex'] },
    ]
    const fcf = steps.reduce((acc, s) => acc + s.value, 0)
    const totalStep: BridgeStep = {
      label: 'Free cash flow',
      value: fcf,
      origin: 'derived',
      total: true,
    }
    return [...steps, totalStep]
  }, [detail, operatingLoss, cf])

  const bs: Record<string, number | null> = {}
  detail?.balance_sheet.forEach((b) => (bs[b.line_item] = toNumber(b.value_eur)))

  const netCurrentAssets = bs['Net Current Assets'] ?? null
  const contingentValue = bs['Contingent consideration']
  const contingentConsideration = contingentValue == null ? null : Math.abs(contingentValue)
  const netCurrentAssetsEx =
    netCurrentAssets !== null && contingentConsideration !== null
      ? netCurrentAssets + contingentConsideration
      : null

  const costOfSales = toNumber(detail?.pnl.find((p) => p.line_item === 'Cost of sales')?.value_eur)
  const costRatio =
    revenue !== null && revenue !== 0 && costOfSales !== null
      ? (costOfSales / revenue) * 100
      : null

  const yoy =
    current?.revenue_yoy_change_pct != null && current.revenue_yoy_period_label
      ? {
          label: current.revenue_yoy_period_label,
          pct: current.revenue_yoy_change_pct,
        }
      : null

  const seasonality = (() => {
    for (const fy of summary) {
      if (fy.duration_months < 10) continue
      const h1 = summary.find(
        (s) => s.period.start_date === fy.period.start_date && s.duration_months < 10,
      )
      if (!h1) continue
      const rev = fy.revenue
      const h1v = h1.revenue
      if (rev == null || h1v == null || rev === 0) continue
      return {
        fy: fy.period.label,
        h1: h1v,
        h2: rev - h1v,
        h1pct: (h1v / rev) * 100,
        h2pct: ((rev - h1v) / rev) * 100,
      }
    }
    return null
  })()

  const fullYear = summary.find((s) => s.duration_months >= 10)
  const fyRevenue = fullYear?.revenue ?? null

  const eurOrDash = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v) ? 'n/d' : formatCompactEur(v)
  const multOrDash = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v) ? 'n/d' : `${v.toFixed(2)}x`
  const cashAtStart = cf['Cash at start']
  const cashAtEnd = cf['Cash at end']
  const netCashMovement =
    cashAtStart != null && cashAtEnd != null ? cashAtEnd - cashAtStart : null
  const interestCover = metricValue('interest_cover')
  const showCoverageNote =
    (interestCover !== null && interestCover < 0) || metricValue('dscr') === null

  return (
    <div className="dashboard">
      <section className="dashboard__hero">
        <div className="container">
          <div className="dashboard__hero-row">
            <div>
              <span className="dashboard__eyebrow">BOARD REPORT · SENUS PLC</span>
              <h1 className="dashboard__title">Financial Performance</h1>
              <p className="dashboard__lede">
                AI-extracted financials from Senus source documents, presented for Management, the
                Board, Equity Investors and Credit Providers.
              </p>
              <p className="dashboard__note">
                HY2026 and its HY2025 comparative are extracted from the source report in full.
                FY2025 is available as revenue only (from the business review narrative).
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="container dashboard__body">
        {/* KPI grid */}
        <section className="kpi-grid">
          <KpiCard
            label="Revenue"
            value={formatCompactEur(revenue)}
            sublabel={
              yoy
                ? `YoY vs ${yoy.label} · ${current?.duration_months}-month`
                : current?.period.label
            }
            trend={yoy ? yoy.pct : null}
            origin={current?.revenue_origin}
            formula="Reported in the income statement"
            icon={Euro}
            accent="teal"
          />
          <KpiCard
            label="Gross Margin"
            value={formatPct(current?.gross_margin)}
            sublabel={`GP ${formatCompactEur(current?.gross_profit)}`}
            origin={current?.gross_margin_origin}
            formula={metricFormula('gross_margin')}
            sublabelOrigin={current?.gross_profit_origin}
            hint={metricNote('gross_margin')}
            icon={Percent}
            accent="green"
          />
          <KpiCard
            label="Operating Margin"
            value={formatPct(operatingMargin)}
            sublabel={`Op. loss ${formatCompactEur(operatingLoss)}`}
            origin={current?.operating_margin_origin}
            formula={metricFormula('operating_margin')}
            sublabelOrigin={current?.operating_loss_origin}
            hint={metricNote('operating_margin')}
            icon={Activity}
            accent="peach"
          />
          <KpiCard
            label="Cash"
            value={formatCompactEur(current?.cash)}
            sublabel={current?.period.source_doc_id == null ? 'Identified source' : 'Balance sheet'}
            origin={current?.cash_origin}
            formula="Reported cash balance or identified source"
            hint={
              current?.period.source_doc_id == null
                ? meta?.source_description ?? undefined
                : undefined
            }
            icon={Wallet}
            accent="teal"
          />
          <KpiCard
            label="Cash Runway"
            value={
              current?.runway_months != null
                ? `${current.runway_months.toFixed(1)} mo`
                : 'n/d'
            }
            sublabel="At current burn"
            origin={current?.runway_origin}
            formula={metricFormula('runway_months')}
            hint={metricNote('runway_months')}
            icon={Timer}
            accent="green"
          />
          <KpiCard
            label="ROCE"
            value={formatPct(metricValue('roce'))}
            sublabel="Return on capital"
            origin={metricOrigin('roce')}
            formula={metricFormula('roce')}
            icon={TrendingUp}
            accent="peach"
          />
        </section>

        {/* Charts row 1 */}
        <section className="grid-2">
          <div className="panel">
            <div className="panel__head">
              <h2>Revenue, Gross Profit &amp; Operating Loss</h2>
              <span className="panel__hint">
                Reported amounts · same-duration periods only
              </span>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={compareData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eceeed" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: '#6b7370', fontSize: 13 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: '#6b7370', fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => formatCompactEur(v)}
                />
                <Tooltip formatter={(v) => formatCompactEur(Number(v))} />
                <Legend />
                <Bar dataKey="Revenue" fill="#20948B" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Gross Profit" fill="#023C28" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Operating Loss" fill="#FFBC7D" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="panel">
            <div className="panel__head">
              <h2>Cost Breakdown</h2>
              <span className="panel__hint">{current?.period.label}</span>
            </div>
            {costData.length ? (
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={costData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={70}
                    outerRadius={110}
                    paddingAngle={3}
                  >
                    {costData.map((_, i) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v) => formatCompactEur(Number(v))} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="panel__empty">No cost data for this period.</div>
            )}
          </div>
        </section>

        {/* Charts row 2 */}
        <section className="grid-2">
          <div className="panel">
            <div className="panel__head">
              <h2>Margin Trend</h2>
              <span className="panel__hint">Derived percentages · same-duration periods only</span>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={marginData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eceeed" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: '#6b7370', fontSize: 13 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: '#6b7370', fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip formatter={(v) => formatPct(Number(v))} />
                <Legend />
                <Line type="monotone" dataKey="Gross Margin" stroke="#20948B" strokeWidth={3} dot={{ r: 5 }} />
                <Line type="monotone" dataKey="Operating Margin" stroke="#FFBC7D" strokeWidth={3} dot={{ r: 5 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="panel">
            <div className="panel__head">
              <h2>EBITDA → Free Cash Flow Bridge</h2>
              <span className="panel__hint">{current?.period.label}</span>
            </div>
            {bridge.length ? (
              <div className="bridge">
                {bridge.map((step) => {
                  const isTotal = step.total === true
                  return (
                    <div key={step.label} className={`bridge__row ${isTotal ? 'is-total' : ''}`}>
                      <span className="bridge__label">
                        {step.label}
                        <span className="data-origin">{step.origin}</span>
                        {step.page && <span className="src-tag" title="Source page">p.{step.page}</span>}
                      </span>
                      <span className={`bridge__value ${step.value < 0 ? 'is-neg' : 'is-pos'}`}>
                        {step.value < 0 ? '−' : '+'}
                        {formatCompactEur(Math.abs(step.value))}
                      </span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="panel__empty">No cash flow data for this period.</div>
            )}
          </div>
        </section>

        {/* Key ratios */}
        <section className="grid-3">
          <RatioPanel
            title="Profitability"
            hint={current?.period.label}
            items={[
              {
                label: 'Gross margin',
                value: formatPct(metricValue('gross_margin')),
                origin: metricOrigin('gross_margin'),
                formula: metricFormula('gross_margin'),
                note: metricNote('gross_margin'),
              },
              {
                label: 'Operating margin',
                value: formatPct(metricValue('operating_margin')),
                origin: metricOrigin('operating_margin'),
                formula: metricFormula('operating_margin'),
                note: metricNote('operating_margin'),
                tone: negativeTone(metricValue('operating_margin')),
              },
              {
                label: 'EBITDA margin',
                value: formatPct(metricValue('ebitda_margin')),
                origin: metricOrigin('ebitda_margin'),
                formula: metricFormula('ebitda_margin'),
                note: metricNote('ebitda_margin'),
                tone: negativeTone(metricValue('ebitda_margin')),
              },
              {
                label: 'Net margin',
                value: formatPct(metricValue('net_margin')),
                origin: metricOrigin('net_margin'),
                formula: metricFormula('net_margin'),
                note: metricNote('net_margin'),
                tone: negativeTone(metricValue('net_margin')),
              },
              {
                label: 'Cost of sales / revenue',
                value: formatPct(costRatio),
                origin: costRatio === null ? 'n/d' : 'derived',
                formula: 'Cost of sales / Revenue × 100',
              },
              {
                label: 'Admin expenses / revenue',
                value: formatPct(metricValue('admin_expense_ratio')),
                origin: metricOrigin('admin_expense_ratio'),
                formula: metricFormula('admin_expense_ratio'),
                note: metricNote('admin_expense_ratio'),
              },
            ]}
          />

          <RatioPanel
            title="Cash &amp; liquidity"
            hint={current?.period.label}
            items={[
              {
                label: 'Free cash flow',
                value: eurOrDash(metricValue('fcf')),
                origin: metricOrigin('fcf'),
                formula: metricFormula('fcf'),
                note: metricNote('fcf'),
                tone: negativeTone(metricValue('fcf')),
              },
              {
                label: 'Monthly burn',
                value: eurOrDash(metricValue('monthly_burn')),
                origin: metricOrigin('monthly_burn'),
                formula: metricFormula('monthly_burn'),
                note: metricNote('monthly_burn'),
              },
              {
                label: 'Cash runway',
                value:
                  metricValue('runway_months') != null
                    ? `${metricValue('runway_months')!.toFixed(1)} mo`
                    : 'n/d',
                origin: metricOrigin('runway_months'),
                formula: metricFormula('runway_months'),
                note: metricNote('runway_months'),
              },
              {
                label: 'Net cash (cash less long-term debt)',
                value: eurOrDash(metricValue('net_cash')),
                origin: metricOrigin('net_cash'),
                formula: metricFormula('net_cash'),
                note: metricNote('net_cash'),
                tone: negativeTone(metricValue('net_cash')),
              },
              {
                label: 'Net cash movement',
                value: eurOrDash(netCashMovement),
                origin: netCashMovement === null ? 'n/d' : 'derived',
                formula: 'Cash at end − Cash at start',
                tone: netCashMovement != null && netCashMovement < 0 ? 'neg' : undefined,
              },
              {
                label: 'Current ratio',
                value: multOrDash(metricValue('current_ratio')),
                origin: metricOrigin('current_ratio'),
                formula: metricFormula('current_ratio'),
                note: metricNote('current_ratio'),
              },
              {
                label: 'Current ratio (ex-contingent)',
                value: multOrDash(metricValue('current_ratio_ex_contingent')),
                origin: metricOrigin('current_ratio_ex_contingent'),
                formula: metricFormula('current_ratio_ex_contingent'),
                note: metricNote('current_ratio_ex_contingent'),
              },
              {
                label: 'Working capital',
                value: eurOrDash(netCurrentAssets),
                origin: netCurrentAssets === null ? 'n/d' : 'reported',
                formula: null,
                tone: netCurrentAssets != null && netCurrentAssets < 0 ? 'neg' : undefined,
                note:
                  contingentConsideration != null && contingentConsideration > 0
                    ? `Net current assets including the Loamin contingent consideration. Excluding it: ${formatCompactEur(
                        netCurrentAssetsEx,
                      )}.`
                    : undefined,
              },
            ]}
          />

          <RatioPanel
            title="Solvency &amp; returns"
            hint={current?.period.label}
            items={[
              {
                label: 'Debt / equity',
                value: formatPct(metricValue('debt_to_equity')),
                origin: metricOrigin('debt_to_equity'),
                formula: metricFormula('debt_to_equity'),
                note: metricNote('debt_to_equity'),
              },
              {
                label: 'Equity / total assets',
                value: formatPct(metricValue('equity_ratio')),
                origin: metricOrigin('equity_ratio'),
                formula: metricFormula('equity_ratio'),
                note: metricNote('equity_ratio'),
              },
              {
                label: 'Intangibles / total assets',
                value: formatPct(metricValue('intangibles_ratio')),
                origin: metricOrigin('intangibles_ratio'),
                formula: metricFormula('intangibles_ratio'),
                note: metricNote('intangibles_ratio'),
              },
              {
                label: 'Interest cover',
                value: multOrDash(metricValue('interest_cover')),
                origin: metricOrigin('interest_cover'),
                formula: metricFormula('interest_cover'),
                note: metricNote('interest_cover'),
                tone: negativeTone(metricValue('interest_cover')),
              },
              {
                label: 'DSCR',
                value: multOrDash(metricValue('dscr')),
                origin: metricOrigin('dscr'),
                formula: metricFormula('dscr'),
                note: metricNote('dscr'),
              },
              {
                label: 'ROCE',
                value: formatPct(metricValue('roce')),
                origin: metricOrigin('roce'),
                formula: metricFormula('roce'),
                note: metricNote('roce'),
                tone: negativeTone(metricValue('roce')),
              },
              {
                label: 'ROCE (ex-contingent)',
                value: formatPct(metricValue('roce_ex_contingent')),
                origin: metricOrigin('roce_ex_contingent'),
                formula: metricFormula('roce_ex_contingent'),
                note: metricNote('roce_ex_contingent'),
                tone: negativeTone(metricValue('roce_ex_contingent')),
              },
              {
                label: 'ROE',
                value: formatPct(metricValue('roe')),
                origin: metricOrigin('roe'),
                formula: metricFormula('roe'),
                note: metricNote('roe'),
                tone: negativeTone(metricValue('roe')),
              },
            ]}
          />
        </section>

        {/* Growth & guidance */}
        <section className="grid-2">
          <RatioPanel
            title="Growth &amp; Senus 2030"
            hint={current?.period.label}
            items={[
              {
                label: 'Revenue growth YoY',
                value: yoy ? formatPct(yoy.pct) : 'n/d',
                origin: yoy ? 'derived' : 'n/d',
                formula: 'Same-duration revenue vs the period starting 12 months earlier',
                tone: yoy ? (yoy.pct >= 0 ? 'pos' : 'neg') : undefined,
                note: yoy
                  ? `${current?.period.label} vs ${yoy.label} — same-length period one year earlier.`
                  : 'No prior-year comparable of the same length is loaded for this period.',
              },
              {
                label: 'Senus 2030 target CAGR',
                value: '50.0%',
                origin: 'reported',
              },
              {
                label: 'Required FY2026 revenue',
                value: fyRevenue !== null ? formatCompactEur(fyRevenue * 1.5) : 'n/d',
                origin: fyRevenue !== null ? 'derived' : 'n/d',
                formula: 'FY2025 revenue × 1.5',
                note: fyRevenue !== null
                  ? `From FY2025 revenue of ${formatCompactEur(fyRevenue)} to stay on a 50% CAGR track.`
                  : 'FY2025 revenue is not available.',
              },
              {
                label: 'FY2025 seasonality (H1 / H2)',
                value: seasonality
                  ? `${seasonality.h1pct.toFixed(0)}% / ${seasonality.h2pct.toFixed(0)}%`
                  : 'n/d',
                origin: seasonality ? 'derived' : 'n/d',
                formula: 'H1 and residual H2 revenue as a share of full-year revenue',
                note: seasonality
                  ? `H1 ${formatCompactEur(seasonality.h1)} vs implied H2 ${formatCompactEur(
                      seasonality.h2,
                    )}.`
                  : undefined,
              },
              {
                label: 'Revenue growth month over month',
                value: formatPct(metricValue('revenue_mom')),
                origin: metricOrigin('revenue_mom'),
                note: metricNote('revenue_mom'),
              },
              {
                label: 'Revenue by sales channel',
                value: eurOrDash(metricValue('sales_channels')),
                origin: metricOrigin('sales_channels'),
                note: metricNote('sales_channels'),
              },
              {
                label: 'Bookings',
                value: eurOrDash(metricValue('bookings')),
                origin: metricOrigin('bookings'),
                note: metricNote('bookings'),
              },
              {
                label: 'Customer accounts',
                value:
                  metricValue('customer_accounts') == null
                    ? 'n/d'
                    : `${metricValue('customer_accounts')}`,
                origin: metricOrigin('customer_accounts'),
                note: metricNote('customer_accounts'),
              },
            ]}
          />

          <div className="panel">
            <div className="panel__head">
              <h2>How to read these ratios</h2>
            </div>
            <ul className="notes">
              {showCoverageNote ? (
                <li>
                  <strong>DSCR is n/d.</strong> {metricNote('dscr') ?? 'Required inputs are not reported.'}
                </li>
              ) : null}
              {contingentConsideration != null && contingentConsideration > 0 && (
                <li>
                  <strong>Contingent consideration shapes liquidity.</strong> Including the{' '}
                  {formatCompactEur(contingentConsideration)} earn-out the current ratio is{' '}
                  {multOrDash(metricValue('current_ratio'))}; excluding it it is{' '}
                  {multOrDash(metricValue('current_ratio_ex_contingent'))}.
                </li>
              )}
              <li>
                <strong>Short history.</strong> Only three periods are available, so trend and
                seasonality views are indicative rather than statistically robust.
              </li>
            </ul>
          </div>
        </section>

        {/* Source & data quality */}
        <section className="panel provenance">
          <div className="panel__head">
            <h2>Source &amp; data quality</h2>
            <span className="panel__hint">{current?.period.label}</span>
          </div>
          <div className="provenance__grid">
            <div>
              <span className="provenance__label">Source</span>
              <span className="provenance__value">
                {meta?.source_description ?? meta?.document_title ?? 'n/d'}
              </span>
            </div>
            <div>
              <span className="provenance__label">Reporting period</span>
              <span className="provenance__value">
                {meta?.period_months ? `${meta.period_months} months` : '—'} ·{' '}
                {meta?.audited ? 'Audited' : 'Unaudited'}
              </span>
            </div>
            <div>
              <span className="provenance__label">Currency</span>
              <span className="provenance__value">{meta?.currency ?? 'EUR'}</span>
            </div>
            <div>
              <span className="provenance__label">Figures traced to</span>
              <span className="provenance__value">
                {statements.length
                  ? statements.map((s) => `${s.name} ${formatPages(s.pages)}`).join(' · ')
                  : '—'}
              </span>
            </div>
          </div>
          <p className="provenance__note">
            {current?.period.source_doc_id == null
              ? 'This period has no uploaded source document; its reported values are attributed above.'
              : 'Statement figures are reported from the source document; derived KPIs show their formula on hover.'}
            {!meta?.audited && ' These interim accounts are unaudited.'}
          </p>
        </section>

        {/* AI insights */}
        <section className="insights">
          <div className="insights__head">
            <div className="insights__title">
              <span className="insights__badge">
                <Sparkles size={16} /> AI Analysis
              </span>
              <h2>Board Insights</h2>
              <p>Generated from {current?.period.label} financials using an LLM analyst.</p>
            </div>
            <button className="btn btn-ghost" onClick={loadInsights} disabled={insightsLoading}>
              <RefreshCw size={16} className={insightsLoading ? 'spin' : ''} />
              Regenerate
            </button>
          </div>

          {insightsLoading && !insights.length ? (
            <div className="state state--inline">
              <RefreshCw className="spin" size={22} />
              <p>Analysing financials…</p>
            </div>
          ) : (
            <div className="insights__grid">
              {insights.map((insight, i) => (
                <article key={i} className="insight-card">
                  <div className="insight-card__head">
                    <h3>{insight.section}</h3>
                    {insight.total_claims > 0 && (
                      <span className="insight-card__confidence">
                        {insight.verified_claims}/{insight.total_claims} claims verified
                      </span>
                    )}
                  </div>
                  {insight.data && (
                    <p className="insight-card__statement">
                      <strong>Data</strong> {insight.data}
                    </p>
                  )}
                  {insight.interpretation && (
                    <p className="insight-card__statement">
                      <strong>Interpretation</strong> {insight.interpretation}
                    </p>
                  )}
                  {!insight.data && !insight.interpretation && <p>{insight.insight}</p>}
                  {insight.sources.length > 0 && (
                    <ul className="insight-card__sources" aria-label="Verified data sources">
                      {insight.sources.map((source, sourceIndex) => (
                        <li key={`${source.source_id}-${sourceIndex}`}>
                          <span>
                            {source.claim_text} → {source.label} ({source.origin})
                            {source.pages.length > 0 && `, PDF p. ${source.pages.join(', ')}`}
                          </span>
                          {source.formula && <small>{source.formula}</small>}
                          {source.source_note && <small>{source.source_note}</small>}
                        </li>
                      ))}
                      {insight.evidence.map((item, evidenceIndex) => (
                        <li key={`evidence-${evidenceIndex}`}>
                          <span>PDF p. {item.page}: “{item.quote}”</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>

        {/* AI comparison */}
        <section className="insights comparison">
          <div className="insights__head">
            <div className="insights__title">
              <span className="insights__badge">
                <Sparkles size={16} /> AI Comparison
              </span>
              <h2>Compare Periods</h2>
              <p>Compare periods with source-linked claims and verified figures.</p>
            </div>
          </div>

          <div className="compare__form">
            <div className="compare__select">
              <label>Period A</label>
              <select
                value={compareSelection.label_a ?? ''}
                onChange={(e) => setCompareSelection((prev) => ({ ...prev, label_a: e.target.value }))}
              >
                <option value="">Select period</option>
                {summary.map((s) => (
                  <option key={s.period.label} value={s.period.label}>
                    {s.period.label} ({s.period.start_date} to {s.period.end_date})
                  </option>
                ))}
              </select>
            </div>
            <div className="compare__select">
              <label>Period B</label>
              <select
                value={compareSelection.label_b ?? ''}
                onChange={(e) => setCompareSelection((prev) => ({ ...prev, label_b: e.target.value }))}
              >
                <option value="">Select period</option>
                {summary.map((s) => (
                  <option key={s.period.label} value={s.period.label}>
                    {s.period.label} ({s.period.start_date} to {s.period.end_date})
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="btn btn--primary btn--small"
              disabled={comparisonLoading || !compareSelection.label_a || !compareSelection.label_b || compareSelection.label_a === compareSelection.label_b}
              onClick={handleCompare}
            >
              {comparisonLoading ? 'Comparing…' : 'Compare'}
            </button>
          </div>

          {comparisonLoading && (
            <div className="state state--inline">
              <RefreshCw className="spin" size={22} />
              <p>Comparing periods…</p>
            </div>
          )}

          {comparison && (
            <div className="compare__result">
              <div className="compare__columns">
                <div className="compare__col">
                  <h3>{comparison.label_a}</h3>
                  <p className="muted small">{summary.find((s) => s.period.label === comparison.label_a)?.period.start_date} to {summary.find((s) => s.period.label === comparison.label_a)?.period.end_date}</p>
                </div>
                <div className="compare__col compare__col--arrow">→</div>
                <div className="compare__col">
                  <h3>{comparison.label_b}</h3>
                  <p className="muted small">{summary.find((s) => s.period.label === comparison.label_b)?.period.start_date} to {summary.find((s) => s.period.label === comparison.label_b)?.period.end_date}</p>
                </div>
              </div>
              <div className="compare__insight">
                {comparison.data && (
                  <p className="insight-card__statement">
                    <strong>Data</strong> {comparison.data}
                  </p>
                )}
                {comparison.interpretation && (
                  <p className="insight-card__statement">
                    <strong>Interpretation</strong> {comparison.interpretation}
                  </p>
                )}
                {!comparison.data && !comparison.interpretation && <p>{comparison.insight}</p>}
                {comparison.total_claims > 0 && (
                  <span className="muted small">
                    {comparison.verified_claims}/{comparison.total_claims} claims verified
                  </span>
                )}
                {comparison.sources.length > 0 && (
                  <ul className="insight-card__sources" aria-label="Verified comparison sources">
                    {comparison.sources.map((source, sourceIndex) => (
                      <li key={`${source.source_id}-${sourceIndex}`}>
                        <span>
                          {source.claim_text} → {source.period_label} · {source.label} ({source.origin})
                          {source.pages.length > 0 && `, PDF p. ${source.pages.join(', ')}`}
                        </span>
                        {source.formula && <small>{source.formula}</small>}
                        {source.source_note && <small>{source.source_note}</small>}
                      </li>
                    ))}
                    {comparison.evidence.map((item, evidenceIndex) => (
                      <li key={`comparison-evidence-${evidenceIndex}`}>
                        <span>
                          {item.period_label && `${item.period_label}, `}PDF p. {item.page}: “{item.quote}”
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
