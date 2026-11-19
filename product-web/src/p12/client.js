// P12 desktop client: one call path per contract operation; responses are contract-checked.
import { loadSession } from '../api'
import { OPERATIONS, P12_SCHEMA_VERSION } from './contract.generated.js'
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

export function newClientId (prefix) {
  const random = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10)
  return `${prefix}-${Date.now().toString(36)}-${random}`
}
