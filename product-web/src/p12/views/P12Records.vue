<template>
  <div class="page-wrap p12-page" data-testid="p12-records">
    <div class="page-heading p12-heading">
      <div>
        <span class="eyebrow">MY ACCOUNT · RECORDS</span>
        <h1>记录</h1>
        <p>原计划、原报告、成交事实与更正、账本流水按同一账户串联；更正只追加，不覆盖原记录。</p>
      </div>
      <div class="heading-actions">
        <select v-if="accounts.length > 1" class="p12-select" :value="accountId" aria-label="选择账户" @change="selectAccount($event.target.value)">
          <option v-for="item in accounts" :key="item.account_id" :value="item.account_id">{{ item.display_name }}</option>
        </select>
        <span v-if="records" class="p12-badge synthetic">{{ label('mode', records.scope.mode) }} {{ records.scope.account_id }}</span>
        <a-button icon="reload" :loading="loading" @click="refresh">刷新</a-button>
      </div>
    </div>
    <a-alert v-if="loadError" class="p12-alert" type="error" show-icon :message="loadError.text" :description="loadError.detail || loadError.reasonCode" data-testid="p12-error" />
    <template v-if="records">
      <section class="panel p12-section">
        <div class="panel-head"><div><h2>账户起点</h2><p>建立于 {{ time(records.baseline.as_of) }} · 来源 {{ records.baseline.source_id }} · 覆盖 {{ records.baseline.coverage_id }}</p></div><span class="status-tag locked">{{ records.baseline.request_id }}</span></div>
        <dl class="p12-inline"><div><dt>初始现金</dt><dd>{{ money(records.baseline.cash) }}</dd></div><div><dt>当前现金</dt><dd data-testid="p12-records-cash">{{ money(records.account.cash_total) }}</dd></div><div><dt>账户版本</dt><dd>{{ records.account.account_revision }}</dd></div><div><dt>账本摘要</dt><dd><code>{{ records.account.ledger_digest.slice(0, 19) }}…</code></dd></div></dl>
      </section>

      <section class="panel p12-section" data-testid="p12-records-plans">
        <div v-if="records.package" class="panel-head"><div><h2>原计划</h2><p>决策包 {{ records.package.package_id }} · {{ records.package.market_date }} 收盘后发布，发布后不再修改</p></div></div>
        <div v-else class="panel-head"><div><h2>原计划</h2><p>{{ records.publication.market_date }} 的盘后计划将于 {{ time(records.publication.published_at) }} 发布</p></div></div>
        <table v-if="records.package" class="p12-table">
          <thead><tr><th>计划</th><th>证券</th><th>目标</th><th>价格上限</th><th>窗口</th><th>账户状态</th></tr></thead>
          <tbody><tr v-for="plan in records.package.plans" :key="plan.plan_id"><td><code>{{ plan.plan_id }}</code></td><td>{{ plan.security_code }}</td><td>{{ qty(plan.target_qty) }}</td><td>{{ plan.price_cap }}</td><td>{{ time(plan.eligible_at) }} – {{ time(plan.expires_at).slice(11) }}</td><td>{{ label('plan_status', planStatus(plan.plan_id)) }}</td></tr></tbody>
        </table>
      </section>

      <section class="panel p12-section" data-testid="p12-records-reports">
        <div class="panel-head"><div><h2>原报告</h2><p>每份报告保留原始内容与回执，重复提交返回同一回执</p></div></div>
        <p v-if="!records.reports.length" class="p12-empty">还没有登记过成交。</p>
        <table v-else class="p12-table">
          <thead><tr><th>报告</th><th>成交</th><th>计划</th><th>偏离</th><th>状态</th><th>接收时间</th></tr></thead>
          <tbody><tr v-for="item in records.reports" :key="item.report_id"><td><code>{{ item.report_id }}</code></td><td>{{ qty(item.quantity) }} @ {{ item.price }} · 费用 {{ money(item.confirmed_fee) }}<br /><small>{{ time(item.occurred_at) }}</small></td><td>{{ qty(item.intended_quantity) }} ≤ {{ item.intended_price_cap }}</td><td>{{ item.deviations.length ? item.deviations.map(reason).join('；') : '无' }}</td><td><span :class="['status-tag', item.status === 'POSTED' ? 'accepted' : 'control']">{{ label('receipt_status', item.status) }}</span><small v-if="item.conflicts.length" data-testid="p12-report-conflicts"><br />{{ item.conflicts.map(reason).join('；') }}</small></td><td>{{ time(item.received_at) }}</td></tr></tbody>
        </table>
      </section>

      <section class="panel p12-section" data-testid="p12-records-facts">
        <div class="panel-head"><div><h2>成交事实与更正链</h2><p>当前有效值来自更正链末端；更正需基于最新版本</p></div></div>
        <p v-if="!records.facts.length" class="p12-empty">暂无成交事实。</p>
        <div v-for="fact in records.facts" :key="fact.fact_id" class="p12-fact">
          <div class="p12-fact-head">
            <div><b>{{ fact.security_code }}</b> {{ qty(fact.quantity) }} @ {{ fact.price }} <small>版本 {{ fact.fact_revision }} · 来自 {{ fact.fact_id }}</small></div>
            <a-button size="small" :data-testid="`p12-correct-${fact.fact_id}`" @click="openCorrection(fact)">更正</a-button>
          </div>
          <ol v-if="fact.corrections.length" class="p12-chain-list">
            <li v-for="item in fact.corrections" :key="item.correction_id">{{ time(item.received_at) }} · 基于版本 {{ item.expected_fact_revision }} 改为 {{ qty(item.quantity) }} @ {{ item.price }} · {{ item.reason }} · <span>{{ label('receipt_status', item.status) }}</span><span v-if="item.error_code">（{{ reason(item.error_code) }}）</span></li>
          </ol>
          <form v-if="editing && editing.fact_id === fact.fact_id" class="p12-form compact" data-testid="p12-correction-form" @submit.prevent="submitCorrection">
            <label>正确数量（股）<input v-model.trim="editing.quantity" inputmode="numeric" data-testid="p12-correct-quantity" /></label>
            <label>正确价格<input v-model.trim="editing.price" inputmode="decimal" data-testid="p12-correct-price" /></label>
            <label class="wide">更正原因<input v-model.trim="editing.reason" maxlength="200" data-testid="p12-correct-reason" /></label>
            <p v-if="editError" class="p12-form-error">{{ editError }}</p>
            <div class="p12-form-actions"><a-button type="primary" html-type="submit" :loading="saving" data-testid="p12-correct-submit">提交更正</a-button><a-button @click="editing = null">取消</a-button></div>
          </form>
        </div>
        <div v-if="correctionOutcome" class="p12-outcome" data-testid="p12-correction-outcome"><b>{{ label('receipt_status', correctionOutcome.status) }}</b> · 现金变化 {{ signed(correctionOutcome.cash_delta) }} · 数量变化 {{ correctionOutcome.quantity_delta }} 股 · 当前现金 {{ money(correctionOutcome.account.cash_total) }}</div>
      </section>

      <section class="panel p12-section" data-testid="p12-records-events">
        <div class="panel-head"><div><h2>账本流水</h2><p>按提交顺序；现金列来自平衡分录</p></div><span class="status-tag locked">待投递提醒 {{ records.outbox_pending }}</span></div>
        <table class="p12-table">
          <thead><tr><th>序号</th><th>类型</th><th>事实</th><th>现金</th><th>持仓成本</th><th>数量</th><th>账户版本</th><th>记录时间</th></tr></thead>
          <tbody><tr v-for="event in records.events" :key="event.event_id"><td>{{ event.event_sequence }}</td><td>{{ label('event_kind', event.kind) }}</td><td><code>{{ event.fact_id }}</code></td><td>{{ signed(event.cash_delta) }}</td><td>{{ signed(event.position_delta) }}</td><td>{{ event.quantity_delta > 0 ? '+' : '' }}{{ event.quantity_delta }}</td><td>{{ event.revision }}</td><td>{{ time(event.recorded_at) }}</td></tr></tbody>
        </table>
      </section>
    </template>
  </div>
