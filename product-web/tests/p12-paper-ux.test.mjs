import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { extractHm, normaliseIntradayPayload } from '../src/p12/client.js'
import {
  calcQuoteChange,
  centsToMoney,
  checkPriceCage,
  compareMoney,
  describeQuoteState,
  humanizeBucketId,
  humanizeMissingField,
  inferExchangeLabel,
  isRehearsalAccount,
  isSyntheticItem,
  isTimestampPast,
  moneyToCents,
  multiplyPriceQty,
  normalizeSecurityName,
  reasonText,
  simOrderActiveRemaining,
  simOrderOriginLabel,
  simOrderQtySummary
} from '../src/p12/format.js'
import { PAPER_OPERATIONS } from '../src/p12/paperContract.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const router = read('../src/router.js')
const login = read('../src/views/LoginView.vue')
const workbench = read('../src/p12/views/P12Workbench.vue')
const records = read('../src/p12/views/P12Records.vue')
const funding = read('../src/p12/views/P12FundingRules.vue')
const chart = read('../src/p12/components/PriceChart.vue')

test('root and fallback routes land directly on my paper workbench (/p12)', () => {
  assert.match(router, /\{\s*path:\s*'\/',\s*redirect:\s*'\/p12'\s*\}/)
  assert.match(router, /\{\s*path:\s*'\*',\s*redirect:\s*'\/p12'\s*\}/)
  assert.ok(!router.includes("redirect: '/research'"))
  assert.ok(login.includes('进入我的模拟盘'))
})

test('exact BigInt money math computes order totals and quote deltas without floats', () => {
  assert.equal(moneyToCents('10.65'), 1065n)
  assert.equal(centsToMoney(106500n), '1065.00')
  assert.equal(multiplyPriceQty('10.65', 400), '4260.00')
  assert.equal(compareMoney('4260.00', '10000.00'), -1)
  assert.deepEqual(calcQuoteChange('10.65', '10.70'), {
    diffText: '-0.05',
    pctText: '-0.47%',
    direction: 'down'
  })
  assert.deepEqual(calcQuoteChange('10.85', '10.70'), {
    diffText: '+0.15',
    pctText: '+1.40%',
    direction: 'up'
  })
})

test('intraday contract normalises received points, source, trade_date and empty reason', () => {
  assert.equal(PAPER_OPERATIONS['p12.securities.intraday'].path, '/api/p12/v1/securities/{code}/intraday')
  assert.equal(extractHm('2026-11-20T14:00:00+08:00'), '14:00')
  const ok = normaliseIntradayPayload({
    security_code: '600000',
    trade_date: '2026-11-20',
    source: 'tencent',
    as_of: '2026-11-20T14:00:00+08:00',
    status: 'available',
    points: [
      { time: '2026-11-20T09:30:00+08:00', price: '10.68' },
      { time: '2026-11-20T14:00:00+08:00', price: '10.65' }
    ]
  })
  assert.equal(ok.status, 'available')
  assert.equal(ok.first_minute, '09:30')
  assert.equal(ok.last_complete_minute, '14:00')
  assert.equal(ok.points.length, 2)

  const empty = normaliseIntradayPayload({
    security_code: '600000',
    trade_date: '2026-11-20',
    source: 'tencent',
    as_of: null,
    status: 'unavailable',
    points: [],
    reason: '当前处于开盘前，尚无完整分钟点'
  })
  assert.equal(empty.status, 'unavailable')
  assert.equal(empty.points.length, 0)
  assert.equal(empty.reason, '当前处于开盘前，尚无完整分钟点')
  assert.ok(chart.includes('最近完整分钟'))
  assert.ok(chart.includes('11:30 / 13:00 午休'))
})

test('security identity separates synthetic rehearsal items from real A-share exchanges', () => {
  assert.equal(normalizeSecurityName('万  科Ａ'), '万 科Ａ')
  assert.equal(isSyntheticItem({ opportunity_id: 'OPP-SYN-600000', security_name: '合成证券甲' }, 'synthetic'), true)
  assert.equal(isSyntheticItem({ opportunity_id: 'HOLD-600000', security_name: '浦发银行', selfDirected: true }, false), false)
  assert.equal(isRehearsalAccount({ account_id: 'SYN-PAPER-A', synthetic: false }, { account_id: 'SYN-PAPER-A', mode: 'synthetic' }), false)
  assert.equal(isRehearsalAccount({ account_id: 'SYN-P12-A', synthetic: true }, { account_id: 'SYN-P12-A', mode: 'synthetic' }), true)
  assert.equal(inferExchangeLabel('600000', '上交所', true), '演练示例代号')
  assert.equal(inferExchangeLabel('600000', '', false), '上交所')
  assert.equal(inferExchangeLabel('000002', '', false), '深交所')
  assert.equal(inferExchangeLabel('300001', '', false), '深交所')
  assert.equal(inferExchangeLabel('920001', '上交所', false), '北交所')
  assert.equal(humanizeBucketId('bucket-01'), '1 号资金桶')
  assert.equal(humanizeMissingField('opportunity_policy_version'), '候选选股策略正式版本号')
  assert.equal(reasonText('NON_ROUND_LOT'), '委托股数须为 100 股（1 手）的整数倍')
})

