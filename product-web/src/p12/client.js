// P12 desktop client: one call path per contract operation; responses are contract-checked.
import { loadSession } from '../api.js'
import { OPERATIONS, P12_SCHEMA_VERSION } from './contract.generated.js'
import { PAPER_OPERATIONS } from './paperContract.js'
import { validate } from './validate.js'

export class P12Error extends Error {
  constructor (status, reasonCode, detail) {
    super(reasonCode || 'request_failed')
    this.status = status
    this.reasonCode = reasonCode
    this.detail = detail
  }
}

async function call (operationId, accountId, body) {
  const spec = OPERATIONS[operationId]
  const path = spec.path.replace('{account_id}', encodeURIComponent(accountId || ''))
  const session = loadSession()
  const headers = { Accept: 'application/json' }
  if (session) headers.Authorization = `Bearer ${session.token}`
  if (body) headers['Content-Type'] = 'application/json'
  const response = await fetch(path, {
    method: spec.method,
    headers,
    body: body ? JSON.stringify({ schema_version: P12_SCHEMA_VERSION, ...body }) : undefined,
    cache: 'no-store'
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const error = payload?.error || {}
    throw new P12Error(response.status, error.reason_code || error.reason || 'request_failed', error.detail || '')
  }
  if (!payload || payload.schema_version !== P12_SCHEMA_VERSION) {
    throw new P12Error(response.status, 'SCHEMA_VERSION_MISMATCH', '')
  }
  try {
    return validate(spec.response, payload)
  } catch (error) {
    throw new P12Error(response.status, 'SCHEMA_VERSION_MISMATCH', error.message)
  }
}

export const listAccounts = () => call('p12.accounts.list')
export const loadWorkbench = accountId => call('p12.workbench.read', accountId)
export const loadRecords = accountId => call('p12.records.read', accountId)
export const loadFundingRules = accountId => call('p12.funding.read', accountId)
export const submitFill = (accountId, fill) => call('p12.reports.submit', accountId, fill)
export const correctFill = (accountId, correction) => call('p12.facts.correct', accountId, correction)
export const submitOrderEvent = (accountId, event) => call('p12.order_events.submit', accountId, event)

async function callPaper (operationId, params = {}, body = null) {
  const spec = PAPER_OPERATIONS[operationId]
  let path = spec.path
  for (const [key, val] of Object.entries(params)) {
    path = path.replace(`{${key}}`, encodeURIComponent(val || ''))
  }
  const session = loadSession()
  const headers = { Accept: 'application/json' }
  if (session) headers.Authorization = `Bearer ${session.token}`
  if (body) headers['Content-Type'] = 'application/json'
  let response
  try {
    response = await fetch(path, {
      method: spec.method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store'
    })
  } catch (_) {
    throw new P12Error(0, 'NETWORK_UNAVAILABLE', '网络连接异常，请确认模拟盘服务正在运行')
  }
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const error = payload?.error || {}
    const fallbackCode = (response.status === 404 || response.status === 405 || response.status === 501)
      ? 'ENDPOINT_NOT_ENABLED'
      : 'request_failed'
    throw new P12Error(
      response.status,
      error.reason_code || error.reason || payload?.reason_code || fallbackCode,
      error.detail || payload?.reason_text || payload?.detail || payload?.reason || ''
    )
  }
  return payload
}

function normaliseQuotePayload (raw, fallbackCode) {
  if (!raw || typeof raw !== 'object') return null
  const q = raw.quote && typeof raw.quote === 'object' ? raw.quote : raw
  return {
    security_code: String(raw.security_code || q.security_code || fallbackCode || ''),
    security_name: String(raw.security_name || q.security_name || ''),
    exchange: String(raw.exchange || q.exchange || ''),
    contract_version: raw.contract_version || q.contract_version || 'p12-paper-quote-v1',
    quote: {
      status: q.status || 'unavailable',
      last_price: q.last_price === null || q.last_price === undefined ? null : String(q.last_price),
      previous_close: q.previous_close === null || q.previous_close === undefined ? null : String(q.previous_close),
      open: q.open === null || q.open === undefined ? null : String(q.open),
      high: q.high === null || q.high === undefined ? null : String(q.high),
      low: q.low === null || q.low === undefined ? null : String(q.low),
      volume_lots: q.volume_lots === null || q.volume_lots === undefined ? null : String(q.volume_lots),
      quote_time: q.quote_time || null,
      received_at: q.received_at || null,
      source: q.source || 'tencent',
      market_state: q.market_state || 'closed',
      stale: Boolean(q.stale),
      reason: q.reason || null
    }
  }
}

export function extractHm (value) {
  if (!value) return ''
  const str = String(value)
  const mIso = /T(\d{2}:\d{2})/.exec(str)
  if (mIso) return mIso[1]
  const mSpace = /\s(\d{2}:\d{2})/.exec(str)
  if (mSpace) return mSpace[1]
  const mHm = /^(\d{2}:\d{2})/.exec(str)
  if (mHm) return mHm[1]
  const mCompact = /^\d{8}(\d{2})(\d{2})/.exec(str)
  if (mCompact) return `${mCompact[1]}:${mCompact[2]}`
  return ''
}

