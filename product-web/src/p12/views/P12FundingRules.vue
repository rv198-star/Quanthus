<template>
  <div class="page-wrap p12-page" data-testid="p12-funding">
    <div class="page-heading p12-heading">
      <div>
        <span class="eyebrow">MY ACCOUNT · FUNDING AND RULES</span>
        <h1>资金与规则</h1>
        <p>当前采用的资金模式、桶额、储备、账户风险上限和各项规则的采用状态。</p>
      </div>
      <div class="heading-actions">
        <select v-if="accounts.length > 1" class="p12-select" :value="accountId" aria-label="选择账户" @change="selectAccount($event.target.value)">
          <option v-for="item in accounts" :key="item.account_id" :value="item.account_id">{{ item.display_name }}</option>
        </select>
        <span v-if="rules" class="p12-badge synthetic">{{ label('mode', rules.scope.mode) }} {{ rules.scope.account_id }}</span>
        <a-button icon="reload" :loading="loading" @click="refresh">刷新</a-button>
      </div>
    </div>
    <a-alert v-if="loadError" class="p12-alert" type="error" show-icon :message="loadError.text" :description="loadError.detail || loadError.reasonCode" data-testid="p12-error" />
    <template v-if="rules">
      <div class="p12-rules-grid">
        <section class="panel p12-section" data-testid="p12-funding-mode">
          <div class="panel-head"><div><h2>资金模式：{{ label('funding_mode', rules.funding.mode) }}</h2><p>每个桶有固定额度；盈利进入共享储备，亏损按可用储备补足</p></div><code>{{ rules.funding.policy_ref.ref_id }}@{{ rules.funding.policy_ref.version }}</code></div>
          <a-alert v-if="rules.funding.status_reason" class="p12-alert inner" type="info" show-icon :message="reason(rules.funding.status_reason)" :description="`初始分区对应账户版本 ${rules.funding.bound_account_revision}，当前账户版本 ${rules.account_revision}；资金周期接入实际成交后显示实时桶内现金。`" />
          <table class="p12-table">
            <thead><tr><th>资金桶</th><th>目标额度</th><th>桶内现金</th><th>欠资差额</th><th>下次可用额度</th><th>锁定预算</th><th>试仓上限</th><th>状态</th><th>周期</th></tr></thead>
            <tbody><tr v-for="bucket in rules.funding.buckets" :key="bucket.bucket_id"><td>{{ bucket.bucket_id }}</td><td>{{ money(bucket.target_notional) }}</td><td :title="bucketCashHint(bucket)">{{ maybe(bucket.cash).text }}</td><td :title="maybe(bucket.underfunded).reason">{{ maybe(bucket.underfunded).text }}<span v-if="isOverspent(bucket)" class="p12-flag" data-testid="p12-underfunded-flag">待核对</span></td><td :title="maybe(bucket.next_available).reason">{{ maybe(bucket.next_available).text }}</td><td :title="maybe(bucket.locked_cycle_budget).reason">{{ maybe(bucket.locked_cycle_budget).text }}</td><td :title="maybe(bucket.trial_notional_cap).reason">{{ maybe(bucket.trial_notional_cap).text }}</td><td>{{ bucketStatus(bucket) }}</td><td>{{ bucket.cycle_id || '—' }}</td></tr></tbody>
          </table>
          <dl class="p12-inline"><div><dt>共享储备</dt><dd :title="maybe(rules.funding.reserve).reason">{{ maybe(rules.funding.reserve).text }}</dd></div><div><dt>待分摊差额</dt><dd>{{ maybe(rules.funding.unallocated_adjustment).text }}</dd></div><div><dt>资金版本</dt><dd>{{ rules.funding.funding_revision === null ? '—' : rules.funding.funding_revision }}</dd></div></dl>
          <p class="p12-hint" data-testid="p12-retained-mode">盈亏留桶模式：后续能力，当前版本未开放，不能切换。</p>
        </section>
        <section class="panel p12-section" data-testid="p12-risk">
          <div class="panel-head"><div><h2>账户风险上限</h2><p>新买入前按成交后的预计状态检查，触发上限时限制新增并提示</p></div><code>{{ rules.risk.policy_ref.ref_id }}@{{ rules.risk.policy_ref.version }}</code></div>
          <dl class="p12-plan">
            <div><dt>单只股票</dt><dd>{{ percent(rules.risk.stock_limit) }}</dd></div>
            <div><dt>同一行业</dt><dd>{{ percent(rules.risk.industry_limit) }}</dd></div>
            <div><dt>整户仓位</dt><dd>{{ percent(rules.risk.total_limit) }}</dd></div>
            <div><dt>交易单位</dt><dd>{{ rules.risk.lot_size }} 股</dd></div>
            <div><dt>佣金费率</dt><dd>{{ percent(rules.risk.commission_rate) }}，最低 {{ money(rules.risk.minimum_commission) }}</dd></div>
            <div><dt>执行口径</dt><dd>{{ rules.execution_profile }}</dd></div>
            <div><dt>组合版本</dt><dd><code>{{ rules.composition_ref.ref_id }}@{{ rules.composition_ref.version }}</code></dd></div>
          </dl>
        </section>
      </div>
      <section class="panel p12-section" data-testid="p12-adoption">
        <div class="panel-head"><div><h2>规则采用状态</h2><p>合成账户使用已配置的合成规则；日常模拟需要各项规则正式采用后才会给出新增买入</p></div><span :class="['status-tag', rules.action_blockers.length ? 'control' : 'accepted']">{{ rules.action_blockers.length ? '当前账户有缺项' : '当前账户可用' }}</span></div>
        <table class="p12-table">
          <thead><tr><th>编号</th><th>内容</th><th>用途</th><th>负责方</th><th v-for="mode in modes" :key="mode">{{ label('mode', mode) }}</th></tr></thead>
          <tbody><tr v-for="row in adoptionRows" :key="row.id"><td>{{ row.id }}</td><td>{{ row.title }}</td><td>{{ row.consumer }}</td><td>{{ row.owner }}</td><td v-for="mode in modes" :key="mode"><template v-if="row.byMode[mode]"><span :class="['status-tag', tone(row.byMode[mode].status)]">{{ label('adoption_status', row.byMode[mode].status) }}</span><small v-if="row.byMode[mode].missing_fields.length"><br />缺：{{ row.byMode[mode].missing_fields.join('、') }}</small></template><span v-else>—</span></td></tr></tbody>
        </table>
      </section>
    </template>
  </div>