test('R1: quote states, order wording, price cage and expired window checks stay accurate', () => {
  assert.equal(PAPER_OPERATIONS['p12.paper.account'].path, '/api/p12/v1/accounts/{account_id}/paper')
  assert.equal(
    describeQuoteState({ status: 'halt', market_state: 'pre_open' }, { market_state: 'pre_open' }, { suspended: false }),
    '开盘前未开市（昨收参考）'
  )
  assert.equal(
    describeQuoteState({ status: 'halt', market_state: 'pre_open' }, { market_state: 'pre_open' }, { suspended: true }),
    '停牌'
  )
  assert.equal(
    describeQuoteState({ status: 'stale', market_state: 'lunch_break' }, { market_state: 'lunch_break' }, null),
    '午间休市（上午最新价）'
  )
  assert.equal(simOrderOriginLabel({ status: 'rejected', source: 'sim_matched' }), '自主模拟委托 · 未受理')
  assert.equal(simOrderOriginLabel({ status: 'open', source: 'sim_matched' }), '自主模拟委托 · 待撮合')
  assert.equal(simOrderOriginLabel({ status: 'cancelled', source: 'sim_matched' }), '自主模拟委托 · 已撤单')
  assert.equal(simOrderOriginLabel({ status: 'expired', source: 'sim_matched' }), '自主模拟委托 · 日终失效')
  assert.equal(simOrderOriginLabel({ status: 'filled', source: 'sim_matched' }), '模拟撮合成交')
  assert.equal(simOrderActiveRemaining({ status: 'rejected', quantity: 100, filled_qty: 0, remaining_qty: 100 }), 0)
  assert.equal(simOrderActiveRemaining({ status: 'cancelled', quantity: 100, filled_qty: 0, remaining_qty: 100 }), 0)
  assert.equal(simOrderActiveRemaining({ status: 'open', quantity: 100, filled_qty: 0, remaining_qty: 100 }), 100)
  assert.equal(simOrderQtySummary({ status: 'rejected', quantity: 100, filled_qty: 0 }), '未成交（0 股）· 未进入挂单')
  const mainBoardOk = checkPriceCage('10.85', '10.00', '600000')
  assert.equal(mainBoardOk.outOfBounds, false)
  assert.equal(mainBoardOk.upText, '11.00')
  const mainBoardOver = checkPriceCage('15.00', '10.00', '600000')
  assert.equal(mainBoardOver.outOfBounds, true)
  assert.equal(mainBoardOver.bandPct, '10%')
  const gemBand = checkPriceCage('11.80', '10.00', '300001')
  assert.equal(gemBand.outOfBounds, false)
  assert.equal(gemBand.bandPct, '20%')
  assert.equal(isTimestampPast('2026-11-20T15:17:00+08:00', '2026-11-19T15:00:00+08:00'), true)
  assert.equal(isTimestampPast('2026-11-19T10:05:00+08:00', '2026-11-19T15:00:00+08:00'), false)
})

test('unheld exit plan guard, terminal-before-fee copy, prior_packages and honest funding preview exist', () => {
  assert.ok(workbench.includes('isUnheldExit'))
  assert.ok(workbench.includes('isOpportunityExpired'))
  assert.match(workbench, /watch:\s*\{\s*selectedId\s*\(newId,\s*oldId\)/)
  assert.ok(!workbench.includes('挂单后可离开页面'))
  assert.ok(workbench.includes('allSimIds'))
  assert.ok(workbench.includes('委托已终结，还需确认整单费用'))
  assert.ok(records.includes('prior_packages'))
  assert.ok(records.includes('data-testid="p12-filter-keyword"'))
  assert.ok(funding.includes('data-testid="p12-funding-preview"'))
  assert.ok(funding.includes('data-testid="p12-funding-cancel"'))
  assert.ok(funding.includes("'p12-funding-save-blocked'") && funding.includes(':data-testid="saveStatus.testid"'))
  assert.ok(funding.includes('pendingSimulationCount'))
  assert.ok(funding.includes('FUNDING_REBIND_NOT_OPEN'))
  assert.ok(funding.includes('沿原规则结算'))
})
