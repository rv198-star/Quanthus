<template>
  <div class="page-wrap p12-page" data-testid="p12-workbench">
    <div class="page-heading p12-heading">
      <div>
        <span class="eyebrow">MY ACCOUNT · WORKBENCH</span>
        <h1>工作台</h1>
        <p v-if="workbench && workbench.package">盘后计划 {{ workbench.package.market_date }} · 信息截至 {{ time(workbench.package.knowledge_cutoff) }} · 发布于 {{ time(workbench.package.published_at) }}</p>
        <p v-else-if="workbench">{{ workbench.publication.market_date }} 的盘后计划将于 {{ time(workbench.publication.published_at) }} 发布</p>
      </div>
      <div class="heading-actions">
        <select v-if="accounts.length > 1" class="p12-select" :value="accountId" aria-label="选择账户" @change="selectAccount($event.target.value)">
          <option v-for="item in accounts" :key="item.account_id" :value="item.account_id">{{ item.display_name }}</option>
        </select>
        <span v-if="workbench" class="p12-badge synthetic" data-testid="p12-mode">{{ label('mode', workbench.scope.mode) }} {{ workbench.scope.account_id }}</span>
        <span v-if="workbench" class="as-of" data-testid="p12-clock"><a-icon type="clock-circle" />{{ label('clock_kind', workbench.clock_kind) }} {{ time(workbench.clock) }} · 账户版本 {{ workbench.account.account_revision }}</span>
        <a-button icon="reload" :loading="loading" @click="refresh">刷新</a-button>
      </div>
    </div>

    <a-alert v-if="loadError" class="p12-alert" type="error" show-icon :message="loadError.text" :description="loadError.detail || loadError.reasonCode" data-testid="p12-error" />

    <template v-if="workbench">
      <AccountSummary :account="workbench.account" />
      <a-alert v-if="workbench.action_blockers.length" class="p12-alert" type="warning" show-icon message="部分规则尚未采用，新增买入暂停；已发生的成交仍可登记" :description="`缺少：${workbench.action_blockers.join('、')}`" />

      <section v-if="!workbench.package" class="panel p12-unpublished" data-testid="p12-unpublished">
        <h2>{{ label('publication_status', workbench.publication.status) }}</h2>
        <p>{{ workbench.publication.market_date }} 收盘后的计划将于 {{ time(workbench.publication.published_at) }} 发布。发布前不显示建议和数量；账户与已登记的成交照常可查。</p>
      </section>
      <div v-else class="p12-grid">
        <aside class="panel p12-queue" aria-label="待办和机会队列" data-testid="p12-queue">
          <div class="panel-head"><div><h2>待办与机会</h2><p>先核对，再处理退出、新建仓和观察</p></div></div>
          <div v-for="group in groups" :key="group.key" class="p12-queue-group">
            <h3>{{ label('queue_group', group.key) }}</h3>
            <button v-for="item in group.items" :key="item.opportunity_id" :class="['p12-queue-item', { active: item.opportunity_id === selectedId }]" :data-testid="`p12-queue-${item.opportunity_id}`" @click="selectedId = item.opportunity_id">
              <span class="name"><b>{{ item.security_name }}</b><small>{{ item.security_code }}</small></span>
              <span class="action">{{ label('action_code', item.action_code) }}</span>
              <span :class="['status-tag', statusTone(item.status_code)]">{{ label('status_code', item.status_code) }}</span>
            </button>
          </div>
        </aside>

        <section v-if="selected" class="panel p12-detail" data-testid="p12-detail">
          <div class="panel-head">
            <div><h2>{{ selected.security_name }} <small>{{ selected.security_code }}</small></h2><p>{{ label('stage', selected.stage) }} · 机会 {{ selected.opportunity_id }}</p></div>
            <span :class="['status-tag', statusTone(selected.status_code)]">{{ label('status_code', selected.status_code) }}</span>
          </div>
          <PriceChart :chart="selected.chart" :name="selected.security_name" :price-cap="selected.plan ? selected.plan.price_cap : null" :cost-price="selected.cost_price.value" :empty-text="selected.status_code === 'MISSING_INPUT' ? '行情数据未更新，暂不显示价格图' : '暂无合格价格数据'" />
          <div class="p12-triple">
            <div class="p12-card" data-testid="p12-action">
              <span>建议动作</span>
              <b>{{ label('action_code', selected.action_code) }}</b>
              <p>{{ actionSentence }}</p>
            </div>
            <div class="p12-card" data-testid="p12-changes">
              <span>今天的变化</span>
              <ul v-if="selected.changes.length"><li v-for="change in selected.changes" :key="change">{{ label('change', change) }}</li></ul>
              <p v-else>与上一交易日相比没有变化</p>
            </div>
            <div class="p12-card" data-testid="p12-constraints">
              <span>主要约束</span>
              <b>{{ selected.primary_constraint ? reason(selected.primary_constraint) : '无' }}</b>
              <p v-if="otherReasons.length">同时：{{ otherReasons.map(reason).join('；') }}</p>
              <p v-if="endedReasons.length" class="p12-history">历史原因：{{ endedReasons.map(reason).join('；') }}</p>
            </div>
          </div>
          <div class="p12-chain">
            <button class="p12-link" @click="chainOpen = !chainOpen">{{ chainOpen ? '收起完整分析链' : '展开完整分析链' }}</button>
            <label class="p12-pin"><input v-model="chainPinned" type="checkbox" /> 固定展开</label>
            <div v-if="chainOpen || chainPinned" class="p12-chain-body" data-testid="p12-chain">
              <div v-for="item in selected.explanations" :key="item.code" class="p12-chain-row">
                <span :class="['p12-use', item.use]">{{ label('explanation_use', item.use) }}</span>
                <p>{{ item.text }}</p>
                <code>{{ item.component_ref }}</code>
              </div>
              <dl>
                <div><dt>决策包</dt><dd><code>{{ workbench.package.package_id }}</code></dd></div>
                <div><dt>组合版本</dt><dd><code>{{ workbench.package.composition_ref.ref_id }}@{{ workbench.package.composition_ref.version }}</code></dd></div>
                <div v-if="selected.plan"><dt>风险检查</dt><dd><code>{{ selected.plan.risk_check_ref.slice(0, 19) }}…</code></dd></div>
                <div><dt>决策来源</dt><dd><code>{{ workbench.package.source_ref.ref_id }}@{{ workbench.package.source_ref.version }}</code></dd></div>
              </dl>
            </div>
          </div>
        </section>

        <aside v-if="selected" class="panel p12-task" data-testid="p12-task">
          <div class="panel-head"><div><h2>{{ selected.plan ? '下一交易日计划' : '今天的任务' }}</h2><p v-if="selected.applicability">{{ label('applicability_status', selected.applicability.status) }}</p></div></div>
          <template v-if="selected.plan && selected.plan.side === 'buy'">
            <dl class="p12-plan" data-testid="p12-plan">
              <div><dt>当前持有</dt><dd>{{ qty(selected.applicability.current_qty) }}</dd></div>
              <div><dt>计划目标</dt><dd>{{ qty(selected.plan.target_qty) }}</dd></div>
              <div><dt>还差</dt><dd data-testid="p12-delta">{{ qty(selected.applicability.delta_qty) }}</dd></div>
              <div><dt>有效窗口</dt><dd>{{ time(selected.plan.eligible_at) }} 至 {{ time(selected.plan.expires_at).slice(11) }}</dd></div>
              <div><dt>价格上限</dt><dd>{{ selected.plan.price_cap }}</dd></div>
              <div><dt>资金上限</dt><dd>{{ money(selected.plan.cash_bound) }}（含预估费用 {{ money(selected.plan.estimated_fee) }}）</dd></div>
            </dl>
            <div class="p12-invalid"><span>失效条件</span><ul><li v-for="item in selected.plan.invalidations" :key="item">{{ item }}</li></ul></div>
            <p class="p12-next">{{ label('next_step', selected.applicability.next_step) }}</p>

            <form v-if="selected.plan.delta_qty > 0 && !outcome" class="p12-form" data-testid="p12-fill-form" @submit.prevent="submit">
              <h3>核对实际执行</h3>
              <p class="p12-hint">登记你在券商实际成交的结果；价格或数量与计划不同也照实填写。</p>
              <label>成交数量（股）<input v-model.trim="form.quantity" inputmode="numeric" data-testid="p12-fill-quantity" /></label>
              <label>成交价<input v-model.trim="form.price" inputmode="decimal" data-testid="p12-fill-price" /></label>
              <label>确认费用<input v-model.trim="form.fee" inputmode="decimal" data-testid="p12-fill-fee" /></label>
              <label>成交时间<input v-model="form.time" type="datetime-local" data-testid="p12-fill-time" /></label>
              <p v-if="formError" class="p12-form-error" data-testid="p12-form-error">{{ formError }}</p>
              <a-button type="primary" html-type="submit" :loading="submitting" data-testid="p12-fill-submit">保存成交</a-button>
              <small class="p12-hint">报告编号 {{ form.reportId }}；网络重试会沿用同一编号。</small>
            </form>
          </template>
          <template v-else-if="selected.stage === 'exit_plan'">
            <dl class="p12-plan" data-testid="p12-sell-plan">
              <div><dt>当前持有</dt><dd>{{ qty(sellTask ? sellTask.held : 0) }}</dd></div>
              <div><dt>卖出数量</dt><dd>{{ sellTask ? qty(sellTask.total) : '—' }}</dd></div>
              <div><dt>已成交</dt><dd data-testid="p12-sell-filled">{{ sellTask ? qty(sellTask.filled) : '0 股' }}</dd></div>
              <div><dt>剩余待成交</dt><dd data-testid="p12-sell-remaining">{{ sellTask ? qty(sellTask.remaining) : '—' }}</dd></div>
              <div><dt>执行窗口</dt><dd>次一交易日 09:30–15:00</dd></div>
            </dl>
            <p class="p12-next" data-testid="p12-sell-next">{{ sellNext }}</p>
            <div v-if="sellTask && !sellTask.settled" class="p12-form p12-sell-entry" data-testid="p12-sell-entry">
              <h3>登记实际卖出成交</h3>
              <p class="p12-hint">同一委托可分笔成交；登记每一笔，最后确认整单费用并终结。系统不代为下单。</p>
              <a-button size="small" type="primary" data-testid="p12-open-sell-form" @click="prefillSell">登记卖出成交（已预填）</a-button>
            </div>
          </template>
          <template v-else-if="selected.plan">
            <dl class="p12-plan" data-testid="p12-plan">
              <div><dt>当前持有</dt><dd>{{ qty(selected.applicability.current_qty) }}</dd></div>
              <div><dt>计划目标</dt><dd>{{ qty(selected.plan.target_qty) }}</dd></div>
              <div><dt>还差</dt><dd data-testid="p12-delta">{{ qty(selected.applicability.delta_qty) }}</dd></div>
              <div><dt>有效窗口</dt><dd>{{ time(selected.plan.eligible_at) }} 至 {{ time(selected.plan.expires_at).slice(11) }}</dd></div>
              <div><dt>价格上限</dt><dd>{{ selected.plan.price_cap }}</dd></div>
            </dl>
            <div class="p12-invalid"><span>失效条件</span><ul><li v-for="item in selected.plan.invalidations" :key="item">{{ item }}</li></ul></div>
            <p class="p12-next">{{ label('next_step', selected.applicability.next_step) }}</p>
          </template>

          <div v-else class="p12-noop">
            <b>{{ label('action_code', selected.action_code) }}</b>
            <p v-if="selected.stage === 'exit_plan' && selected.status_code === 'ENDED'" data-testid="p12-ended-note">{{ actionSentence }}</p>
            <p v-else-if="selected.stage === 'exit_plan'" data-testid="p12-sell-note">{{ actionSentence }}</p>
            <p v-else>{{ selected.next_observation || '无需操作' }}</p>
            <small>最近检查：{{ workbench.package.market_date }} 收盘</small>
          </div>

          <div v-if="outcome" class="p12-outcome" data-testid="p12-outcome">
            <h3>{{ label('receipt_status', outcome.status) }}</h3>
            <dl>
              <div><dt>回执</dt><dd><code>{{ outcome.receipt_id.slice(0, 22) }}…</code></dd></div>
              <div><dt>现金</dt><dd data-testid="p12-outcome-cash">{{ money(outcomeBefore) }} → {{ money(outcome.account.cash_total) }}</dd></div>
              <div><dt>持仓</dt><dd data-testid="p12-outcome-position">{{ positionText(outcome.account) }}</dd></div>
              <div><dt>费用</dt><dd>{{ outcome.status === 'POSTED' ? `已确认 ${money(lastFee)}` : `已登记 ${money(lastFee)}，待核对后入账` }}</dd></div>
              <div><dt>账户版本</dt><dd>{{ outcome.account.account_revision }}</dd></div>
            </dl>
            <p v-if="outcome.deviations.length" class="p12-deviation" data-testid="p12-outcome-deviations">偏离说明：{{ outcome.deviations.map(reason).join('；') }}</p>
            <p v-if="outcome.conflicts.length" class="p12-deviation">待核对：{{ outcome.conflicts.map(reason).join('；') }}</p>
            <p v-if="outcome.affected_plans.length" class="p12-hint">受影响的计划将重新检查：{{ outcome.affected_plans.join('、') }}</p>
            <a-button size="small" @click="resetForm">再登记一笔</a-button>
          </div>
        </aside>
      </div>

      <section class="panel p12-section p12-order-panel" data-testid="p12-order-panel">
        <div class="panel-head">
          <div><h2>实际订单登记</h2><p>同一委托可以分笔成交；撤单以券商确认结果为准，未报齐的成交继续占用</p></div>
        </div>
        <form class="p12-form" data-testid="p12-order-form" @submit.prevent="submitOrder">
          <div class="p12-form-row">
            <label>订单编号<input v-model.trim="orderForm.order_id" data-testid="p12-order-id" placeholder="ORD-1" /></label>
            <label>类型
              <select v-model="orderForm.kind" class="p12-select" data-testid="p12-order-kind">
                <option value="fill">一笔成交</option>
                <option value="observation">订单状态（进行中/撤单/终结）</option>
                <option value="fee">整单确认费用</option>
              </select>
            </label>
            <label v-if="orderForm.kind !== 'fee'">方向
              <select v-model="orderForm.side" class="p12-select" data-testid="p12-order-side">
                <option value="buy">买入</option>
                <option value="sell">卖出</option>
              </select>
            </label>
            <label>发生时间<input v-model="orderForm.time" type="datetime-local" data-testid="p12-order-time" /></label>
          </div>
          <div v-if="orderForm.kind === 'fill'" class="p12-form-row">
            <label>证券代码<input v-model.trim="orderForm.security_code" data-testid="p12-order-security" placeholder="600000" /></label>
            <label>本笔数量<input v-model.trim="orderForm.quantity" inputmode="numeric" data-testid="p12-order-quantity" /></label>
            <label>本笔价格<input v-model.trim="orderForm.price" inputmode="decimal" data-testid="p12-order-price" /></label>
            <label>本笔费用<input v-model.trim="orderForm.fee" inputmode="decimal" placeholder="0.00" data-testid="p12-order-fill-fee" /></label>
            <label>委托总量<input v-model.trim="orderForm.accepted_total_qty" inputmode="numeric" placeholder="首次登记必填" data-testid="p12-order-fill-accepted" /></label>
            <label>价格上界<input v-model.trim="orderForm.price_bound" inputmode="decimal" placeholder="首次登记必填" data-testid="p12-order-fill-bound" /></label>
          </div>
          <div v-if="orderForm.kind === 'observation'" class="p12-form-row">
            <label>状态
              <select v-model="orderForm.order_status" class="p12-select" data-testid="p12-order-status">
                <option value="in_progress">进行中</option>
                <option value="cancel_requested">已申请撤单</option>
                <option value="terminal">已终结</option>
              </select>
            </label>
            <label v-if="orderForm.order_status === 'terminal'">终结累计成交<input v-model.trim="orderForm.final_cumulative_fill_qty" inputmode="numeric" placeholder="未知留空" data-testid="p12-order-final" /></label>
            <label v-if="orderForm.order_status === 'terminal'">取消数量<input v-model.trim="orderForm.cancelled_qty" inputmode="numeric" placeholder="未知留空" data-testid="p12-order-cancelled" /></label>
            <label v-if="orderForm.order_status === 'in_progress'">证券代码<input v-model.trim="orderForm.security_code" placeholder="600000" data-testid="p12-order-obs-security" /></label>
            <label v-if="orderForm.order_status === 'in_progress'">委托总量<input v-model.trim="orderForm.accepted_total_qty" inputmode="numeric" data-testid="p12-order-accepted" /></label>
            <label v-if="orderForm.order_status === 'in_progress'">价格上界<input v-model.trim="orderForm.price_bound" inputmode="decimal" data-testid="p12-order-bound" /></label>
            <label v-if="orderForm.order_status === 'in_progress'">费用预留<input v-model.trim="orderForm.fee_upper_bound" inputmode="decimal" placeholder="0.00" data-testid="p12-order-fee-bound" /></label>
          </div>
          <div v-if="orderForm.kind === 'fee'" class="p12-form-row">
            <label>整单确认费用<input v-model.trim="orderForm.fee" inputmode="decimal" data-testid="p12-order-fee" /></label>
          </div>
          <p v-if="orderError" class="p12-form-error" data-testid="p12-order-error">{{ orderError }}</p>
          <a-button type="primary" html-type="submit" :loading="orderSaving" data-testid="p12-order-submit">登记订单事实</a-button>
          <small class="p12-hint">编号 {{ orderForm.report_id }}；重复提交返回同一回执。</small>
        </form>
        <div v-if="orderOutcome" class="p12-outcome" data-testid="p12-order-outcome">
          <h3>{{ label('receipt_status', orderOutcome.status) }}</h3>
          <dl>
            <div><dt>现金</dt><dd data-testid="p12-order-cash">{{ money(orderOutcome.account.cash_total) }}</dd></div>
            <div><dt>已占用</dt><dd data-testid="p12-order-occupied">{{ money(orderOutcome.account.cash_occupied) }}</dd></div>
            <div><dt>可用于新买入</dt><dd data-testid="p12-order-available">{{ formatMaybe(orderOutcome.account.cash_available_for_new).text }}</dd></div>
            <div><dt>事实核对</dt><dd>{{ label('reconciliation_status', orderOutcome.account.reconciliation_status) }}</dd></div>
          </dl>
        </div>
      </section>
    </template>
  </div>
