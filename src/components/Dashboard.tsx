import { useEffect, useMemo, useState } from 'react'
import {
  ResponsiveContainer,
  Tooltip,
  Legend,
  PieChart,
  Pie,
  Cell,
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
import { PeriodComparison } from './PeriodComparison'
import {
  api,
  type FinancialSummary,
  type PeriodDetail,
  type AIInsight,
  type KPIValue,
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

function insightLoadError(error: unknown): AIInsight {
  return {
    section: 'Error',
    insight: error instanceof Error
      ? `Could not load saved insights: ${error.message}. Retry manually; the rest of the dashboard remains available.`
      : 'Could not load saved insights. Retry manually; the rest of the dashboard remains available.',
    data: null,
    interpretation: null,
    confidence: 0,
    verified_claims: 0,
    total_claims: 0,
    sources: [],
    evidence: [],
  }
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
  const [insightsLoading, setInsightsLoading] = useState(Boolean(selected))
  const [sourceDocumentError] = useState('')

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
    if (!selected) return
    let active = true
    api
      .insights(selected)
      .then((savedInsights) => {
        if (active) setInsights(savedInsights)
      })
      .catch((error: unknown) => {
        if (active) setInsights([insightLoadError(error)])
      })
      .finally(() => {
        if (active) setInsightsLoading(false)
      })
    return () => {
      active = false
    }
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

  const fullYear =  summary.find((s) => s.duration_months >= 10)
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

        {/* Cost structure and cash bridge */}
        <section className="grid-2">
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
        {(insightsLoading || insights.length > 0) && (
        <section className="insights">
          <div className="insights__head">
            <div className="insights__title">
              <span className="insights__badge">
                <Sparkles size={16} /> AI Analysis
              </span>
              <h2>Board Insights</h2>
              <p>Points from the {current?.period.label} report text that the KPIs do not show.</p>
            </div>
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
                    <h3>{insight.section.charAt(0).toUpperCase() + insight.section.slice(1)}</h3>
                    {insight.total_claims > 0 && (
                      <div className="insight-card__confidence">
                        <span>
                          {insight.verified_claims}/{insight.total_claims} verified figures
                        </span>
                      </div>
                    )}
                  </div>
                  <p className="insight-card__statement">
                    {insight.interpretation ?? insight.data ?? insight.insight}
                  </p>
                  <details className="insight-card__details">
                    <summary>Sources and Calculations</summary>
                    {insight.data && (
                      <p className="insight-card__statement">
                        <strong>Data</strong> {insight.data}
                      </p>
                    )}
                    <ul className="insight-card__sources" aria-label="Verified data sources">
                      {insight.sources.map((source, sourceIndex) => (
                        <li key={`${source.source_id}-${sourceIndex}`}>
                          <span>
                            {source.claim_text} → {source.label} ({source.origin})
                          </span>
                          {source.formula && <small>Formula: {source.formula}</small>}
                          {source.source_note && <small>{source.source_note}</small>}
                          {source.pdf_excerpts.map((excerpt, excerptIndex) => (
                            <small key={`${source.source_id}-excerpt-${excerptIndex}`}>
                              {excerpt.period_label ?? selected}, PDF p. {excerpt.page}: “{excerpt.quote}”
                            </small>
                          ))}
                          {source.pages.length > 0 && (
                            <small>
                              {source.pages
                                .map((page) => `${source.period_label ?? selected}, PDF p. ${page}`)
                                .join(' · ')}
                            </small>
                          )}
                        </li>
                      ))}
                      {insight.evidence.map((item, evidenceIndex) => (
                        <li key={`evidence-${evidenceIndex}`}>
                          <span>
                            {item.period_label ?? selected}, PDF p. {item.page}: “{item.quote}”
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                </article>
              ))}
            </div>
          )}
          {sourceDocumentError && <p role="alert">{sourceDocumentError}</p>}
        </section>
        )}

        {/* Period comparison */}
        <PeriodComparison summary={summary} />
      </div>
    </div>
  )
}
