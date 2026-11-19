import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { LABELS, OPERATIONS, P12_SCHEMA_DIGEST, P12_SCHEMA_VERSION, REASON_TEXTS } from '../src/p12/contract.generated.js'
import { formatMoney, formatSignedMoney, normaliseMoneyInput, ratioToPercent, formatMaybe } from '../src/p12/format.js'
import { validate } from '../src/p12/validate.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const schema = JSON.parse(read('../../schemas/p12/v1/openapi.json'))
const app = read('../src/App.vue')
const router = read('../src/router.js')
const client = read('../src/p12/client.js')
const server = read('../../ops/platform/web-server.mjs')
const views = ['P12Workbench.vue', 'P12Records.vue', 'P12FundingRules.vue'].map(name => read(`../src/p12/views/${name}`))
const nonChartSources = [...views, read('../src/p12/components/AccountSummary.vue'), read('../src/p12/format.js'), client, read('../src/p12/accountMixin.js')].join('\n')

test('generated client contract matches the single schema source', () => {
  assert.equal(P12_SCHEMA_VERSION, schema.components.schemas.SchemaVersion.const)
  assert.match(P12_SCHEMA_DIGEST, /^sha256:[0-9a-f]{64}$/)
  assert.deepEqual(Object.keys(REASON_TEXTS).sort(), schema.components.schemas.ReasonCode.enum)
  assert.deepEqual(LABELS, schema['x-quanthus-labels'])
  const paths = Object.values(OPERATIONS).map(item => item.path).sort()
  assert.deepEqual(paths, Object.keys(schema.paths).sort())
})

test('desktop routes and navigation separate my account from strategy research', () => {
  for (const route of ["'/p12'", "'/p12/records'", "'/p12/funding'"]) assert.ok(router.includes(route))
  for (const text of ['我的账户', '工作台', '记录', '资金与规则', '策略研究', '候选验证']) assert.ok(app.includes(text), text)
  assert.match(server, /pathname\.startsWith\("\/p12\/"\)/)
  assert.match(app, /@session-expired="logout"/)
})

test('client derives every call from contract operations', () => {
  assert.match(client, /OPERATIONS\[operationId\]/)
  assert.doesNotMatch(nonChartSources, /\/api\/p12\/v1/)
  assert.match(client, /validate\(spec\.response, payload\)/)
})

test('money is never routed through floats outside chart coordinates', () => {
  assert.doesNotMatch(nonChartSources, /parseFloat|toFixed|\bNumber\(/)
})

test('pages keep waiting, pending reconciliation and missing states visible', () => {
  const all = views.join('\n')
  for (const text of ['建议动作', '今天的变化', '主要约束', '核对实际执行', '失效条件', '完整分析链', '原报告', '更正链', '账本流水', '盈亏留桶模式']) {
    assert.ok(all.includes(text), text)
  }
  for (const code of ['WAITING', 'NEEDS_RECONCILIATION', 'MISSING_INPUT', 'NEEDS_REASSESSMENT']) assert.ok(LABELS.status_code[code])
  assert.equal(LABELS.funding_mode.fixed_bucket, '固定桶额')
  assert.ok(views[0].includes('data-testid="p12-unpublished"'))
  assert.ok(views[0].includes('v-else class="p12-grid"'))
  assert.ok(views[1].includes('v-if="records.package"'))
  assert.equal(LABELS.publication_status.NOT_YET_PUBLISHED, '盘后计划尚未发布')
})

test('decimal formatting is exact string manipulation', () => {
  assert.equal(formatMoney('10000.00'), '¥10,000.00')
  assert.equal(formatMoney('-5005.00'), '-¥5,005.00')
  assert.equal(formatSignedMoney('1250.00'), '+¥1,250.00')
  assert.equal(ratioToPercent('0.4942'), '49.42%')
  assert.equal(ratioToPercent('1.1800'), '118.00%')
  assert.equal(ratioToPercent('0.0003'), '0.03%')
  assert.equal(ratioToPercent('0.6'), '60.00%')
  assert.equal(normaliseMoneyInput('12.5'), '12.50')
  assert.equal(normaliseMoneyInput('0012'), '12.00')
  assert.equal(normaliseMoneyInput('12.505'), null)
  assert.equal(normaliseMoneyInput('1e3'), null)
  assert.deepEqual(formatMaybe({ value: null, reason_code: 'VALUATION_UNAVAILABLE' }), { text: '未知', unknown: true, reason: '缺少估值价格' })
})

test('runtime validator enforces closed objects and unknown semantics', () => {
  validate('MaybeMoney', { value: null, reason_code: 'FUNDING_CYCLE_NOT_BOUND' })
  assert.throws(() => validate('MaybeMoney', { value: '1.0', reason_code: null }))
  assert.throws(() => validate('MaybeMoney', { value: null, reason_code: 'UNKNOWN_CODE' }))
  assert.throws(() => validate('MaybeMoney', { value: '1.00', reason_code: null, extra: 1 }))
})
