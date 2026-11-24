import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const workbench = readFileSync(new URL('../src/p12/views/P12Workbench.vue', import.meta.url), 'utf8')
const funding = readFileSync(new URL('../src/p12/views/P12FundingRules.vue', import.meta.url), 'utf8')
const fmt = readFileSync(new URL('../src/p12/format.js', import.meta.url), 'utf8')
const has = (src, s) => assert.ok(src.includes(s), 'missing: ' + s)

test('sell opportunities read consistent direction, quantity and timing', () => {
  has(workbench, "action_code === 'SELL'")
  assert.match(workbench, /卖出 [\s\S]*formatQuantity/)
  for (const t of ['当前持有', '卖出数量', '已成交', '剩余待成交', '执行窗口']) has(workbench, t)
  has(workbench, 'data-testid="p12-sell-plan"')
  has(workbench, 'data-testid="p12-open-sell-form"')
  has(workbench, "side: 'sell'")
  assert.match(workbench, /已卖出[\s\S]*剩余[\s\S]*待成交并在同委托终结/)
})

test('closed exit shows history not a live ask', () => {
  has(workbench, "status_code === 'ENDED'")
  has(workbench, '历史原因：')
  assert.match(workbench, /该机会已卖出[\s\S]*结束/)
  has(workbench, 'data-testid="p12-ended-note"')
})

test('sell timing wording follows the package-bound next_session (E8 r4-3)', () => {
  // The page's executable date is the package's authoritative next_session -
  // never a computed market_date + 1 that can land on a weekend/holiday.
  has(workbench, 'sellIsTomorrow')
  has(workbench, 'boundSessionDate')
  has(workbench, "workbench?.package?.next_session")
  has(workbench, 'boundSessionLabel')
  assert.match(workbench, /09:30[\s\S]*交易时段内确认卖出/)
  has(workbench, '09:30–15:00')
  has(workbench, 'today.slice(5)')
  has(workbench, '绑定交易日')
  assert.ok(!/该机会已于/.test(workbench), 'ended sentence must not use market_date')
  has(workbench, 'v-if="!(sellTask && sellTask.settled)"')
  has(workbench, 'taskTitle')
  has(fmt, 'nextDate')
})

test('queue list shows the final sized sell quantity, not the holding (E8 r4-2)', () => {
  // A 400-held / 200-to-sell reduce reads "卖出 200 股" on the queue row -
  // the same figure the detail card, confirm card and receipt display.
  has(workbench, 'finalQty')
  assert.match(workbench, /plan\.delta_qty < 0[\s\S]*Math\.abs\(item\.plan\.delta_qty\)/)
  has(workbench, 'isResidueLotZero')
  has(workbench, '余仓不足一手，请自行决定是否卖出')
  has(workbench, 'data-testid="p12-residue-note"')
  // After-hours sell confirm shows the phase-1 entry wording.
  has(workbench, '开盘后可确认卖出')
  has(workbench, 'data-testid="p12-sell-window-hint"')
})

test('off-price funding names the recorded fill and the bucket shortfall', () => {
  has(funding, 'INSUFFICIENT_CASH')
  has(funding, '欠资已入账')
  has(funding, '该笔成交已记入账户账本')
  has(funding, 'data-testid="p12-underfunded-flag"')
  has(funding, 'bucket.target_notional')
  has(funding, '桶预算的欠资')
  assert.ok(!/缺口是 5,000/.test(funding), 'hint must not hard-code 5,000')
  has(funding, '未接入实际成交')
  has(funding, 'p12-status-cell')
})
test('a fully filled sell waits for fee and terminal before it ends', () => {
  // The task only settles once every sell order is terminal and
  // settlement-ready; before that it stays a todo with a prefilled entry.
  has(workbench, 'awaitingConfirmation')
  has(workbench, 'o.settlement_ready')
  has(workbench, "o.status === 'terminal'")
  assert.match(workbench, /卖出已全部成交[\s\S]*还需确认整单费用并终结委托/)
  // The prefilled confirm entries keep order id, side and security filled.
  has(workbench, 'prefillConfirm')
  has(workbench, 'data-testid="p12-open-fee-form"')
  has(workbench, 'data-testid="p12-open-term-form"')
  has(workbench, "kind: isFee ? 'fee' : 'observation'")
  has(workbench, '确认卖出收尾')
  // The ended sentence still exists for the truly settled state.
  assert.match(workbench, /该机会已卖出[\s\S]*结束，无需操作/)
})


test('a fee-confirmed sell only asks for the terminal close', () => {
  // feeConfirmed derives from the orders' fee_confirmed_total; once set the
  // copy names the confirmed fee and only the terminal button remains.
  has(workbench, 'fee_confirmed_total')
  has(workbench, 'feeConfirmed')
  assert.match(workbench, /整单费用[\s\S]*formatMoney\(t\.feeTotal\)[\s\S]*已确认，还需终结委托/)
  has(workbench, 'v-if="!sellTask.feeConfirmed"')
  // Remaining zero no longer says "剩余 0 股 待成交并在同委托终结".
  assert.match(workbench, /t\.filled > 0 && !t\.remaining/)
  assert.match(workbench, /待确认费用并终结委托/)
  assert.match(workbench, /已卖出[\s\S]*待终结委托/)
})
