import { useEffect, useState, type ReactNode } from 'react'
import { Building2, RefreshCw, TrendingUp, Users, Wallet } from 'lucide-react'
import {
  api,
  formatCompactEur,
  formatPct,
  type CompanyCustomer,
  type CompanyNarrative,
  type FinancialSummary,
} from '../api/client'
import './Company.css'

const STATUS_LABEL: Record<string, string> = {
  secured: 'Contract secured',
  expanded: 'Contract expanded',
  executed: 'Project executed',
  signed_up: 'Signed up',
  confirmed: 'Confirmed partner',
  partner: 'Research partner',
  adopted: 'Adopted Senus ERA',
}

const MILESTONE_STATE: Record<string, string> = {
  integrated: 'Integrated',
  completed: 'Completed',
  confirmed: 'Confirmed',
  secured: 'Secured',
  plan: 'Board plan',
}
const MILESTONE_TONE: Record<string, string> = {
  integrated: 'complete',
  completed: 'complete',
  secured: 'live',
  confirmed: 'live',
  plan: 'plan',
}

const PEOPLE_GROUP_ORDER = ['board', 'management', 'founder']
const PEOPLE_GROUP_LABEL: Record<string, string> = {
  board: 'Board',
  management: 'Management',
  founder: 'Founders',
}

function Source({ page, quote }: { page: number; quote: string }) {
  return (
    <span className="bd-source" title={quote}>
      p.{page}
    </span>
  )
}

function fact(data: CompanyNarrative, key: string): string | number | undefined {
  const entry = data.company[key]
  if (!entry || Array.isArray(entry.value)) return undefined
  return entry.value
}

function money(value: number | null | undefined): string | null {
  return typeof value === 'number' ? formatCompactEur(value) : null
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter((w) => /^[A-Z]/.test(w))
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
}

function groupOf(person: { groups: string[] }): string {
  return PEOPLE_GROUP_ORDER.find((g) => person.groups.includes(g)) ?? 'management'
}

function Delta({ value, suffix = '%' }: { value: number; suffix?: string }) {
  const down = value < 0
  return (
    <span className={down ? 'delta down' : 'delta'}>
      <span>{down ? '↘' : '↗'}</span>
      {value > 0 ? '+' : ''}
      {value.toFixed(1)}
      {suffix}
    </span>
  )
}

function MetricCard({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <section className="metric-card">
      <div className="metric-top">
        <span>{label}</span>
        <span className="metric-icon">{icon}</span>
      </div>
      {children}
    </section>
  )
}

