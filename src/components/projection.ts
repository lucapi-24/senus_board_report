export interface Projection {
  years: string[]
  target: number[]
}

// Compound growth of the strategy target from the last reported fiscal year (FY25).
export function targetSeries(baseRevenue: number, firstFy: number, lastFy: number, cagrPct: number): Projection {
  const steps = lastFy - firstFy + 1
  return {
    years: ['FY25', ...Array.from({ length: steps }, (_, i) => `FY${String(firstFy + i).slice(2)}`)],
    target: Array.from({ length: steps + 1 }, (_, i) => baseRevenue * (1 + cagrPct / 100) ** i),
  }
}
