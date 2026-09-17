import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api.js', import.meta.url), 'utf8')
const candidateDetail = readFileSync(new URL('../src/views/CandidateDetail.vue', import.meta.url), 'utf8')
const provenance = readFileSync(new URL('../UPSTREAM.md', import.meta.url), 'utf8')
const catalog = JSON.parse(readFileSync(new URL('../../config/product-research-catalog.v1.json', import.meta.url), 'utf8'))
const sources = [
  'src/App.vue',
  'src/views/LoginView.vue',
  'src/views/ResearchOverview.vue',
  'src/views/StrategyLibrary.vue',
  'src/views/CandidateDetail.vue',
  'src/views/AttributionCenter.vue',
  'src/components/ReplayJobs.vue'
].map(path => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')).join('\n')

test('primary navigation contains only retained product workflows', () => {
  for (const route of ['/research', '/candidates', '/attribution']) {
    assert.match(app, new RegExp(route))
  }
  assert.doesNotMatch(app, /\/strategies|\/backtests/)
  assert.match(app, /href="\/operator"/)
  for (const removed of ['billing', 'broker', 'copilot', 'fast-analysis', 'mcp', 'agent-token']) {
    assert.doesNotMatch(app.toLowerCase(), new RegExp(removed))
  }
})

test('product client calls admitted catalog, detail, review, and governance APIs', () => {
  assert.match(api, /\/api\/platform\/v1\/product\/catalog/)
  assert.match(api, /\/api\/platform\/v1\/product\/candidates/)
  assert.match(api, /\/reviews/)
  assert.match(api, /\/governance/)
  assert.match(api, /\/api\/platform\/v1\/auth\/login/)
  assert.match(api, /\/data\/attribution-releases\/catalog\.json/)
  assert.match(api, /formal_strategy_evidence === true/)
  assert.match(api, /synthetic_fixture === false/)
  assert.doesNotMatch(api, /billing|broker|copilot|mcp|quick-trade/)
})

test('research-only and attribution honesty remain visible', () => {
  assert.match(sources, /研究用途，禁止实盘/)
  assert.match(sources, /合成结果不冒充结论/)
  assert.match(sources, /实盘授权/)
  assert.match(sources, /Powered by QuantDinger/)
  assert.match(sources, /回放成功/)
  assert.match(sources, /Job 本地投影/)
  assert.match(sources, /strategies.length/)
  assert.match(sources, /治理准入/)
  assert.match(sources, /执行授权/)
  assert.match(sources, /DWD 数据覆盖与候选回放分区是两套口径/)
  assert.match(sources, /近期研究分区/)
  assert.match(sources, /成交明细/)
  assert.match(sources, /Run 历史/)
  assert.match(sources, /当前有效/)
  assert.match(sources, /要求补证/)
  assert.match(sources, /提交治理/)
  assert.match(sources, /governance-under-review/)
  assert.match(sources, /available_actions/)
  assert.match(sources, /治理准入也不自动授权/)
  assert.match(sources, /重新回放是独立作业/)
})

test('catalog exposes candidates without implicit admission or execution authority', () => {
  assert.equal(catalog.strategies.length, 6)
  assert.ok(catalog.strategies.every(item => item.kind === 'candidate-version'))
  assert.equal(catalog.strategies.filter(item => item.governance_decision === 'research-admitted').length, 0)
  assert.equal(catalog.strategies.filter(item => item.execution_authorized === true).length, 0)
  assert.ok(catalog.strategies.every(item => item.pipeline_state === 'replayed'))
  assert.ok(catalog.strategies.every(item => item.replay_state === 'replay-succeeded'))
  assert.ok(catalog.strategies.every(item => item.governance_decision === 'not-submitted'))
  assert.equal(catalog.data_scope.dwd_base.through_date, '2026-07-31')
  assert.equal(catalog.data_scope.dwd_base.package_id, 'dwd-base-202607-v2.1')
  assert.equal(catalog.data_scope.dwd_base.correction_id, 'dwd-base-202607-v2.1-correction-20260813-001')
  assert.equal(catalog.latest_candidate_batch.candidate_count, 5)
  assert.equal(catalog.latest_candidate_batch.succeeded_count, 5)
  assert.equal(catalog.latest_candidate_batch.failed_count, 0)
  assert.equal(catalog.latest_candidate_batch.dedicated_ads_required, false)
  assert.equal(catalog.latest_candidate_batch.governance_transition_applied, false)
  assert.equal(catalog.data_scope.replay_partitions.recent_research.end, '2026-07-30')
  assert.ok(catalog.strategies.every(item => item.evidence_scope.partition === 'development'))
  const quantdinger = catalog.strategies.find(item => item.source_system === 'quantdinger')
  assert.equal(quantdinger.pipeline_state, 'replayed')
  assert.equal(quantdinger.governance_decision, 'not-submitted')
  assert.equal(quantdinger.evidence_scope.decision_end, '2021-12-30')
  assert.equal(quantdinger.operations, 36)
  assert.equal(quantdinger.closed_trades, 18)
  assert.equal(quantdinger.total_return, 59.628843)
  assert.equal(quantdinger.external_conformance.signal_match, '37 shared + 1 governed downstream divergence')
  assert.equal(quantdinger.external_conformance.fill_date_side_match, '36 shared / 38 native')
  assert.equal(quantdinger.external_conformance.legacy_direct_dwd_v2_1, false)
  const expert = catalog.strategies.find(item => item.strategy_id === 'candidate.p4-trend-risk')
  assert.equal(expert.version, '1.0.1')
  assert.equal(expert.replay_submission_eligible, false)
  assert.equal(expert.execution_authorized, false)
})

test('candidate forward validation is read-only and activation-safe', () => {
  for (const field of [
    'forward_validation',
    'waiting_activation',
    'official_collection_sessions_completed',
    'unit_notional_cny',
    'source_digests'
  ]) {
    assert.match(candidateDetail, new RegExp(field))
  }
  assert.match(candidateDetail, /Forward Validation/)
  assert.match(candidateDetail, /未激活/)
  assert.match(candidateDetail, /官方 Prospective Evidence 尚未激活/)
  assert.match(candidateDetail, /Broker \/ Live/)
  assert.match(candidateDetail, /未授权 \/ 未授权/)
  assert.doesNotMatch(api, /forward[^'"\n]*(activate|broker|notify|execute|promote|update)/i)
  assert.doesNotMatch(candidateDetail, /triggerAction\(['"]forward/)
})

test('candidate expert evidence remains digest-bound and read-only', () => {
  for (const field of [
    'expert_evidence',
    'strategy_binding',
    'assessment_scorecards',
    'economic_effects',
    'disagreement_cases',
    'lifecycle_decision',
    'source_digests'
  ]) {
    assert.match(candidateDetail, new RegExp(field))
  }
  assert.match(candidateDetail, /评估 Run/)
  assert.match(candidateDetail, /归因发布/)
  assert.match(candidateDetail, /待生命周期决定/)
  assert.match(candidateDetail, /已形成决定/)
  assert.match(candidateDetail, /证据不足/)
  assert.match(candidateDetail, /Shadow.*停用/)
  assert.match(candidateDetail, /当前批准仅冻结该策略版本及证据链/)
  assert.match(candidateDetail, /执行授权<\/span><b class="negative">未授权/)
  assert.doesNotMatch(api, /expert[^'"\n]*(create|update|approve|decide|promote|execute)/i)
  assert.doesNotMatch(candidateDetail, /triggerAction\(['"]expert/)
})

test('pinned upstream provenance is recorded', () => {
  assert.match(provenance, /f2b5968bb815ed7c0c2301d61523dca98dcdddde/)
  assert.match(provenance, /Commercial deployment is blocked/)
})