</template>
<script>
import accountMixin from '../accountMixin.js'
import { loadFundingRules } from '../client.js'
import { formatMaybe, formatMoney, label, ratioToPercent, reasonText } from '../format.js'

export default {
  mixins: [accountMixin],
  data: () => ({ rules: null }),
  computed: {
    modes () { return [...new Set((this.rules?.adoption || []).map(item => item.mode))] },
    adoptionRows () {
      const rows = new Map()
      for (const item of this.rules?.adoption || []) {
        const row = rows.get(item.binding_id) || { id: item.binding_id, title: item.title, consumer: item.consumer, owner: item.owner, byMode: {} }
        row.byMode[item.mode] = item
        rows.set(item.binding_id, row)
      }
      return [...rows.values()].sort((a, b) => a.id.localeCompare(b.id))
    }
  },
  methods: {
    async load (accountId) { this.rules = await loadFundingRules(accountId) },
    tone (status) { return status === 'adopted' || status === 'configured' ? 'accepted' : 'control' },
    maybe: value => formatMaybe(value),
    money: formatMoney,
    percent: ratioToPercent,
    reason: reasonText,
    // A fill posted above the plan price leaves the bucket owing money: the
    // cash cell then reads unknown(INSUFFICIENT_CASH) while underfunded holds
    // the real gap.  Say the fill is recorded and the gap is the bucket's
    // budget shortfall; only flag the underfunded amount as the item to check.
    isOverspent (bucket) { return bucket.cash.value === null && bucket.cash.reason_code === 'INSUFFICIENT_CASH' },
    bucketStatus (bucket) {
      if (this.isOverspent(bucket)) return '成交已入账·桶内欠资'
      if (bucket.cash.value === null) return '未接入实际成交'
      return bucket.phase === 'empty' ? '空闲' : bucket.phase
    },
    bucketCashHint (bucket) {
      if (this.isOverspent(bucket)) return '该笔成交已记入账户账本；缺口是 5,000 元桶预算的欠资，桶内现金暂时无法给出确定值。欠资差额一项标为待核对。'
      return this.maybe(bucket.cash).reason
    },
    label
  }
}
</script>
