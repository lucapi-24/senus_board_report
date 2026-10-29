import { describe, expect, it } from 'vitest'
import { buildProjection, type ProjectionInput } from './projection'

const input: ProjectionInput = {
  baseRevenue: 836_991,
  firstFy: 2026,
  lastFy: 2030,
  targetCagrPct: 50,
  growthPct: 4.1,
  currentHalf: 354_813,
  comparableHalf: 340_931,
  closedPipeline: 700_000,
  openPipeline: 500_000,
  assumptions: { conversionMonths: 12, openProbabilityPct: 30, renews: false },
}

describe('buildProjection', () => {
  it('compounds the target from FY25', () => {
    const p = buildProjection(input)
    expect(p.years).toEqual(['FY25', 'FY26', 'FY27', 'FY28', 'FY29', 'FY30'])
    expect(p.target[1]).toBeCloseTo(836_991 * 1.5)
  })

  it('scales the first half by last year seasonality', () => {
    const p = buildProjection(input)
    expect(p.usesSeasonality).toBe(true)
    expect(p.base?.[1]).toBeCloseTo(354_813 * (836_991 / 340_931), 0)
  })

  it('falls back to plain growth without comparable half-years', () => {
    const p = buildProjection({ ...input, currentHalf: null })
    expect(p.usesSeasonality).toBe(false)
    expect(p.base?.[1]).toBeCloseTo(836_991 * 1.041)
  })

  it('spreads closed deals over the term starting in January', () => {
    const p = buildProjection({ ...input, openPipeline: 0 })
    const extra = (i: number) => (p.pipeline as number[])[i] - (p.base as number[])[i]
    expect(extra(1)).toBeCloseTo(350_000) // Jan-Jun 2026
    expect(extra(2)).toBeCloseTo(350_000) // Jul-Dec 2026
    expect(extra(3)).toBeCloseTo(0)
  })

  it('weights open deals by probability and starts them later', () => {
    const p = buildProjection({ ...input, closedPipeline: 0 })
    const extra = (i: number) => (p.pipeline as number[])[i] - (p.base as number[])[i]
    expect(extra(1)).toBeCloseTo(0)
    expect(extra(2)).toBeCloseTo(150_000) // 500k x 30% over the next 12 months
  })

  it('keeps billing after the term when contracts renew', () => {
    const p = buildProjection({ ...input, openPipeline: 0, assumptions: { ...input.assumptions, renews: true } })
    expect((p.pipeline as number[])[5] - (p.base as number[])[5]).toBeCloseTo(700_000)
  })

  it('returns only the target when growth is unknown', () => {
    const p = buildProjection({ ...input, growthPct: null })
    expect(p.base).toBeNull()
    expect(p.pipeline).toBeNull()
  })
})
