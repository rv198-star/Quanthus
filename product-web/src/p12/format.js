// Presentation-only formatting. Amounts stay decimal strings; no float arithmetic on money.
import { LABELS, REASON_TEXTS } from './contract.generated.js'

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
  return REASON_TEXTS[code] || code
}

export function label (group, code) {
  if (code === null || code === undefined) return '—'
  return LABELS[group]?.[code] || code
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