</template>
<script>
import AccountSummary from '../components/AccountSummary.vue'
import PriceChart from '../components/PriceChart.vue'
import accountMixin from '../accountMixin.js'
import { loadWorkbench, newClientId, submitFill, submitOrderEvent } from '../client.js'
import { formatMaybe, formatMoney, formatQuantity, formatTime, label, normaliseMoneyInput, reasonText, timezoneSuffix } from '../format.js'

const GROUPS = ['reconcile', 'exit', 'entry', 'holding', 'unassigned', 'watch', 'ended']

export default {
  components: { AccountSummary, PriceChart },
  mixins: [accountMixin],
  data: () => ({ workbench: null, selectedId: null, chainOpen: false, chainPinned: false, form: {}, formError: '', submitting: false, outcome: null, outcomeBefore: null, lastFee: null, orderForm: {}, orderError: '', orderSaving: false, orderOutcome: null }),
  computed: {
    groups () {
      const items = this.workbench?.opportunities || []
      return GROUPS.map(key => ({ key, items: items.filter(item => item.queue_group === key) })).filter(group => group.items.length)
    },
    selected () { return (this.workbench?.opportunities || []).find(item => item.opportunity_id === this.selectedId) || null },
    otherReasons () {
      const item = this.selected
      if (!item) return []
      // A finished opportunity keeps its trigger reason as history, not as a
      // live "also" constraint, so EXIT_SIGNAL no longer reads as a current ask.
      const ended = item.status_code === 'ENDED'
      return item.reason_codes.filter(code =>
        code !== item.primary_constraint &&
        code !== 'CAPACITY_AVAILABLE' &&
        !code.startsWith('NEW_') &&
        code !== 'WAITING_FOR_PULLBACK' &&
        !(ended && code === 'EXIT_SIGNAL'))
    },
    endedReasons () {
      // The exit that already ran shows its reason once, as history.
      const item = this.selected
      if (!item || item.status_code !== 'ENDED') return []
      return item.reason_codes.filter(code => code === 'EXIT_SIGNAL')
    },
    sellOrders () {
      const code = this.selected?.security_code
      if (!code) return []
      return (this.workbench?.orders || []).filter(o => o.security_code === code && o.side === 'sell')
    },
    sellTask () {
      const item = this.selected
      if (!item || item.stage !== 'exit_plan') return null
      const orders = this.sellOrders
      const filled = orders.reduce((sum, o) => sum + (o.known_fill_qty || 0), 0)
      const accepted = orders.find(o => o.accepted_total_qty)
      const planned = item.plan ? Math.abs(item.plan.delta_qty || item.plan.target_qty || 0) : null
      const open = orders.filter(o => o.status !== 'terminal')
      const position = (this.workbench?.account?.positions || []).find(p => p.security_code === item.security_code)
      const held = position ? position.quantity : 0
      const total = accepted ? accepted.accepted_total_qty : (planned || held + filled)
      const remaining = Math.max((total || 0) - filled, 0)
      const settled = item.status_code === 'ENDED' || (total !== null && filled >= total && !open.length)
      return { filled, total, remaining, open, settled, planned, held }
    },
    sellNext () {
      const item = this.selected
      if (!item || item.stage !== 'exit_plan') return ''
      const t = this.sellTask || { filled: 0, remaining: null, settled: false, open: [], total: null }
      if (item.status_code === 'ENDED' || t.settled) {
        const when = this.workbench?.publication?.market_date || ''
        return `该机会已于 ${when} 卖出 ${formatQuantity(t.filled || t.total || 0)} 结束，无需操作`
      }
      if (t.filled > 0) {
        const open = t.open.length
        return `已成交 ${formatQuantity(t.filled)}，剩余 ${formatQuantity(t.remaining)}；同一委托待终结确认${open ? `（${open} 笔进行中）` : ''}`
      }
      return `今天任务：卖出 ${formatQuantity(t.total || 0)}，在交易窗口内登记实际成交`
    },
    actionSentence () {
      const item = this.selected
      if (!item) return ''
      const plan = item.plan
      const delta = item.applicability ? Math.abs(item.applicability.delta_qty) : null
      if (item.action_code === 'BUY' && plan) return `${formatTime(plan.eligible_at).slice(0, 10)} 开盘后买入 ${formatQuantity(item.applicability.delta_qty)}，成交价不高于 ${plan.price_cap}`
      if (item.action_code === 'SELL') {
        const t = this.sellTask || { filled: 0, remaining: Math.abs(item.applicability ? item.applicability.delta_qty : 0) }
        if (t.filled > 0) return `已卖出 ${formatQuantity(t.filled)}，剩余 ${formatQuantity(t.remaining)} 待成交并在同委托终结`
        const qty = t.remaining || Math.abs((item.applicability && item.applicability.delta_qty) || 0)
        const when = item.applicability && item.applicability.status === 'SCHEDULED' ? '次日开盘后' : '今天'
        return `${when}卖出 ${formatQuantity(qty)}，在交易窗口内登记实际成交`
      }
      if (item.action_code === 'REVIEW' && item.applicability) return label('next_step', item.applicability.next_step)
      if (item.action_code === 'PAUSED') return reasonText(item.primary_constraint) || '新增买入暂停'
      if (item.action_code === 'WAIT') return item.next_observation || reasonText(item.primary_constraint) || '继续等待'
      if (item.action_code === 'HOLD') return '继续持有，等待下一次检查'
      if (item.action_code === 'NONE' && item.stage === 'exit_plan') {
        const qty = this.sellTask && this.sellTask.filled ? formatQuantity(this.sellTask.filled) : '全部持仓'
        const when = this.workbench?.publication?.market_date || ''
        return `该机会已于 ${when} 卖出 ${qty} 结束`
      }
      return '今天没有需要执行的操作'
    }
  },
  watch: { selectedId () { this.resetForm() } },
  methods: {
    async load (accountId) {
      this.workbench = await loadWorkbench(accountId)
      const ids = this.workbench.opportunities.map(item => item.opportunity_id)
      if (!ids.includes(this.selectedId)) this.selectedId = ids[0] || null
    },
    resetForm () {
      const plan = this.selected?.plan
      const clock = this.workbench?.clock || ''
      this.form = { reportId: newClientId('fill'), quantity: plan ? String(Math.max(this.selected.applicability.delta_qty, 0) || plan.delta_qty) : '', price: plan ? plan.price_cap : '', fee: '', time: clock.slice(0, 16) }
      this.formError = ''
      this.outcome = null
      this.resetOrderForm()
    },
    resetOrderForm () {
      const clock = this.workbench?.clock || ''
      this.orderForm = {
        report_id: newClientId('ord'),
        kind: 'fill',
        order_id: '',
        side: 'buy',
        security_code: '',
        quantity: '',
        price: '',
        fee: '',
        accepted_total_qty: '',
        order_status: 'in_progress',
        final_cumulative_fill_qty: '',
        cancelled_qty: '',
        price_bound: '',
        fee_upper_bound: '',
        time: clock.slice(0, 16)
      }
      this.orderError = ''
    },
    async submitOrder () {
      const f = this.orderForm
      const body = {
        report_id: f.report_id,
        kind: f.kind,
        order_id: f.order_id,
        occurred_at: `${f.time}:00${timezoneSuffix(this.workbench?.clock)}`,
        // The transport contract is a closed object: every field must be
        // present, unused ones as null.  Missing keys are rejected with a 422
        // before any semantic check can run, so the form sends the full shape.
        side: f.side || 'buy',
        security_code: f.security_code || '',
        quantity: null,
        price: null,
        confirmed_fee: null,
        accepted_total_qty: null,
        order_status: null,
        final_cumulative_fill_qty: null,
        cancelled_qty: null,
        price_bound: null,
        fee_upper_bound: null,
        order_revision: 1
      }
      if (!f.order_id) { this.orderError = '请填写订单编号'; return }
      if (!f.time) { this.orderError = '请填写发生时间'; return }
      const num = (v) => (v === '' || v === null || v === undefined ? null : Number.parseInt(v, 10))
      if (f.kind === 'fill') {
        const quantity = num(f.quantity)
        const price = normaliseMoneyInput(f.price)
        if (!quantity || quantity <= 0) { this.orderError = '本笔数量需为正整数'; return }
        if (!price) { this.orderError = '本笔价格需为合法金额'; return }
        body.side = f.side
        body.security_code = f.security_code
        body.quantity = quantity
        body.price = price
        body.confirmed_fee = normaliseMoneyInput(f.fee || '0') || '0.00'
        body.accepted_total_qty = num(f.accepted_total_qty)
        body.price_bound = f.price_bound ? normaliseMoneyInput(f.price_bound) : null
        body.fee_upper_bound = f.fee_upper_bound ? normaliseMoneyInput(f.fee_upper_bound) : null
      } else if (f.kind === 'observation') {
        body.order_status = f.order_status
        body.side = f.side
        body.security_code = f.security_code
        body.final_cumulative_fill_qty = num(f.final_cumulative_fill_qty)
        body.cancelled_qty = num(f.cancelled_qty)
        body.accepted_total_qty = num(f.accepted_total_qty)
        body.price_bound = f.price_bound ? normaliseMoneyInput(f.price_bound) : null
        body.fee_upper_bound = f.fee_upper_bound ? normaliseMoneyInput(f.fee_upper_bound) : null
      } else {
        const fee = normaliseMoneyInput(f.fee)
        if (fee === null) { this.orderError = '费用需为合法金额'; return }
        body.confirmed_fee = fee
      }
      this.orderError = ''
      this.orderSaving = true
      try {
        this.orderOutcome = await submitOrderEvent(this.accountId, body)
        const outcome = this.orderOutcome
        await this.load(this.accountId)
        this.orderOutcome = outcome
        this.orderForm.report_id = newClientId('ord')
      } catch (error) {
        this.orderError = reasonText(error.reasonCode) || '登记失败，请核对内容后重试'
      } finally {
        this.orderSaving = false
      }
    },
    async submit () {
      const plan = this.selected.plan
      const quantity = /^\d+$/.test(this.form.quantity) ? Number.parseInt(this.form.quantity, 10) : null
      const price = normaliseMoneyInput(this.form.price)
      const fee = normaliseMoneyInput(this.form.fee)
      if (!quantity || quantity <= 0) { this.formError = '成交数量需为正整数'; return }
      if (!price || price === '0.00') { this.formError = '成交价需为最多两位小数的正数'; return }
      if (fee === null) { this.formError = '请填写券商确认的费用（最多两位小数，没有费用填 0）'; return }
      if (!this.form.time) { this.formError = '请填写成交时间'; return }
      this.formError = ''
      this.submitting = true
      this.outcomeBefore = this.workbench.account.cash_total
      try {
        this.outcome = await submitFill(this.accountId, {
          report_id: this.form.reportId,
          plan_id: plan.plan_id,
          quantity,
          price,
          confirmed_fee: fee,
          occurred_at: `${this.form.time}:00${timezoneSuffix(plan.eligible_at)}`
        })
        this.lastFee = fee
        const outcome = this.outcome
        await this.load(this.accountId)
        this.outcome = outcome
      } catch (error) {
        this.formError = reasonText(error.reasonCode) || '保存失败，请重试（编号不变，重复提交不会重复入账）'
      } finally {
        this.submitting = false
      }
    },
    positionText (account) {
      const line = account.positions.find(item => item.security_code === this.selected?.security_code)
      return line ? formatQuantity(line.quantity) : '0 股'
    },
    statusTone (code) {
      if (['READY'].includes(code)) return 'accepted'
      if (['NEEDS_RECONCILIATION', 'NEEDS_REASSESSMENT', 'MISSING_INPUT', 'POLICY_NOT_ADOPTED'].includes(code)) return 'control'
      if (['EXPIRED', 'ENDED'].includes(code)) return 'locked'
      return 'locked'
    },
    prefillSell () {
      const item = this.selected
      const clock = (this.workbench?.clock || '').slice(0, 16)
      const plan = item.plan
      const remaining = this.sellTask ? this.sellTask.remaining : Math.abs(plan ? plan.delta_qty : 0)
      this.orderForm = {
        report_id: newClientId('ord'),
        kind: 'fill',
        order_id: this.sellTask && this.sellTask.open.length ? this.sellTask.open[0].order_id : '',
        side: 'sell',
        security_code: item.security_code,
        quantity: remaining ? String(remaining) : '',
        price: plan && plan.price_cap ? String(plan.price_cap) : '',
        fee: '',
        accepted_total_qty: plan ? String(Math.abs(plan.delta_qty)) : '',
        order_status: 'in_progress',
        final_cumulative_fill_qty: '',
        cancelled_qty: '',
        price_bound: plan && plan.price_cap ? String(plan.price_cap) : '',
        fee_upper_bound: '',
        time: clock
      }
      this.orderError = ''
      this.orderOutcome = null
      const el = this.$el.querySelector('[data-testid="p12-order-form"]')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    },
    money: formatMoney,
    qty: formatQuantity,
    time: formatTime,
    reason: reasonText,
    formatMaybe,
    label
  }
}
</script>
