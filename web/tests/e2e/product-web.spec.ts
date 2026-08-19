import { expect, test } from '@playwright/test'

const username = process.env.QUANTHUS_WEB_E2E_USERNAME
const password = process.env.QUANTHUS_WEB_E2E_PASSWORD

test.skip(!username || !password, 'product Web credentials are required')

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('username').fill(username || '')
  await page.getByTestId('password').fill(password || '')
  await page.getByTestId('login-submit').click()
  await expect(page.getByTestId('research-page')).toBeVisible()
})

async function openProductRoute(page, label: string) {
  const menu = page.getByRole('button', { name: '打开导航' })
  if (await menu.isVisible() && !(await page.locator('.sidebar').evaluate(element => element.classList.contains('open')))) await menu.click()
  await page.getByRole('link', { name: label }).click()
}

async function apiJson(page, path: string) {
  return page.evaluate(async apiPath => {
    const session = JSON.parse(sessionStorage.getItem('quanthus.platform.session.v1') || 'null')
    const response = await fetch(apiPath, {
      headers: { Authorization: `Bearer ${session.token}` },
      cache: 'no-store'
    })
    if (!response.ok) throw new Error(`API ${apiPath} returned ${response.status}`)
    return response.json()
  }, path)
}

const signed = value => `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`
const percent = value => `${Number(value).toFixed(2)}%`

test('retained product workflows expose candidates without overstating integration', async ({ page }, testInfo) => {
  const catalog = await apiJson(page, '/api/platform/v1/product/catalog')
  const governed = catalog.strategies.find(item => item.strategy_id === 'candidate.market-breadth-trend-breakout')
  const quantdingerSummary = catalog.strategies.find(item => item.strategy_id === 'quantdinger.strategy-v2-bullish-three-lines')
  expect(governed).toBeTruthy()
  expect(quantdingerSummary).toBeTruthy()

  await expect(page.getByText('A 股研究总览')).toBeVisible()
  await expect(page.getByText(signed(governed.total_return)).first()).toBeVisible()
  await expect(page.getByText('Powered by QuantDinger')).toBeVisible()
  await expect(page.getByText('DWD 基线截至 2026-07-31')).toBeVisible()
  await expect(page.getByText('DWD 数据覆盖与候选回放分区是两套口径')).toBeVisible()
  await expect(page.getByText('2026-01 至 2026-07 · 尚未回放')).toBeVisible()

  await openProductRoute(page, '候选验证')
  await expect(page.getByTestId('candidates-page')).toBeVisible()
  await expect(page.getByText('5 回放成功')).toBeVisible()
  const replayJobs = page.getByTestId('replay-jobs')
  await expect(replayJobs).toBeVisible()
  await expect(replayJobs).toContainText(/排队中|运行中|已完成|失败|已取消/)
  await expect(page.getByText('5 个候选版本')).toBeVisible()
  await expect(page.getByText('0 治理准入')).toBeVisible()
  await expect(page.getByText('0 执行授权')).toBeVisible()
  await expect(page.getByText('5 待复核')).toBeVisible()
  await expect(page.getByTestId('strategy-candidate.market-breadth-trend-breakout')).toContainText(String(governed.closed_trades))
  const quantdinger = page.getByTestId('strategy-quantdinger.strategy-v2-bullish-three-lines')
  await expect(quantdinger).toContainText(signed(quantdingerSummary.total_return))
  await expect(quantdinger).toContainText(String(quantdingerSummary.closed_trades))
  await expect(quantdinger).toContainText('回放成功')
  await expect(quantdinger).toContainText('待复核')
  await expect(quantdinger).toContainText('未提交治理')
  await expect(quantdinger).toContainText('未授权')
  await expect(quantdinger).toContainText('存在已解释执行差异')
  await quantdinger.click()
  await expect(page.getByTestId('candidate-detail-page')).toBeVisible()
  const quantdingerDetail = await apiJson(page, '/api/platform/v1/product/candidates/quantdinger.strategy-v2-bullish-three-lines')
  await expect(page.getByText('成交明细')).toBeVisible()
  await expect(page.getByTestId('trade-ledger')).toContainText('600519')
  await expect(page.getByText(percent(quantdingerDetail.evidence.performance.win_rate))).toBeVisible()
  await expect(page.getByText('security_not_trading_observed', { exact: false })).toBeVisible()
  await expect(page.getByTestId('approval-state-rail')).toContainText('回放成功')
  await expect(page.getByTestId('approval-state-rail')).toContainText('待复核')
  await expect(page.getByTestId('approval-state-rail')).toContainText('未提交治理')
  await expect(page.getByTestId('approval-state-rail')).toContainText('未授权')
  await expect(page.getByTestId('review-actions')).toContainText('认领复核')
  await expect(page.getByTestId('governance-actions')).toContainText('复核通过后才可生成并提交治理包')
  const detailReplayJobs = page.getByTestId('replay-jobs')
  await expect(detailReplayJobs).toBeVisible()
  await expect(detailReplayJobs).toContainText(/排队中|运行中|已完成|失败|已取消/)
  await expect(page.getByTestId('run-history')).toBeVisible()
  if (quantdingerDetail.run_history.length) {
    const activeRun = quantdingerDetail.run_history.find(run => run.active)
    expect(activeRun).toBeTruthy()
    await expect(page.getByTestId('run-history')).toContainText('当前有效')
    await expect(page.getByTestId('run-history')).toContainText(activeRun.run_id)
  } else {
    await expect(page.getByTestId('run-history')).toContainText('尚无 PostgreSQL 已封存 Run')
  }
  const chartPixels = await page.locator('.detail-chart canvas').evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext('2d')
    if (!context || canvas.width === 0 || canvas.height === 0) return 0
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data
    let nonTransparent = 0
    for (let offset = 3; offset < data.length; offset += 4) {
      if (data[offset] > 0) nonTransparent++
    }
    return nonTransparent
  })
  expect(chartPixels).toBeGreaterThan(1000)
  await page.reload()
  await expect(page.getByTestId('candidate-detail-page')).toBeVisible()
  await expect(page.getByTestId('trade-ledger')).toContainText('600519')
  await page.screenshot({
    path: `../artifacts/platform-kernel/worker-lean-v1/candidate-detail-${testInfo.project.name}.png`,
    fullPage: true
  })

  await openProductRoute(page, '归因分析')
  await expect(page.getByTestId('attribution-page')).toBeVisible()
  const releases = page.locator('.release-row')
  if (await releases.count()) await expect(releases.first()).toContainText('已发布')
  else await expect(page.getByText('合成结果不冒充结论')).toBeVisible()
})

test('removed capabilities are absent and layout does not overflow', async ({ page }) => {
  const body = (await page.locator('body').innerText()).toLowerCase()
  for (const removed of ['ai copilot', 'fast analysis', 'billing', 'broker', 'mcp']) {
    expect(body).not.toContain(removed)
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await openProductRoute(page, '候选验证')
  const strategyOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(strategyOverflow).toBeLessThanOrEqual(1)
})