function sparkPath(values: number[]): string | null {
  if (values.length < 2) return null
  const min = Math.min(...values)
  const span = Math.max(...values) - min || 1
  return values
    .map((v, i) => {
      const x = 1 + (i / (values.length - 1)) * 188
      const y = 30 - ((v - min) / span) * 24
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}

function niceMax(value: number): number {
  const step = value > 1_000_000 ? 400_000 : 200_000
  return Math.ceil((value * 1.1) / step) * step
}

function CustomerCard({ customer }: { customer: CompanyCustomer }) {
  const meta = [customer.segment, customer.country, customer.product].filter(Boolean).join(' · ')
  return (
    <div className="account-row">
      <span>{initials(customer.name) || '·'}</span>
      <p>
        <strong>{customer.name}</strong>
        {customer.detail ?? meta}
      </p>
      <b>
        {STATUS_LABEL[customer.status] ?? customer.status}
        {money(customer.value_eur) && ` · ${money(customer.value_eur)}`}{' '}
        <Source page={customer.page} quote={customer.quote} />
      </b>
    </div>
  )
}

export function Company({
  summary,
  selected,
}: {
  summary: FinancialSummary[]
  selected: string
}) {
  const [data, setData] = useState<CompanyNarrative | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api
      .company(selected || undefined)
      .then((result) => active && setData(result))
      .catch((e) => active && setError(e instanceof Error ? e.message : 'Failed to load company data'))
    return () => {
      active = false
    }
  }, [selected])

  if (error) {
    return (
      <div className="state state--error">
        <p>Company information is not available for this document.</p>
        <code>{error}</code>
        <p className="state__hint">Re-process the source PDF from Documents to extract it.</p>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="state">
        <RefreshCw className="spin" size={28} />
        <p>Loading company overview…</p>
      </div>
    )
  }

  const current = summary.find((s) => s.period.label === selected) ?? summary[0]
  const series = [...summary]
    .sort((a, b) => a.period.end_date.localeCompare(b.period.end_date))
    .map((s) => s.revenue)
    .filter((v): v is number => typeof v === 'number')
  const spark = sparkPath(series)

  const agg = data.commercial.aggregates
  const closed = agg.closed_pipeline_eur
  const open = agg.open_pipeline_eur
  const customersClosed = agg.enterprise_customers_closed
  const totalPipeline = closed && open ? closed.value + open.value : null
  const closedShare = totalPipeline && closed ? Math.round((closed.value / totalPipeline) * 100) : null
  const scaleMax = closed && open ? niceMax(Math.max(closed.value, open.value)) : 0

  const people = [...data.people].sort(
    (a, b) => PEOPLE_GROUP_ORDER.indexOf(groupOf(a)) - PEOPLE_GROUP_ORDER.indexOf(groupOf(b)),
  )
  const named = data.commercial.customers.filter((c) => !c.unnamed)
  const unnamed = data.commercial.customers.filter((c) => c.unnamed)
  const priority = named.filter((c) => c.value_eur || c.status === 'expanded' || c.status === 'secured').slice(0, 3)
  const description = fact(data, 'description')
  const markets = data.company.markets?.value
  const milestones = data.milestones.filter((m) => m.key !== 'senus_2030')
  const periodLabel = data.document.period_label ?? selected

  const facts: [string, string | number | undefined][] = [
    ['Market', fact(data, 'market')],
    ['Ticker', fact(data, 'ticker')],
    ['ISIN', fact(data, 'isin')],
    ['Admission', fact(data, 'admission_date')],
    ['Issued share capital', fact(data, 'issued_share_capital')?.toLocaleString?.('en-IE')],
    ['Sponsor', fact(data, 'sponsor')],
    ['Registered office', fact(data, 'registered_office')],
    ['Registration no.', fact(data, 'registration_number')],
    ['Fiscal year end', fact(data, 'fiscal_year_end')],
    ['Founded', fact(data, 'founded')],
    ['Investor relations', fact(data, 'ir_email')],
  ]

  return (
    <div className="board">
      <div className="bd-page">
        <div className="page-heading">
          <div>
            <p className="eyebrow">EXECUTIVE OVERVIEW</p>
            <h1>Company direction at a glance</h1>
            {typeof description === 'string' && (
              <p>
                {description.charAt(0).toUpperCase() + description.slice(1)}
                {Array.isArray(markets) && ` · ${markets.join(' · ')}`}
              </p>
            )}
          </div>
          <div className="report-status">
            <span>Board pack</span>
            <strong>{periodLabel}</strong>
            <small>{data.document.filename}</small>
          </div>
        </div>

        {current && (
          <div className="metrics-grid">
            <MetricCard icon={<TrendingUp size={16} />} label="GROUP REVENUE">
              <div className="metric-primary">
                <strong>{formatCompactEur(current.revenue)}</strong>
                {typeof current.revenue_yoy_change_pct === 'number' && (
                  <Delta value={current.revenue_yoy_change_pct} />
                )}
              </div>
              <p>
                {current.revenue_yoy_period_label ? `vs ${current.revenue_yoy_period_label}` : current.period.label}
              </p>
              {spark && (
                <div className="sparkline">
                  <svg preserveAspectRatio="none" viewBox="0 0 190 34">
                    <path d={spark} />
                  </svg>
                </div>
              )}
            </MetricCard>
            <MetricCard icon={<TrendingUp size={16} />} label="GROSS PROFIT & MARGIN">
              <div className="metric-primary">
                <strong>{formatCompactEur(current.gross_profit)}</strong>
              </div>
              <p>{current.period.label}</p>
              <div className="margin-track">
                <i style={{ width: `${Math.min(Math.max(current.gross_margin ?? 0, 0), 100)}%` }} />
              </div>
              <div className="margin-label">
                <span>Gross margin</span>
                <strong>{formatPct(current.gross_margin)}</strong>
              </div>
            </MetricCard>
            <MetricCard icon={<Users size={16} />} label="OPERATING PERFORMANCE">
              <div className="dual-metric">
                <div>
                  <span>Operating loss</span>
                  <strong className="loss">{formatCompactEur(current.operating_loss)}</strong>
                  <small>{current.period.label}</small>
                </div>
                <div>
                  <span>Operating margin</span>
                  <strong className="loss">{formatPct(current.operating_margin)}</strong>
                  <small>of revenue</small>
                </div>
              </div>
            </MetricCard>
            <MetricCard icon={<Wallet size={16} />} label="CASH & CAPITAL">
              <div className="metric-primary">
                <strong>{formatCompactEur(current.cash)}</strong>
                {typeof current.runway_months === 'number' && (
                  <span className="status-pill">{current.runway_months.toFixed(0)} mo runway</span>
                )}
              </div>
              <p>Cash at period end</p>
              <div className="cash-row">
                <span>
                  Bank debt <strong>{formatCompactEur(current.debt)}</strong>
                </span>
              </div>
            </MetricCard>
          </div>
        )}

        <div className="dashboard-grid">
          <div className="main-column">
            <section className="bd-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">COMMERCIAL PIPELINE</p>
                  <h2>Contracted revenue &amp; open opportunities</h2>
                </div>
                {customersClosed && (
                  <div className="period-chip">
                    {customersClosed.value} enterprise customers{' '}
                    <Source page={customersClosed.page} quote={customersClosed.quote} />
                  </div>
                )}
              </div>
              <div className="pipeline-content">
                <div className="pipeline-chart">
                  <div className="pipeline-scale">
                    {[1, 2 / 3, 1 / 3, 0].map((f) => (
                      <span key={f}>{scaleMax ? formatCompactEur(scaleMax * f) : ''}</span>
                    ))}
                  </div>
                  <div className="pipeline-bars">
                    {closed && (
                      <div className="pipeline-column">
                        <span>{formatCompactEur(closed.value)}</span>
                        <i className="closed" style={{ height: `${(closed.value / scaleMax) * 100}%` }} />
                        <small>
                          Closed <Source page={closed.page} quote={closed.quote} />
                        </small>
                      </div>
                    )}
                    {open && (
                      <div className="pipeline-column">
                        <span>{formatCompactEur(open.value)}</span>
                        <i className="active" style={{ height: `${(open.value / scaleMax) * 100}%` }} />
                        <small>
                          Open <Source page={open.page} quote={open.quote} />
                        </small>
                      </div>
                    )}
                    {totalPipeline && closedShare !== null && (
                      <div className="pipeline-total">
                        <span>Total pipeline</span>
                        <strong>{formatCompactEur(totalPipeline)}</strong>
                        <small>
                          {closedShare}% closed · {100 - closedShare}% open (calculated)
                        </small>
                      </div>
                    )}
                  </div>
                </div>
                <div className="pipeline-accounts">
                  <p>Priority accounts</p>
                  {priority.map((c) => (
                    <CustomerCard key={c.name} customer={c} />
                  ))}
                </div>
              </div>
            </section>

            <section className="bd-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">CUSTOMERS &amp; PARTNERS</p>
                  <h2>Named in the report</h2>
                </div>
                <div className="period-chip">{named.length} named</div>
              </div>
              <div className="account-grid">
                {named.map((c) => (
                  <CustomerCard key={c.name} customer={c} />
                ))}
                {unnamed.map((c) => (
                  <CustomerCard key={c.name} customer={c} />
                ))}
              </div>
              {fact(data, 'fy25_customer_accounts') !== undefined && (
                <p className="bd-note">
                  {fact(data, 'fy25_customer_accounts')} customer accounts in FY25
                  {typeof fact(data, 'fy25_revenue_eur') === 'number' &&
                    ` with revenue of ${formatCompactEur(fact(data, 'fy25_revenue_eur') as number)}`}
                  .
                </p>
              )}
            </section>

            <section className="bd-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">LEADERSHIP</p>
                  <h2>Board &amp; management</h2>
                </div>
                <div className="period-chip">{people.length} people</div>
              </div>
              <div className="people-grid">
                {people.map((p) => (
                  <div className="person" key={p.name}>
                    <span className="avatar">{initials(p.name)}</span>
                    <p>
                      <strong>
                        {p.title && `${p.title} `}
                        {p.name}
                      </strong>
                      {p.roles.join(' · ')}
                      <em>{PEOPLE_GROUP_LABEL[groupOf(p)]}</em> <Source page={p.page} quote={p.quote} />
                    </p>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <aside className="side-column">
            <section className="bd-panel milestones-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">STRATEGIC MILESTONES</p>
                  <h2>Execution priorities</h2>
                </div>
                <span className="status-pill">{milestones.length + (data.strategy ? 1 : 0)} tracked</span>
              </div>
              <div className="milestone-list">
                {milestones.map((m, i) => (
                  <article key={m.key}>
                    <div className="milestone-top">
                      <span className="milestone-icon">{String(i + 1).padStart(2, '0')}</span>
                      <span className={`milestone-state ${MILESTONE_TONE[m.status] ?? 'plan'}`}>
                        {MILESTONE_STATE[m.status] ?? m.status}
                      </span>
                    </div>
                    <h3>{m.title}</h3>
                    <p>
                      {m.quote} <Source page={m.page} quote={m.quote} />
                    </p>
                    <div className="milestone-values">
                      {Object.entries(m.details).map(([k, v]) => (
                        <span key={k}>
                          {k.replace(/_eur$/, '').replace(/_/g, ' ')}{' '}
                          <strong>{k.endsWith('_eur') ? formatCompactEur(v as number) : String(v)}</strong>
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
                {data.strategy && (
                  <article>
                    <div className="milestone-top">
                      <span className="milestone-icon">{String(milestones.length + 1).padStart(2, '0')}</span>
                      <span className="milestone-state plan">Board plan</span>
                    </div>
                    <h3>{data.strategy.name}</h3>
                    <p>
                      {data.strategy.min_cagr_pct !== null && `Targeting ≥${data.strategy.min_cagr_pct}% annual compound sales growth`}
                      {data.strategy.ebitda_positive_fy && `, with positive EBITDA expected in ${data.strategy.ebitda_positive_fy}`}
                      . <Source page={data.strategy.page} quote={data.strategy.paragraphs[0] ?? ''} />
                    </p>
                    <div className="strategy-path">
                      <span>{periodLabel}</span>
                      <i />
                      <span>
                        {data.strategy.ebitda_positive_fy ?? 'n/d'}
                        <strong>EBITDA+</strong>
                      </span>
                      <i />
                      <span>
                        {data.strategy.period}
                        <strong>Scale</strong>
                      </span>
                    </div>
                  </article>
                )}
              </div>
            </section>

            <section className="bd-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">CORPORATE &amp; LISTING</p>
                  <h2>Company facts</h2>
                </div>
                <span className="metric-icon">
                  <Building2 size={16} />
                </span>
              </div>
              <dl className="facts">
                {facts
                  .filter(([, v]) => v !== undefined)
                  .map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
              </dl>
            </section>
          </aside>
        </div>

        {data.drivers.length > 0 && (
          <section className="insight-panel">
            <div className="insight-header">
              <div className="insight-title">
                <span>
                  <TrendingUp size={18} />
                </span>
                <div>
                  <p className="eyebrow">FROM THE REPORT</p>
                  <h2>Operating context &amp; Board commentary</h2>
                </div>
              </div>
              <span className="insight-status">
                <i />
                {data.drivers.length} sourced statements
              </span>
            </div>
            <div className="bd-insights">
              {data.drivers.map((d, i) => (
                <article key={d.text}>
                  <span>{String(i + 1).padStart(2, '0')}</span>
                  <div>
                    <strong>{d.topic.replace('_', ' ')}</strong>
                    <p>
                      {d.text} <Source page={d.page} quote={d.quote} />
                    </p>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        <div className="bd-footer">
          <span>Confidential · Senus PLC Board of Directors</span>
          <span>Every statement links to its page in {data.document.filename}</span>
        </div>
      </div>
    </div>
  )
}