// PAPER-PRODUCT.md intraday contract:
// Operation p12.securities.intraday -> { security_code, trade_date, source, as_of, status: 'available' | 'unavailable', points: [{ time, price }], reason? }
export function normaliseIntradayPayload (raw, fallbackCode) {
  if (!raw || typeof raw !== 'object') return null
  const container = raw.intraday && typeof raw.intraday === 'object' ? raw.intraday : raw
  const rawPoints = Array.isArray(container.points)
    ? container.points
    : (Array.isArray(container.bars) ? container.bars : [])
  const points = []
  for (const pt of rawPoints) {
    if (!pt || typeof pt !== 'object') continue
    const rawTime = pt.time || pt.minute || pt.ended_at || pt.timestamp || ''
    const hm = extractHm(rawTime)
    const price = pt.price ?? pt.close ?? null
    if (!hm || price === null || price === undefined) continue
    points.push({
      time: String(rawTime),
      hm,
      price: String(price)
    })
  }
  const asOf = container.as_of || (points.length ? points[points.length - 1].time : null)
  const lastHm = extractHm(asOf) || (points.length ? points[points.length - 1].hm : null)
  const firstHm = points.length ? points[0].hm : null
  const status = container.status || (points.length ? 'available' : 'unavailable')
  const reason = container.reason || container.reason_text || container.detail || container.reason_code || null
  return {
    security_code: String(container.security_code || raw.security_code || fallbackCode || ''),
    security_name: String(container.security_name || raw.security_name || ''),
    exchange: String(container.exchange || raw.exchange || ''),
    trade_date: container.trade_date || container.trading_date || (asOf ? String(asOf).slice(0, 10) : ''),
    source: container.source || raw.source || 'tencent',
    as_of: asOf,
    status,
    reason: reason ? String(reason) : null,
    previous_close: container.previous_close || raw.previous_close || null,
    first_minute: firstHm,
    last_complete_minute: lastHm,
    points
  }
}

export async function loadMarketSession () {
  try {
    const data = await callPaper('p12.market.session')
    return { available: true, data }
  } catch (error) {
    return { available: false, status: error.status, reasonCode: error.reasonCode, detail: error.detail }
  }
}

export async function loadSecurityQuote (code) {
  try {
    const raw = await callPaper('p12.securities.quote', { code })
    return { available: true, data: normaliseQuotePayload(raw, code), raw }
  } catch (error) {
    const codeName = error.reasonCode === 'ENDPOINT_NOT_ENABLED' ? 'QUOTE_SERVICE_UNAVAILABLE' : error.reasonCode
    return { available: false, status: error.status, reasonCode: codeName, detail: error.detail }
  }
}

export async function loadSecurityIntraday (code) {
  try {
    const raw = await callPaper('p12.securities.intraday', { code })
    return { available: true, data: normaliseIntradayPayload(raw, code) }
  } catch (error) {
    const codeName = error.reasonCode === 'ENDPOINT_NOT_ENABLED' ? 'INTRADAY_SERVICE_UNAVAILABLE' : error.reasonCode
    return { available: false, status: error.status, reasonCode: codeName, detail: error.detail }
  }
}

export async function loadSecurityIdentity (code) {
  try {
    const data = await callPaper('p12.securities.identity', { code })
    return { available: true, data }
  } catch (error) {
    return { available: false, status: error.status, reasonCode: error.reasonCode, detail: error.detail }
  }
}

export async function listSimOrders (accountId) {
  try {
    const data = await callPaper('p12.sim_orders.list', { account_id: accountId })
    const orders = Array.isArray(data) ? data : (Array.isArray(data?.orders) ? data.orders : [])
    return { available: true, orders }
  } catch (error) {
    return { available: false, orders: [], status: error.status, reasonCode: error.reasonCode }
  }
}

export async function submitSimOrder (accountId, order) {
  try {
    return await callPaper('p12.sim_orders.submit', { account_id: accountId }, order)
  } catch (error) {
    if (error.reasonCode === 'ENDPOINT_NOT_ENABLED' || error.status === 503) {
      throw new P12Error(error.status, 'SIM_ORDER_SERVICE_UNAVAILABLE', error.detail)
    }
    throw error
  }
}

export async function cancelSimOrder (accountId, orderId) {
  try {
    return await callPaper('p12.sim_orders.cancel', { account_id: accountId, order_id: orderId }, {})
  } catch (error) {
    if (error.reasonCode === 'ENDPOINT_NOT_ENABLED' || error.status === 503) {
      throw new P12Error(error.status, 'SIM_ORDER_SERVICE_UNAVAILABLE', error.detail)
    }
    throw error
  }
}

export async function saveFundingConfig (accountId, configBody) {
  try {
    return await callPaper('p12.funding.configure', { account_id: accountId }, configBody)
  } catch (error) {
    if (error.reasonCode === 'ENDPOINT_NOT_ENABLED' || error.status === 503) {
      throw new P12Error(error.status, 'FUNDING_WRITE_NOT_AVAILABLE', error.detail)
    }
    throw error
  }
}

export function newClientId (prefix) {
  const random = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10)
  return `${prefix}-${Date.now().toString(36)}-${random}`
}
