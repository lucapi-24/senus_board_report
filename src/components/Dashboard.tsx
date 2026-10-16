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
  toNumber,
  formatCompactEur,
  formatPct,
} from '../api/client'
import { KpiCard } from './KpiCard'

const CHART_COLORS = ['#20948B', '#023C28', '#FFBC7D', '#5FB3AC', '#8FBF9F']

const COST_LABELS = ['Cost of sales', 'Distribution costs', 'Administrative expenses']

function shiftYear(iso: string, years: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  return `${y + years}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function monthSpan(period: { start_date: string; end_date: string }): number {
  const [y1, m1] = period.start_date.split('-').map(Number)
  const [y2, m2] = period.end_date.split('-').map(Number)
  return (y2 - y1) * 12 + (m2 - m1)
}

interface BridgeStep {
  label: string
  value: number
  total?: boolean
  page?: number
}

interface RatioItem {
  label: string
  value: string
  note?: string
  tone?: 'neg' | 'pos'
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
              <span className="ratios__label">{item.label}</span>
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
  onSelect: (label: string) => void
}

export function Dashboard({ summary, selected, onSelect }: DashboardProps) {
  const [detail, setDetail] = useState<PeriodDetail | null>(null)
  const [insights, setInsights] = useState<AIInsight[]>([])
  const [insightsLoading, setInsightsLoading] = useState(false)

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

  const loadInsights = () => {
    if (!selected) return
    setInsightsLoading(true)
    api
      .insights(selected)
      .then(setInsights)
      .catch(() =>
        setInsights([{ section: 'Error', insight: 'Could not reach the AI service.', confidence: 0 }]),
      )
      .finally(() => setInsightsLoading(false))
  }

  useEffect(() => {
    setInsights([])
    loadInsights()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const current = useMemo(
    () => summary.find((s) => s.period.label === selected) ?? null,
    [summary, selected],
  )

  const kpiMap = useMemo(() => {
    const map: Record<string, number> = {}
    detail?.kpis.forEach((k) => (map[k.metric] = toNumber(k.value)))
    return map
  }, [detail])

  const revenue = toNumber(current?.revenue)
  const operatingLoss = toNumber(current?.operating_loss)
  const operatingMargin = revenue !== 0 ? (operatingLoss / revenue) * 100 : 0

  const compareData = summary.map((s) => ({
    name: s.period.label,
    Revenue: toNumber(s.revenue),
    'Gross Profit': toNumber(s.gross_profit),
    'Operating Loss': toNumber(s.operating_loss),
  }))

  const marginData = summary.map((s) => {
    const rev = toNumber(s.revenue)
    const op = toNumber(s.operating_loss)
    return {
      name: s.period.label,
      'Gross Margin': s.gross_margin ?? 0,
      'Operating Margin': rev !== 0 ? (op / rev) * 100 : 0,
    }
  })

  const costData = useMemo(() => {
    if (!detail) return []
    return COST_LABELS.map((label) => ({
      name: label,
      value: toNumber(detail.pnl.find((p) => p.line_item === label)?.value_eur),
    })).filter((c) => c.value > 0)
  }, [detail])

  const cf = useMemo(() => {
    const map: Record<string, number> = {}
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
    const steps: BridgeStep[] = [
      { label: 'Operating loss', value: operatingLoss, page: p?.pnl?.['Group operating loss'] },
      { label: 'Depreciation', value: cf['Depreciation'] ?? 0, page: p?.cash_flow?.['Depreciation'] },
      {
        label: 'Working capital',
        value: cf['Working capital movements'] ?? 0,
        page: p?.cash_flow?.['Working capital movements'],
      },
      { label: 'Interest paid', value: cf['Interest paid'] ?? 0, page: p?.cash_flow?.['Interest paid'] },
      { label: 'Capex', value: cf['Capex'] ?? 0, page: p?.cash_flow?.['Capex'] },
    ]
    const fcf = steps.reduce((acc, s) => acc + s.value, 0)
    return [...steps, { label: 'Free cash flow', value: fcf, total: true }]
  }, [detail, operatingLoss, cf])

  const bs: Record<string, number> = {}
  detail?.balance_sheet.forEach((b) => (bs[b.line_item] = toNumber(b.value_eur)))

  const netCurrentAssets = bs['Net Current Assets'] ?? null
  const contingentConsideration = Math.abs(bs['Contingent consideration'] ?? 0)
  const netCurrentAssetsEx =
    netCurrentAssets !== null ? netCurrentAssets + contingentConsideration : null

  const costOfSales = toNumber(detail?.pnl.find((p) => p.line_item === 'Cost of sales')?.value_eur)
  const costRatio = revenue !== 0 ? (costOfSales / revenue) * 100 : null

  // Compare like-for-like: same point one year earlier AND the same period
  // length, so a half-year is never measured against a full year (FY2025 and
  // HY2025 share the same start date).
  const prior = current
    ? summary.find(
        (s) =>
          s.period.start_date === shiftYear(current.period.start_date, -1) &&
          monthSpan(s.period) === monthSpan(current.period),
      )
    : undefined
  const yoy =
    current && prior && toNumber(prior.revenue) !== 0
      ? {
          label: prior.period.label,
          pct:
            ((toNumber(current.revenue) - toNumber(prior.revenue)) / toNumber(prior.revenue)) * 100,
        }
      : null

  const seasonality = (() => {
    for (const fy of summary) {
      if (monthSpan(fy.period) < 10) continue
      const h1 = summary.find(
        (s) => s.period.start_date === fy.period.start_date && monthSpan(s.period) < 10,
      )
      if (!h1) continue
      const rev = toNumber(fy.revenue)
      const h1v = toNumber(h1.revenue)
      if (!rev) continue
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

  const fullYear = summary.find((s) => monthSpan(s.period) >= 10)
  const fyRevenue = toNumber(fullYear?.revenue)

  const eurOrDash = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v) ? '—' : formatCompactEur(v)
  const multOrDash = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(2)}x`
  const netCashMovement =
    cf['Cash at end'] != null ? toNumber(cf['Cash at end']) - toNumber(cf['Cash at start']) : null

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
            <div className="period-selector" role="tablist">
              {summary.map((s) => (
                <button
                  key={s.period.label}
                  role="tab"
                  aria-selected={selected === s.period.label}
                  className={`period-selector__btn ${
                    selected === s.period.label ? 'is-active' : ''
                  }`}
                  onClick={() => onSelect(s.period.label)}
                >
                  <strong>{s.period.label}</strong>
                  <span>
                    {s.period.start_date} → {s.period.end_date}
                  </span>
                </button>
              ))}
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
                ? `YoY vs ${yoy.label} · ${monthSpan(current!.period) + 1}-month`
                : current?.period.label
            }
            trend={yoy ? yoy.pct : null}
            icon={Euro}
            accent="teal"
          />
          <KpiCard
            label="Gross Margin"
            value={formatPct(current?.gross_margin)}
            sublabel={`GP ${formatCompactEur(current?.gross_profit)}`}
            icon={Percent}
            accent="green"
          />
          <KpiCard
            label="Operating Margin"
            value={formatPct(operatingMargin)}
            sublabel={`Op. loss ${formatCompactEur(operatingLoss)}`}
            icon={Activity}
            accent="peach"
          />
          <KpiCard
            label="Cash"
            value={current?.cash != null ? formatCompactEur(current.cash) : '—'}
            sublabel="Balance sheet"
            icon={Wallet}
            accent="teal"
          />
          <KpiCard
            label="Cash Runway"
            value={current?.runway_months != null ? `${current.runway_months.toFixed(1)} mo` : '—'}
            sublabel="At current burn"
            icon={Timer}
            accent="green"
          />
          <KpiCard
            label="ROCE"
            value={kpiMap['roce'] != null ? formatPct(kpiMap['roce']) : formatPct(operatingMargin)}
            sublabel="Return on capital"
            icon={TrendingUp}
            accent="peach"
          />
        </section>

        {/* Charts row 1 */}
        <section className="grid-2">
          <div className="panel">
            <div className="panel__head">
              <h2>Revenue, Gross Profit &amp; Operating Loss</h2>
              <span className="panel__hint">FY = full year · HY = 6-month interim</span>
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
              <span className="panel__hint">Gross vs operating margin</span>
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
              { label: 'Gross margin', value: formatPct(kpiMap['gross_margin']) },
              {
                label: 'Operating margin',
                value: formatPct(kpiMap['operating_margin']),
                tone: kpiMap['operating_margin'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'EBITDA margin',
                value: formatPct(kpiMap['ebitda_margin']),
                tone: kpiMap['ebitda_margin'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'Net margin',
                value: formatPct(kpiMap['net_margin']),
                tone: kpiMap['net_margin'] < 0 ? 'neg' : undefined,
              },
              { label: 'Cost of sales / revenue', value: formatPct(costRatio) },
              { label: 'Admin expenses / revenue', value: formatPct(kpiMap['admin_expense_ratio']) },
            ]}
          />

          <RatioPanel
            title="Cash &amp; liquidity"
            hint={current?.period.label}
            items={[
              {
                label: 'Free cash flow',
                value: eurOrDash(kpiMap['fcf']),
                tone: kpiMap['fcf'] < 0 ? 'neg' : undefined,
              },
              { label: 'Monthly burn', value: eurOrDash(kpiMap['monthly_burn']) },
              {
                label: 'Cash runway',
                value: kpiMap['runway_months'] != null ? `${kpiMap['runway_months'].toFixed(1)} mo` : '—',
              },
              {
                label: 'Net cash (cash less long-term debt)',
                value: eurOrDash(kpiMap['net_cash']),
                tone: kpiMap['net_cash'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'Net cash movement',
                value: eurOrDash(netCashMovement),
                tone: netCashMovement != null && netCashMovement < 0 ? 'neg' : undefined,
              },
              { label: 'Current ratio', value: multOrDash(kpiMap['current_ratio']) },
              {
                label: 'Current ratio (ex-contingent)',
                value: multOrDash(kpiMap['current_ratio_ex_contingent']),
              },
              {
                label: 'Working capital',
                value: eurOrDash(netCurrentAssets),
                tone: netCurrentAssets != null && netCurrentAssets < 0 ? 'neg' : undefined,
                note:
                  contingentConsideration > 0
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
              { label: 'Debt / equity', value: formatPct(kpiMap['debt_to_equity']) },
              { label: 'Equity / total assets', value: formatPct(kpiMap['equity_ratio']) },
              {
                label: 'Intangibles / total assets',
                value: formatPct(kpiMap['intangibles_ratio']),
              },
              {
                label: 'Interest cover',
                value: multOrDash(kpiMap['interest_cover']),
                tone: kpiMap['interest_cover'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'DSCR',
                value: multOrDash(kpiMap['dscr']),
                note: 'Not meaningful while operating cash flow is negative.',
              },
              {
                label: 'ROCE',
                value: formatPct(kpiMap['roce']),
                tone: kpiMap['roce'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'ROCE (ex-contingent)',
                value: formatPct(kpiMap['roce_ex_contingent']),
                tone: kpiMap['roce_ex_contingent'] < 0 ? 'neg' : undefined,
              },
              {
                label: 'ROE',
                value: formatPct(kpiMap['roe']),
                tone: kpiMap['roe'] < 0 ? 'neg' : undefined,
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
                value: yoy ? formatPct(yoy.pct) : '—',
                tone: yoy ? (yoy.pct >= 0 ? 'pos' : 'neg') : undefined,
                note: yoy
                  ? `${current?.period.label} vs ${yoy.label} — same-length period one year earlier.`
                  : 'No prior-year comparable of the same length is loaded for this period.',
              },
              { label: 'Senus 2030 target CAGR', value: '50.0%' },
              {
                label: 'Required FY2026 revenue',
                value: fyRevenue ? formatCompactEur(fyRevenue * 1.5) : '—',
                note: fyRevenue
                  ? `From FY2025 revenue of ${formatCompactEur(fyRevenue)} to stay on a 50% CAGR track.`
                  : undefined,
              },
              {
                label: 'FY2025 seasonality (H1 / H2)',
                value: seasonality
                  ? `${seasonality.h1pct.toFixed(0)}% / ${seasonality.h2pct.toFixed(0)}%`
                  : '—',
                note: seasonality
                  ? `H1 ${formatCompactEur(seasonality.h1)} vs implied H2 ${formatCompactEur(
                      seasonality.h2,
                    )}.`
                  : undefined,
              },
            ]}
          />

          <div className="panel">
            <div className="panel__head">
              <h2>How to read these ratios</h2>
            </div>
            <ul className="notes">
              {(kpiMap['interest_cover'] < 0 || kpiMap['dscr'] == null) && (
                <li>
                  <strong>Coverage metrics are not meaningful.</strong> DSCR and interest cover are
                  negative or undefined because operating profit and operating cash flow are negative
                  — there is no EBITDA to service debt.
                </li>
              )}
              {contingentConsideration > 0 && (
                <li>
                  <strong>Contingent consideration shapes liquidity.</strong> Including the{' '}
                  {formatCompactEur(contingentConsideration)} earn-out the current ratio is{' '}
                  {multOrDash(kpiMap['current_ratio'])}; excluding it it is{' '}
                  {multOrDash(kpiMap['current_ratio_ex_contingent'])}.
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
              <span className="provenance__label">Source document</span>
              <span className="provenance__value">{meta?.document_title ?? '—'}</span>
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
            Every figure in the P&amp;L, balance sheet and cash flow is extracted from the source
            document; the tags on the cash-flow bridge show the page each line comes from.
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
                    <span className="insight-card__confidence">
                      {(insight.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p>{insight.insight}</p>
                  <div className="insight-card__meter">
                    <span style={{ width: `${Math.round(insight.confidence * 100)}%` }} />
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
