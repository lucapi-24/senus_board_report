export interface ProjectionInput {
  baseRevenue: number // FY25 revenue
  firstFy: number // first strategy year, e.g. 2026
  lastFy: number // last strategy year, e.g. 2030
  targetCagrPct: number
  growthPct: number | null // year-on-year growth between comparable half-years
  currentHalf: number | null // latest half-year revenue (first half of the fiscal year)
  comparableHalf: number | null // same half a year earlier
  closedPipeline: number | null
  openPipeline: number | null
  assumptions: Assumptions
}

export interface Assumptions {
  conversionMonths: number // months over which a contract value is recognised
  openProbabilityPct: number // chance an open pipeline deal closes
  renews: boolean // contracts keep billing at the same monthly rate after the term
}

export interface Projection {
  years: string[]
  target: number[]
  base: number[] | null
  pipeline: number[] | null
  usesSeasonality: boolean
}

// Fiscal year k runs Jul(k-1) to Jun(k). Month 0 is July 2025 (start of FY26).
function monthsBilled(startMonth: number, termMonths: number, renews: boolean, fyIndex: number): number {
  const from = fyIndex * 12
  const to = from + 12
  const end = renews ? Infinity : startMonth + termMonths
  return Math.max(0, Math.min(to, end) - Math.max(from, startMonth))
}

export function buildProjection(input: ProjectionInput): Projection {
  const { baseRevenue, firstFy, lastFy, targetCagrPct, growthPct } = input
  const steps = lastFy - firstFy + 1
  const years = ['FY25', ...Array.from({ length: steps }, (_, i) => `FY${String(firstFy + i).slice(2)}`)]
  const target = Array.from({ length: steps + 1 }, (_, i) => baseRevenue * (1 + targetCagrPct / 100) ** i)

  if (growthPct === null) return { years, target, base: null, pipeline: null, usesSeasonality: false }

  // The first half of the year is seasonally lighter, so scale it by last year's full-year/half-year ratio.
  const usesSeasonality = !!input.currentHalf && !!input.comparableHalf
  const firstYear = usesSeasonality
    ? (input.currentHalf as number) * (baseRevenue / (input.comparableHalf as number))
    : baseRevenue * (1 + growthPct / 100)
  const base = [baseRevenue, ...Array.from({ length: steps }, (_, i) => firstYear * (1 + growthPct / 100) ** i)]

  const { conversionMonths, openProbabilityPct, renews } = input.assumptions
  const term = Math.max(1, conversionMonths)
  const closed = input.closedPipeline ?? 0
  const open = (input.openPipeline ?? 0) * (openProbabilityPct / 100)
  const pipeline = base.map((value, i) => {
    if (i === 0) return value
    const fyIndex = i - 1
    // Closed deals start billing after the report date (January); open ones assumed six months later.
    const added =
      (closed / term) * monthsBilled(6, term, renews, fyIndex) + (open / term) * monthsBilled(12, term, renews, fyIndex)
    return value + added
  })
  return { years, target, base, pipeline, usesSeasonality }
}
