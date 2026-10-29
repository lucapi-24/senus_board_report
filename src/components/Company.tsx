import { useEffect, useState } from 'react'
import { Building2, Euro, Landmark, RefreshCw, TrendingUp, Users, Wallet } from 'lucide-react'
import {
  api,
  formatCompactEur,
  formatPct,
  type CompanyCustomer,
  type CompanyNarrative,
  type FinancialSummary,
} from '../api/client'
import { KpiCard } from './KpiCard'
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

const PEOPLE_GROUP_ORDER = ['board', 'management', 'founder']
const PEOPLE_GROUP_LABEL: Record<string, string> = {
  board: 'Board',
  management: 'Management',
  founder: 'Founders',
}

function Source({ page, quote }: { page: number; quote: string }) {
  return (
    <span className="co-source" title={quote}>
      p.{page}
    </span>
  )
}

function fact(data: CompanyNarrative, key: string): string | number | undefined {
  const entry = data.company[key]
  if (!entry || Array.isArray(entry.value)) return undefined
  return entry.value
}

function agg(data: CompanyNarrative, key: string) {
  return data.commercial.aggregates[key]
}

function money(value: number | string | undefined): string | null {
  return typeof value === 'number' ? formatCompactEur(value) : null
}

function groupOf(person: { groups: string[] }): string {
  return PEOPLE_GROUP_ORDER.find((g) => person.groups.includes(g)) ?? 'management'
}

