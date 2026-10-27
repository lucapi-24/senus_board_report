import { describe, it, expect } from 'vitest'
import { toNumber, formatEur, formatPct } from './client'

describe('toNumber', () => {
  it('parses decimal strings from the API', () => {
    expect(toNumber('354813.00')).toBe(354813)
    expect(toNumber('-483753.00')).toBe(-483753)
  })

  it('preserves null/undefined/invalid as unavailable', () => {
    expect(toNumber(null)).toBeNull()
    expect(toNumber(undefined)).toBeNull()
    expect(toNumber('n/a')).toBeNull()
  })
})

describe('formatPct', () => {
  it('formats with one decimal by default', () => {
    expect(formatPct(81.7197)).toBe('81.7%')
  })

  it('renders n/d for missing values', () => {
    expect(formatPct(null)).toBe('n/d')
    expect(formatPct(undefined)).toBe('n/d')
    expect(formatPct(Number.NaN)).toBe('n/d')
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

  it('renders missing amounts as n/d instead of zero', () => {
    expect(formatEur(null)).toBe('n/d')
  })
})
