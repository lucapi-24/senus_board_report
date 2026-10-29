import { describe, expect, it } from 'vitest'
import { targetSeries } from './projection'

describe('targetSeries', () => {
  it('compounds the target from FY25 to the last strategy year', () => {
    const p = targetSeries(1000, 2026, 2030, 50)
    expect(p.years).toEqual(['FY25', 'FY26', 'FY27', 'FY28', 'FY29', 'FY30'])
    expect(p.target[0]).toBe(1000)
    expect(p.target[5]).toBeCloseTo(1000 * 1.5 ** 5)
  })
})
