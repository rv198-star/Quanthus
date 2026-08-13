const SESSION_KEY = 'quanthus.platform.session.v1'

export function loadSession () {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null')
    if (!value || !value.token || Date.parse(value.expiresAt) <= Date.now()) return null
    return value
  } catch (_) {
    return null
  }
}

export function clearSession () {
  sessionStorage.removeItem(SESSION_KEY)
}

async function request (path, options = {}) {
  const session = loadSession()
  const headers = { Accept: 'application/json', ...(options.headers || {}) }
  if (session) headers.Authorization = `Bearer ${session.token}`
  if (options.body) headers['Content-Type'] = 'application/json'
  const response = await fetch(path, { ...options, headers, cache: 'no-store' })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const error = new Error(payload?.error?.reason || 'request_failed')
    error.status = response.status
    throw error
  }
  return payload
}

export async function login (username, password, mfaCode) {
  const result = await request('/api/platform/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, ...(mfaCode ? { mfa_code: mfaCode } : {}) })
  })
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: result.token, expiresAt: result.expires_at }))
  return result
}

export const loadMe = () => request('/api/platform/v1/auth/me')
export const loadCatalog = () => request('/api/platform/v1/product/catalog')
export const loadCandidate = strategyId => request(`/api/platform/v1/product/candidates/${encodeURIComponent(strategyId)}`)
export const loadCandidateReplays = () => request('/api/platform/v1/product/candidate-replays')
export const loadJob = jobId => request(`/api/platform/v1/jobs/${encodeURIComponent(jobId)}`)
export const submitCandidateReplay = (reason, idempotencyKey) => request('/api/platform/v1/product/candidate-replays', {
  method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ reason })
})
export const cancelJob = jobId => request(`/api/platform/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
export const recordCandidateReview = (strategyId, action, note = '') => request(`/api/platform/v1/product/candidates/${encodeURIComponent(strategyId)}/reviews`, {
  method: 'POST',
  body: JSON.stringify({ action, note })
})
export const recordCandidateGovernance = (strategyId, action, note = '') => request(`/api/platform/v1/product/candidates/${encodeURIComponent(strategyId)}/governance`, {
  method: 'POST',
  body: JSON.stringify({ action, note })
})

export async function loadAttributionCatalog () {
  const catalogUrl = '/data/attribution-releases/catalog.json'
  const response = await fetch(catalogUrl, { cache: 'no-store' })
  if (response.status === 404) return { releases: [] }
  if (!response.ok) throw new Error('publication_catalog_unavailable')
  const catalog = await response.json()
  const releases = await Promise.all((catalog.releases || []).map(async release => {
    const manifestUrl = new URL(release.release_path, new URL(catalogUrl, window.location.origin))
    const manifestResponse = await fetch(manifestUrl, { cache: 'no-store' })
    if (!manifestResponse.ok) return null
    const manifest = await manifestResponse.json()
    return manifest.formal_strategy_evidence === true && manifest.synthetic_fixture === false
      ? { ...release, manifest }
      : null
  }))
  return { ...catalog, releases: releases.filter(Boolean) }
}
