// Presentation-only formatting. Amounts stay decimal strings; no float arithmetic on money.
import { LABELS, REASON_TEXTS } from './contract.generated.js'
import { MISSING_FIELD_LABELS, PAPER_LABELS, PAPER_REASON_TEXTS } from './paperContract.js'

function groupDigits (digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

export function formatMoney (text) {
  if (text === null || text === undefined) return '—'
  const negative = text.startsWith('-')
  const [whole, fraction = ''] = (negative ? text.slice(1) : text).split('.')
  return `${negative ? '-' : ''}¥${groupDigits(whole)}${fraction ? '.' + fraction : ''}`
}

export function formatSignedMoney (text) {
  if (text === null || text === undefined) return '—'
  return text.startsWith('-') ? formatMoney(text) : `+${formatMoney(text)}`
}

export function formatPrice (text) {
  return text === null || text === undefined ? '—' : text
}

// "0.4942" -> "49.42%"; "1.1800" -> "118.00%"; "0.6" -> "60.00%". Pure string shifting.
export function ratioToPercent (text) {
  if (text === null || text === undefined) return '—'
  const negative = text.startsWith('-')
  const [whole, fraction = ''] = (negative ? text.slice(1) : text).split('.')
  const padded = (fraction + '0000').slice(0, 4)
  const digits = (whole + padded).replace(/^0+(?=\d{3})/, '')
  const integer = digits.slice(0, -2).replace(/^0+(?=\d)/, '') || '0'
  return `${negative ? '-' : ''}${integer}.${digits.slice(-2)}%`
}

export function reasonText (code) {
  if (!code) return ''
  return PAPER_REASON_TEXTS[code] || REASON_TEXTS[code] || code
}

export function label (group, code) {
  if (code === null || code === undefined) return '—'
  return PAPER_LABELS[group]?.[code] || LABELS[group]?.[code] || code
}

export function formatMaybe (maybe, formatter = formatMoney) {
  if (!maybe) return { text: '—', unknown: false, reason: '' }
  if (maybe.value === null) return { text: '未知', unknown: true, reason: reasonText(maybe.reason_code) }
  return { text: formatter(maybe.value), unknown: false, reason: '' }
}

export function formatTime (iso) {
  if (!iso) return '—'
  return iso.slice(0, 16).replace('T', ' ')
}

// Next calendar day, MM-DD, for an evening package whose sell runs the next
// trading day. Date-only; weekend/holiday handling stays with the market data.
export function nextDate (ymd) {
  if (!ymd) return ''
  const d = new Date(`${ymd}T00:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  d.setDate(d.getDate() + 1)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function timezoneSuffix (iso) {
  const match = /([+-]\d{2}:\d{2}|Z)$/.exec(iso || '')
  return match ? match[1] : '+08:00'
}

// Validate user decimal text and normalise to exactly two places without floats.
export function normaliseMoneyInput (text) {
  const value = String(text ?? '').trim()
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  return `${whole.replace(/^0+(?=\d)/, '')}.${(fraction + '00').slice(0, 2)}`
}

export function formatQuantity (value) {
  return value === null || value === undefined ? '—' : `${groupDigits(String(value))} 股`
}

// Exact 2-decimal cents arithmetic via BigInt (never floats).
export function moneyToCents (text) {
  const clean = String(text ?? '').trim()
  if (!/^-?\d+(\.\d{1,4})?$/.test(clean)) return null
  const negative = clean.startsWith('-')
  const body = negative ? clean.slice(1) : clean
  const [whole, fraction = ''] = body.split('.')
  const centsDigits = (fraction + '00').slice(0, 2)
  const raw = BigInt(whole) * 100n + BigInt(centsDigits)
  return negative ? -raw : raw
}

export function centsToMoney (cents) {
  if (typeof cents !== 'bigint') return null
  const negative = cents < 0n
  const abs = negative ? -cents : cents
  const whole = (abs / 100n).toString()
  const frac = (abs % 100n).toString().padStart(2, '0')
  return `${negative ? '-' : ''}${whole}.${frac}`
}

export function multiplyPriceQty (priceText, qtyInt) {
  const cents = moneyToCents(priceText)
  if (cents === null || !Number.isInteger(qtyInt) || qtyInt < 0) return null
  return centsToMoney(cents * BigInt(qtyInt))
}

export function subtractMoney (leftText, rightText) {
  const left = moneyToCents(leftText)
  const right = moneyToCents(rightText)
  if (left === null || right === null) return null
  return centsToMoney(left - right)
}

export function compareMoney (leftText, rightText) {
  const left = moneyToCents(leftText)
  const right = moneyToCents(rightText)
  if (left === null || right === null) return null
  if (left === right) return 0
  return left > right ? 1 : -1
}

export function calcQuoteChange (lastPrice, prevClose) {
  const last = moneyToCents(lastPrice)
  const prev = moneyToCents(prevClose)
  if (last === null || prev === null || prev <= 0n) return null
  const diff = last - prev
  const absDiff = diff < 0n ? -diff : diff
  const sign = diff > 0n ? '+' : (diff < 0n ? '-' : '')
  const diffStr = `${sign}${centsToMoney(absDiff)}`
  // Basis points rounded half-up via integer math: (absDiff * 10000 + prev / 2) / prev
  const bps = (absDiff * 10000n + prev / 2n) / prev
  const pctWhole = (bps / 100n).toString()
  const pctFrac = (bps % 100n).toString().padStart(2, '0')
  const pctStr = `${sign}${pctWhole}.${pctFrac}%`
  const direction = diff > 0n ? 'up' : (diff < 0n ? 'down' : 'flat')
  return { diffText: diffStr, pctText: pctStr, direction }
}

export function normalizeSecurityName (name) {
  if (!name) return ''
  return String(name).replace(/\u3000+/g, ' ').replace(/\s{2,}/g, ' ').trim()
}

export function isSyntheticItem (item, scopeMode) {
  if (scopeMode === 'synthetic') return true
  if (!item) return false
  if (String(item.opportunity_id || '').startsWith('OPP-SYN-')) return true
  if (String(item.security_name || '').startsWith('合成')) return true
  if (item.chart && item.chart.price_basis === 'unadjusted_synthetic') return true
  return false
}

export function inferExchangeLabel (securityCode, explicitExchange, synthetic = false) {
  if (synthetic) return '演练示例代号'
  if (explicitExchange) return explicitExchange
  const code = String(securityCode || '').trim()
  if (/^(60|68|90)\d{4}$/.test(code)) return '上交所'
  if (/^(00|30|20)\d{4}$/.test(code)) return '深交所'
  if (/^(43|83|87|92)\d{4}$/.test(code)) return '北交所'
  return 'A 股'
}

export function humanizeBucketId (bucketId) {
  const m = /^bucket-0*(\d+)$/i.exec(String(bucketId || ''))
  return m ? `${m[1]} 号资金桶` : String(bucketId || '资金桶')
}

export function humanizeMissingField (field) {
  return MISSING_FIELD_LABELS[field] || field
}
