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
  Cell,
  ReferenceLine,
} from 'recharts'
import {
  api,
  type FinancialSummary,
  type PeriodDetail,
  toNumber,
  formatCompactEur,
  formatPct,
} from '../api/client'

const POSITIVE = '#20948B'
const NEGATIVE = '#E08A3C'
const TOTAL = '#023C28'

type Metric = {
  label: string
  kind: 'eur' | 'pct'
  get: (s: FinancialSummary) => number | null
}

const METRICS: Metric[] = [
  { label: 'Revenue', kind: 'eur', get: (s) => toNumber(s.revenue) },
  { label: 'Gross profit', kind: 'eur', get: (s) => toNumber(s.gross_profit) },
  { label: 'Operating loss', kind: 'eur', get: (s) => toNumber(s.operating_loss) },
  { label: 'Gross margin', kind: 'pct', get: (s) => s.gross_margin ?? null },
  { label: 'Operating margin', kind: 'pct', get: (s) => s.operating_margin ?? null },
  { label: 'Cash', kind: 'eur', get: (s) => toNumber(s.cash) },
  { label: 'Debt', kind: 'eur', get: (s) => toNumber(s.debt) },
]

const line = (detail: PeriodDetail | null, name: string) =>
  toNumber(detail?.pnl.find((p) => p.line_item === name)?.value_eur) ?? 0

const signed = (v: number, text: string) => `${v > 0 ? '+' : ''}${text}`

function PeriodSelect({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string
  label: string
  value: string
  options: FinancialSummary[]
  onChange: (v: string) => void
}) {
  return (
    <div className="compare__select">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select a period</option>
        {options.map((s) => (
          <option key={s.period.label} value={s.period.label}>
            {s.period.label} · {s.duration_months} months
          </option>
        ))}
      </select>
    </div>
  )
}

