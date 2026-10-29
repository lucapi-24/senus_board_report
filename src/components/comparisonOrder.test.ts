import { describe, expect, it } from 'vitest'
import { orderChronologically } from './comparisonOrder'

const p = (label: string, end_date: string) => ({ label, period: { end_date } })

describe('orderChronologically', () => {
  const hy25 = p('HY2025', '2025-06-30')
  const hy26 = p('HY2026', '2026-06-30')

  it('keeps the selected period as base when the picked one is later', () => {
    expect(orderChronologically(hy25, hy26)).toEqual({ base: hy25, other: hy26 })
  })

  it('swaps so the earlier period is always the base', () => {
    expect(orderChronologically(hy26, hy25)).toEqual({ base: hy25, other: hy26 })
  })

  it('handles nothing picked yet', () => {
    expect(orderChronologically(hy26, null)).toEqual({ base: null, other: hy26 })
  })
})
