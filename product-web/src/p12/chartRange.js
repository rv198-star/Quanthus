// Coordinate-only helpers for PriceChart Y-axis scaling and form price reset.
// Displayed amounts always stay the server's decimal strings.

export function computeIntradayRange (points, prevClose) {
  const prices = (points || [])
    .map(pt => Number(pt && pt.price))
    .filter(v => Number.isFinite(v) && v > 0)
  const prev = prevClose !== null && prevClose !== undefined && prevClose !== ''
    ? Number(prevClose)
    : null
  const validPrev = prev !== null && Number.isFinite(prev) && prev > 0 ? prev : null
  if (validPrev !== null) {
    let maxDiff = 0
    for (const p of prices) {
      const d = Math.abs(p - validPrev)
      if (d > maxDiff) maxDiff = d
    }
    const minHalf = Math.max(validPrev * 0.004, 0.02)
    const half = Math.max(maxDiff * 1.15, minHalf)
    return { min: Math.max(0.01, validPrev - half), max: validPrev + half }
  }
  if (!prices.length) {
    return { min: 0, max: 1 }
  }
  const min = Math.min(...prices)
  const max = Math.max(...prices)
  const span = max - min
  const margin = span > 0 ? span * 0.12 : Math.max(max * 0.005, 0.02)
  return { min: Math.max(0.01, min - margin), max: max + margin }
}

export function computeDailyRange (bars) {
  const values = (bars || [])
    .flatMap(bar => [Number(bar && bar.high), Number(bar && bar.low)])
    .filter(v => Number.isFinite(v) && v > 0)
  if (!values.length) {
    return { min: 0, max: 1 }
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const margin = span > 0 ? span * 0.08 : Math.max(max * 0.02, 0.1)
  return { min: Math.max(0.01, min - margin), max: max + margin }
}

export function partitionChartLevels (levels, range) {
  const visible = []
  const offRange = []
  if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max)) {
    return { visible, offRange }
  }
  for (const level of (levels || [])) {
    if (!level || !Number.isFinite(level.value)) continue
    if (level.value >= range.min && level.value <= range.max) {
      visible.push(level)
    } else {
      offRange.push({
        ...level,
        direction: level.value > range.max ? 'above' : 'below'
      })
    }
  }
  return { visible, offRange }
}

// Determine the initial simulated-order limit_price when switching or resetting.
// Only an active system plan or a live quote belonging to the SAME security may prefill it.
export function resolveResetLimitPrice ({ plan, liveQuote, securityCode }) {
  if (plan && plan.price_cap) return String(plan.price_cap)
  if (
    liveQuote &&
    liveQuote.last_price &&
    securityCode &&
    String(liveQuote.security_code || '') === String(securityCode)
  ) {
    return String(liveQuote.last_price)
  }
  return ''
}