function CustomerRow({ customer }: { customer: CompanyCustomer }) {
  const meta = [customer.segment, customer.country, customer.product].filter(Boolean).join(' · ')
  return (
    <li className="co-customer">
      <span className="co-customer__avatar" aria-hidden="true">
        {customer.name
          .split(/\s+/)
          .filter((w) => /^[A-Z]/.test(w))
          .slice(0, 2)
          .map((w) => w[0])
          .join('')}
      </span>
      <div className="co-customer__body">
        <strong>{customer.name}</strong>
        <span>{customer.detail ?? meta}</span>
        {customer.detail && meta && <span className="co-customer__meta">{meta}</span>}
      </div>
      <div className="co-customer__side">
        <span className={`co-pill co-pill--${customer.status}`}>
          {STATUS_LABEL[customer.status] ?? customer.status}
        </span>
        {money(customer.value_eur ?? undefined) && <b>{money(customer.value_eur ?? undefined)}</b>}
        <Source page={customer.page} quote={customer.quote} />
      </div>
    </li>
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
  const closed = agg(data, 'closed_pipeline_eur')
  const open = agg(data, 'open_pipeline_eur')
  const customersClosed = agg(data, 'enterprise_customers_closed')
  const recentCount = agg(data, 'recent_deals_count')
  const recentValue = agg(data, 'recent_deals_value_eur')
  const totalPipeline = closed && open ? closed.value + open.value : null
  const closedShare = totalPipeline && closed ? (closed.value / totalPipeline) * 100 : null

  const people = [...data.people].sort(
    (a, b) => PEOPLE_GROUP_ORDER.indexOf(groupOf(a)) - PEOPLE_GROUP_ORDER.indexOf(groupOf(b)),
  )
  const groups = PEOPLE_GROUP_ORDER.filter((g) => people.some((p) => groupOf(p) === g))
  const named = data.commercial.customers.filter((c) => !c.unnamed)
  const unnamed = data.commercial.customers.filter((c) => c.unnamed)
  const description = fact(data, 'description')
  const markets = data.company.markets?.value
  const listing: [string, string | number | undefined][] = [
    ['Market', fact(data, 'market')],
    ['Ticker', fact(data, 'ticker')],
    ['ISIN', fact(data, 'isin')],
    ['Admission', fact(data, 'admission_date')],
    ['Issued share capital', fact(data, 'issued_share_capital')?.toLocaleString?.('en-IE')],
    ['Sponsor', fact(data, 'sponsor')],
  ]
  const corporate: [string, string | number | undefined][] = [
    ['Registered office', fact(data, 'registered_office')],
    ['Registration no.', fact(data, 'registration_number')],
    ['Legal form', fact(data, 'legal_form')],
    ['Fiscal year end', fact(data, 'fiscal_year_end')],
    ['Accounting framework', fact(data, 'accounting_framework')],
    ['Founded', fact(data, 'founded')],
    ['Investor relations', fact(data, 'ir_email')],
  ]

  return (
    <div className="dashboard company">
      <section className="dashboard__hero">
        <div className="container">
          <div className="dashboard__hero-row">
            <div>
              <span className="dashboard__eyebrow">EXECUTIVE OVERVIEW · SENUS PLC</span>
              <h1 className="dashboard__title">Company &amp; Leadership</h1>
              {typeof description === 'string' && (
                <p className="co-hero__lead">
                  {description.charAt(0).toUpperCase() + description.slice(1)}
                  {Array.isArray(markets) && ` · ${markets.join(' · ')}`}
                </p>
              )}
            </div>
            <div className="co-hero__meta">
              <span>Source</span>
              <strong>{data.document.filename}</strong>
              <small>{data.document.period_label ?? selected}</small>
            </div>
          </div>
        </div>
      </section>

      <div className="container dashboard__body">
        {current && (
          <section className="kpi-grid">
            <KpiCard
              label="Group Revenue"
              value={formatCompactEur(current.revenue)}
              sublabel={current.revenue_yoy_period_label ? `vs ${current.revenue_yoy_period_label}` : current.period.label}
              trend={current.revenue_yoy_change_pct}
              origin={current.revenue_origin}
              icon={Euro}
              accent="teal"
            />
            <KpiCard
              label="Gross Margin"
              value={formatPct(current.gross_margin)}
              sublabel={`Gross profit ${formatCompactEur(current.gross_profit)}`}
              origin={current.gross_margin_origin}
              icon={TrendingUp}
              accent="green"
            />
            <KpiCard
              label="Operating Loss"
              value={formatCompactEur(current.operating_loss)}
              sublabel={current.period.label}
              origin={current.operating_loss_origin}
              icon={Landmark}
              accent="peach"
            />
            <KpiCard
              label="Cash"
              value={formatCompactEur(current.cash)}
              sublabel={current.debt !== null ? `Bank debt ${formatCompactEur(current.debt)}` : current.period.label}
              origin={current.cash_origin}
              icon={Wallet}
              accent="default"
            />
          </section>
        )}

        <div className="co-grid">
          <div className="co-col">
            <section className="panel">
              <div className="panel__head">
                <h3>Commercial pipeline</h3>
                {customersClosed && (
                  <span className="panel__hint">
                    {customersClosed.value} enterprise customers closed in the period{' '}
                    <Source page={customersClosed.page} quote={customersClosed.quote} />
                  </span>
                )}
              </div>
              <div className="co-pipeline">
                <div className="co-pipeline__figure">
                  <span>Closed in the period</span>
                  <strong>{closed ? formatCompactEur(closed.value) : 'n/d'}</strong>
                  {closed && <small>approx. <Source page={closed.page} quote={closed.quote} /></small>}
                </div>
                <div className="co-pipeline__figure co-pipeline__figure--open">
                  <span>Open pipeline</span>
                  <strong>{open ? formatCompactEur(open.value) : 'n/d'}</strong>
                  {open && <small>approx. <Source page={open.page} quote={open.quote} /></small>}
                </div>
                {recentCount && recentValue && (
                  <div className="co-pipeline__figure">
                    <span>Deals in Nov–Dec 2025</span>
                    <strong>{formatCompactEur(recentValue.value)}</strong>
                    <small>
                      {recentCount.value} deals <Source page={recentValue.page} quote={recentValue.quote} />
                    </small>
                  </div>
                )}
              </div>
              {closedShare !== null && (
                <div className="co-bar" aria-label="Closed versus open pipeline">
                  <i style={{ width: `${closedShare}%` }} />
                  <span>
                    {closedShare.toFixed(0)}% closed · {(100 - closedShare).toFixed(0)}% open{' '}
                    <em>calculated</em>
                  </span>
                </div>
              )}
            </section>

            <section className="panel">
              <div className="panel__head">
                <h3>Customers &amp; partners</h3>
                <span className="panel__hint">{named.length} named in the report</span>
              </div>
              <ul className="co-customers">
                {named.map((c) => (
                  <CustomerRow key={c.name} customer={c} />
                ))}
                {unnamed.map((c) => (
                  <CustomerRow key={c.name} customer={c} />
                ))}
              </ul>
              {fact(data, 'fy25_customer_accounts') !== undefined && (
                <p className="co-note">
                  {fact(data, 'fy25_customer_accounts')} customer accounts in FY25
                  {typeof fact(data, 'fy25_revenue_eur') === 'number' &&
                    ` with revenue of ${formatCompactEur(fact(data, 'fy25_revenue_eur') as number)}`}
                  .
                </p>
              )}
            </section>

            {data.drivers.length > 0 && (
              <section className="panel">
                <div className="panel__head">
                  <h3>Operating context</h3>
                  <span className="panel__hint">As stated by the Board</span>
                </div>
                <ul className="co-drivers">
                  {data.drivers.map((d) => (
                    <li key={d.text}>
                      <span className={`co-topic co-topic--${d.topic}`}>{d.topic.replace('_', ' ')}</span>
                      <p>{d.text}</p>
                      <Source page={d.page} quote={d.quote} />
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>

          <aside className="co-col">
            <section className="panel">
              <div className="panel__head">
                <h3>Strategic milestones</h3>
                <span className="panel__hint">{data.milestones.length} tracked</span>
              </div>
              <div className="co-milestones">
                {data.milestones
                  .filter((m) => m.key !== 'senus_2030')
                  .map((m, i) => (
                    <article key={m.key}>
                      <div className="co-milestone__top">
                        <span className="co-milestone__n">{String(i + 1).padStart(2, '0')}</span>
                        <span className={`co-pill co-pill--${m.status}`}>
                          {MILESTONE_STATE[m.status] ?? m.status}
                        </span>
                      </div>
                      <h4>{m.title}</h4>
                      <dl>
                        {Object.entries(m.details).map(([k, v]) => (
                          <div key={k}>
                            <dt>{k.replace(/_eur$/, '').replace(/_/g, ' ')}</dt>
                            <dd>{k.endsWith('_eur') ? formatCompactEur(v as number) : String(v)}</dd>
                          </div>
                        ))}
                      </dl>
                      <Source page={m.page} quote={m.quote} />
                    </article>
                  ))}
              </div>
            </section>

            {data.strategy && (
              <section className="panel co-strategy">
                <div className="panel__head">
                  <h3>{data.strategy.name}</h3>
                  <span className="panel__hint">
                    {data.strategy.period} · p.{data.strategy.page}
                  </span>
                </div>
                <div className="co-strategy__path">
                  <div>
                    <strong>{data.strategy.min_cagr_pct !== null ? `≥${data.strategy.min_cagr_pct}%` : 'n/d'}</strong>
                    <span>annual compound sales growth</span>
                  </div>
                  <div>
                    <strong>{data.strategy.ebitda_positive_fy ?? 'n/d'}</strong>
                    <span>EBITDA positive expected</span>
                  </div>
                </div>
                <p className="co-note">{data.strategy.paragraphs[0]}</p>
              </section>
            )}

            <section className="panel">
              <div className="panel__head">
                <h3>
                  <Users size={16} aria-hidden="true" /> Leadership
                </h3>
              </div>
              {groups.map((g) => (
                <div key={g} className="co-people">
                  <p>{PEOPLE_GROUP_LABEL[g]}</p>
                  <ul>
                    {people
                      .filter((p) => groupOf(p) === g)
                      .map((p) => (
                        <li key={p.name}>
                          <strong>
                            {p.title && `${p.title} `}
                            {p.name}
                          </strong>
                          <span>{p.roles.join(' · ')}</span>
                          <Source page={p.page} quote={p.quote} />
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
            </section>

            <section className="panel">
              <div className="panel__head">
                <h3>
                  <Building2 size={16} aria-hidden="true" /> Corporate &amp; listing
                </h3>
              </div>
              <dl className="co-facts">
                {[...listing, ...corporate]
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
      </div>
    </div>
  )
}
