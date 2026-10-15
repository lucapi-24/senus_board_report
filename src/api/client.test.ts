import { describe, it, expect } from 'vitest'
import { toNumber, formatEur, formatPct } from './client'

describe('toNumber', () => {
  it('parses decimal strings from the API', () => {
    expect(toNumber('354813.00')).toBe(354813)
    expect(toNumber('-483753.00')).toBe(-483753)
  })

  it('returns 0 for null/undefined/invalid', () => {
    expect(toNumber(null)).toBe(0)
    expect(toNumber(undefined)).toBe(0)
    expect(toNumber('n/a')).toBe(0)
  })
})

describe('formatPct', () => {
  it('formats with one decimal by default', () => {
    expect(formatPct(81.7197)).toBe('81.7%')
  })

  it('renders an em dash for missing values', () => {
    expect(formatPct(null)).toBe('—')
    expect(formatPct(undefined)).toBe('—')
  })
})

describe('formatEur', () => {
  it('formats thousands with a euro symbol', () => {
    const out = formatEur(354813)
    expect(out).toContain('354,813')
    expect(out).toContain('€')
  })

  it('formats negatives', () => {
    const out = formatEur(-485144)
    expect(out).toContain('485,144')
    expect(out).toContain('-')
  })
})