export function PeriodComparison({
  summary,
  selected,
}: {
  summary: FinancialSummary[]
  selected: string
}) {
  const [otherPick, setOtherPick] = useState('')
  const [details, setDetails] = useState<{ base: PeriodDetail; other: PeriodDetail } | null>(null)

  const selectedPeriod = summary.find((s) => s.period.label === selected) ?? null
  const picked = summary.find((s) => s.period.label === otherPick) ?? null
  // Comparisons always read forward in time: the earlier period is the base.
  const pickedIsLater = Boolean(
    selectedPeriod && picked && picked.period.end_date >= selectedPeriod.period.end_date,
  )
  const base = pickedIsLater ? selectedPeriod : picked
  const other = pickedIsLater ? picked : selectedPeriod
  const baseLabel = base?.period.label ?? ''
  const otherLabel = other?.period.label ?? ''
  const comparing = Boolean(base && other && baseLabel !== otherLabel)

  useEffect(() => {
    if (!comparing) return
    let active = true
    Promise.all([api.periodDetail(baseLabel), api.periodDetail(otherLabel)])
      .then(([b, o]) => {
        if (active) setDetails({ base: b, other: o })
      })
      .catch(() => {
        if (active) setDetails(null)
      })
    return () => {
      active = false
      setDetails(null)
    }
  }, [comparing, baseLabel, otherLabel])

  const rows = useMemo(() => {
    if (!base || !other) return []
    return METRICS.map((m) => {
      const a = m.get(base)
      const b = m.get(other)
      const diff = a !== null && b !== null ? b - a : null
      const pct =
        m.kind === 'eur' && diff !== null && a !== null && a !== 0 ? (diff / Math.abs(a)) * 100 : null
      return { ...m, a, b, diff, pct }
    })
  }, [base, other])

  const bridge = useMemo(() => {
    if (!details) return []
    const { base: a, other: b } = details
    const steps = [
      { name: 'Revenue', delta: line(b, 'Revenue') - line(a, 'Revenue') },
      { name: 'Cost of sales', delta: -(line(b, 'Cost of sales') - line(a, 'Cost of sales')) },
      {
        name: 'Distribution',
        delta: -(line(b, 'Distribution costs') - line(a, 'Distribution costs')),
      },
      {
        name: 'Administrative',
        delta: -(line(b, 'Administrative expenses') - line(a, 'Administrative expenses')),
      },
      {
        name: 'Other income',
        delta: line(b, 'Other operating income') - line(a, 'Other operating income'),
      },
    ].filter((s) => s.delta !== 0)
    const start = line(a, 'Group operating loss')
    const end = line(b, 'Group operating loss')
    let running = start
    const data = [
      { name: baseLabel, range: [Math.min(0, start), Math.max(0, start)], value: start, kind: 'total' },
    ]
    for (const s of steps) {
      const next = running + s.delta
      data.push({
        name: s.name,
        range: [Math.min(running, next), Math.max(running, next)],
        value: s.delta,
        kind: s.delta >= 0 ? 'up' : 'down',
      })
      running = next
    }
    data.push({
      name: otherLabel,
      range: [Math.min(0, end), Math.max(0, end)],
      value: end,
      kind: 'total',
    })
    return data
  }, [details, baseLabel, otherLabel])

  const pctData = rows
    .filter((r) => r.kind === 'eur' && r.pct !== null)
    .map((r) => ({ name: r.label, pct: Number((r.pct as number).toFixed(1)) }))

  const groupedData = rows
    .filter((r) => r.kind === 'eur')
    .map((r) => ({ name: r.label, [baseLabel]: r.a, [otherLabel]: r.b }))

  const mixedDurations = base && other && base.duration_months !== other.duration_months

  const colour = (kind: string) => (kind === 'total' ? TOTAL : kind === 'up' ? POSITIVE : NEGATIVE)

  return (
    <section className="panel comparison">
      <div className="panel__head">
        <h2>Compare periods</h2>
        <span className="panel__hint">Calculated from reported figures · two periods</span>
      </div>

      <div className="compare__form">
        <PeriodSelect
          id="compare-other"
          label={`Compare ${selected} with`}
          value={otherPick}
          options={summary.filter((s) => s.period.label !== selected)}
          onChange={setOtherPick}
        />
      </div>

      {!comparing && <div className="panel__empty">Choose a period to compare it with {selected}.</div>}

      {comparing && base && other && (
        <>
          {mixedDurations && (
            <p className="provenance__note">
              The periods have different lengths ({base.duration_months} vs {other.duration_months}{' '}
              months), so absolute differences are not like-for-like.
            </p>
          )}

          <table className="compare__table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>{base.period.label}</th>
                <th>{other.period.label}</th>
                <th>Change</th>
                <th>Change %</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const show = (v: number | null) =>
                  v === null ? 'n/d' : r.kind === 'eur' ? formatCompactEur(v) : formatPct(v)
                return (
                  <tr key={r.label}>
                    <td>{r.label}</td>
                    <td>{show(r.a)}</td>
                    <td>{show(r.b)}</td>
                    <td className={r.diff === null ? '' : r.diff < 0 ? 'is-neg' : 'is-pos'}>
                      {r.diff === null
                        ? 'n/d'
                        : signed(
                            r.diff,
                            r.kind === 'eur' ? formatCompactEur(r.diff) : `${r.diff.toFixed(1)} pp`,
                          )}
                    </td>
                    <td className={r.pct === null ? '' : r.pct < 0 ? 'is-neg' : 'is-pos'}>
                      {r.pct === null ? '–' : signed(r.pct, `${r.pct.toFixed(1)}%`)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="panel compare__trends">
            <div className="panel__head">
              <h2>Operating result bridge</h2>
              <span className="panel__hint">
                What moved the operating result from {base.period.label} to {other.period.label}
              </span>
            </div>
            {bridge.length ? (
              <ResponsiveContainer width="100%" height={320}>
                <BarChart data={bridge} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eceeed" vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: '#6b7370', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis
                    tick={{ fill: '#6b7370', fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => formatCompactEur(v)}
                  />
                  <ReferenceLine y={0} stroke="#9aa3a0" />
                  <Tooltip
                    formatter={(_v, _n, item) =>
                      formatCompactEur((item.payload as { value: number }).value)
                    }
                  />
                  <Bar dataKey="range" radius={[4, 4, 4, 4]}>
                    {bridge.map((d) => (
                      <Cell key={d.name} fill={colour(d.kind)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="panel__empty">Loading line items…</div>
            )}
          </div>

          <div className="grid-2 compare__trends">
            <div className="panel">
              <div className="panel__head">
                <h2>% change by metric</h2>
                <span className="panel__hint">
                  {other.period.label} vs {base.period.label}
                </span>
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart
                  data={pctData}
                  layout="vertical"
                  margin={{ top: 8, right: 16, left: 24, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#eceeed" horizontal={false} />
                  <XAxis
                    type="number"
                    tick={{ fill: '#6b7370', fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => `${v}%`}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={100}
                    tick={{ fill: '#6b7370', fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <ReferenceLine x={0} stroke="#9aa3a0" />
                  <Tooltip formatter={(v) => `${Number(v) > 0 ? '+' : ''}${v}%`} />
                  <Bar dataKey="pct" radius={4}>
                    {pctData.map((d) => (
                      <Cell key={d.name} fill={d.pct >= 0 ? POSITIVE : NEGATIVE} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="panel">
              <div className="panel__head">
                <h2>Side by side</h2>
                <span className="panel__hint">Reported amounts</span>
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={groupedData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eceeed" vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: '#6b7370', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis
                    tick={{ fill: '#6b7370', fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => formatCompactEur(v)}
                  />
                  <ReferenceLine y={0} stroke="#9aa3a0" />
                  <Tooltip formatter={(v) => formatCompactEur(Number(v))} />
                  <Legend />
                  <Bar dataKey={baseLabel} fill="#8FBF9F" radius={[4, 4, 0, 0]} />
                  <Bar dataKey={otherLabel} fill={POSITIVE} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
