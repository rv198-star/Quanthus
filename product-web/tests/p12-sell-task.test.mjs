import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const workbench = readFileSync(new URL('../src/p12/views/P12Workbench.vue', import.meta.url), 'utf8')
const funding = readFileSync(new URL('../src/p12/views/P12FundingRules.vue', import.meta.url), 'utf8')

test('sell opportunities read consistent direction, quantity and timing', () => {
  // A SELL action is named, not left to the generic "no operation" fallback.
  assert.match(workbench, /action_code === 'SELL'/)
  assert.match(workbench, /卖出 \$\{formatQuantity/)
  // The sell task card shows held / filled / remaining / window.
  for (const t of ['当前持有', '卖出数量', '已成交', '剩余待成交', '执行窗口']) assert.ok(workbench.includes(t), t)
  assert.ok(workbench.includes('data-testid="p12-sell-plan"'))
  // In-window the task offers a prefilled sell registration entry.
  assert.ok(workbench.includes('data-testid="p12-open-sell-form"'))
  assert.match(workbench, /side: 'sell'/)
  // After the first fill the sentence names filled/remaining and the open terminal.
  assert.match(workbench, /已卖出.*剩余.*待成交并在同委托终结/)
})

test('closed exit shows history not a live ask', () => {
  // Ended exit keeps the trigger reason as history, filtered out of "同时".
  assert.match(workbench, /status_code === 'ENDED'/)
  assert.match(workbench, /历史原因：/)
  assert.match(workbench, /该机会已于 .*卖出.*结束/)
  // The noop pane no longer says "无需操作" for an ended exit.
  assert.ok(workbench.includes('data-testid="p12-ended-note"'))
})

test('off-price funding names the recorded fill and the bucket shortfall', () => {
  assert.match(funding, /INSUFFICIENT_CASH/)
  assert.match(funding, /成交已入账·桶内欠资/)
  assert.match(funding, /该笔成交已记入账户账本/)
  assert.ok(funding.includes('data-testid="p12-underfunded-flag"'))
  // Non-overspent unknown cash keeps the original wording.
  assert.match(funding, /未接入实际成交/)
})