</template>
<script>
import accountMixin from '../accountMixin.js'
import { correctFill, loadRecords, newClientId } from '../client.js'
import { formatMoney, formatQuantity, formatSignedMoney, formatTime, label, normaliseMoneyInput, reasonText } from '../format.js'

export default {
  mixins: [accountMixin],
  data: () => ({ records: null, editing: null, editError: '', saving: false, correctionOutcome: null }),
  methods: {
    async load (accountId) { this.records = await loadRecords(accountId) },
    planStatus (planId) { return this.records.plans.find(item => item.plan_id === planId)?.status || null },
    openCorrection (fact) {
      this.editing = { fact_id: fact.fact_id, revision: fact.fact_revision, quantity: String(fact.quantity), price: fact.price, reason: '', correctionId: newClientId('corr') }
      this.editError = ''
      this.correctionOutcome = null
    },
    async submitCorrection () {
      const quantity = /^\d+$/.test(this.editing.quantity) ? Number.parseInt(this.editing.quantity, 10) : null
      const price = normaliseMoneyInput(this.editing.price)
      if (!quantity) { this.editError = '数量需为正整数'; return }
      if (!price || price === '0.00') { this.editError = '价格需为最多两位小数的正数'; return }
      if (!this.editing.reason) { this.editError = '请写明更正原因，例如“券商对账单为 300 股”'; return }
      this.saving = true
      try {
        this.correctionOutcome = await correctFill(this.accountId, {
          correction_id: this.editing.correctionId,
          fact_id: this.editing.fact_id,
          expected_fact_revision: this.editing.revision,
          quantity,
          price,
          reason: this.editing.reason
        })
        this.editing = null
        await this.load(this.accountId)
      } catch (error) {
        this.editError = reasonText(error.reasonCode) || '更正失败'
      } finally { this.saving = false }
    },
    money: formatMoney,
    signed: formatSignedMoney,
    qty: formatQuantity,
    time: formatTime,
    reason: reasonText,
    label
  }
}
</script>
