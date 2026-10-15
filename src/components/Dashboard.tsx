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
  Boxes,
  Gauge,
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

interface BridgeStep {
  label: string
  value: number
  total?: boolean
}

export function Dashboard() {
  const [summary, setSummary] = useState<FinancialSummary[]>([])
  const [selected, setSelected] = useState<string>('')
  const [detail, setDetail] = useState<PeriodDetail | null>(null)
  const [insights, setInsights] = useState<AIInsight[]>([])
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api
      .summary()
      .then((data) => {
        if (!active) return
        setSummary(data)
        if (data.length) setSelected(data[0].period.label)
        setLoading(false)
      })
      .catch((e) => {
        if (!active) return
        setError(e instanceof Error ? e.message : 'Failed to load data')
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

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

  const bridge = useMemo(() => {
    if (!detail || detail.cash_flow.length === 0) return [] as BridgeStep[]
    const steps: BridgeStep[] = [
      { label: 'Operating loss', value: operatingLoss },
      { label: 'Depreciation', value: cf['Depreciation'] ?? 0 },
      { label: 'Working capital', value: cf['Working capital movements'] ?? 0 },
      { label: 'Interest paid', value: cf['Interest paid'] ?? 0 },
      { label: 'Capex', value: cf['Capex'] ?? 0 },
    ]
    const fcf = steps.reduce((acc, s) => acc + s.value, 0)
    return [...steps, { label: 'Free cash flow', value: fcf, total: true }]
  }, [detail, operatingLoss, cf])

  if (loading) {
    return (
      <div className="state">
        <RefreshCw className="spin" size={28} />
        <p>Loading board report…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="state state--error">
        <p>Could not load financial data.</p>
        <code>{error}</code>
        <p className="state__hint">Ensure the API is running at http://localhost:8000.</p>
      </div>
    )
  }

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
                HY2026 and its HY2025 comparative are extracted from the source report. FY2025 is a
                full-year reference from summary data.
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
                  onClick={() => setSelected(s.period.label)}
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
            sublabel={current?.period.label}
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
                      <span className="bridge__label">{step.label}</span>
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

        {/* Liquidity strip */}
        <section className="grid-3">
          <div className="stat">
            <Gauge size={20} />
            <div>
              <span className="stat__label">DSCR</span>
              <span className="stat__value">{kpiMap['dscr'] != null ? `${kpiMap['dscr'].toFixed(2)}x` : '—'}</span>
            </div>
          </div>
          <div className="stat">
            <Boxes size={20} />
            <div>
              <span className="stat__label">Current Ratio</span>
              <span className="stat__value">
                {kpiMap['current_ratio'] ? `${kpiMap['current_ratio'].toFixed(2)}x` : '—'}
              </span>
            </div>
          </div>
          <div className="stat">
            <Wallet size={20} />
            <div>
              <span className="stat__label">Net Cash Movement</span>
              <span className="stat__value">
                {formatCompactEur(toNumber(cf['Cash at end']) - toNumber(cf['Cash at start']))}
              </span>
            </div>
          </div>
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
