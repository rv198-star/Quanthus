<template>
  <section class="p12-summary" data-testid="p12-account-summary">
    <div class="p12-summary-item primary">
      <span>总经济权益</span>
      <b :class="{ unknown: equity.unknown }">{{ equity.text }}</b>
      <small>{{ equity.unknown ? equity.reason : '现金 + 持仓按同一价格快照估值' }}</small>
    </div>
    <div class="p12-summary-item">
      <span>现金</span>
      <b>{{ money(account.cash_total) }}</b>
      <small>可用于新买入 <em :class="{ unknown: available.unknown }" :title="available.reason">{{ available.text }}</em> · 已占用 {{ money(account.cash_occupied) }}</small>
    </div>
    <div class="p12-summary-item">
      <span>持仓市值</span>
      <b :class="{ unknown: positions.unknown }">{{ positions.text }}</b>
      <small>{{ account.positions.length }} 只持仓</small>
    </div>
    <div class="p12-summary-item">
      <span>储备</span>
      <b :class="{ unknown: reserve.unknown }">{{ reserve.text }}</b>
      <small>{{ reserve.unknown ? reserve.reason : '固定桶额模式的共享储备' }}</small>
    </div>
    <div class="p12-summary-item">
      <span>最大单股占比</span>
      <b :class="{ unknown: concentration.unknown }">{{ concentration.text }}</b>
      <small>{{ account.top_concentration_security ? account.top_concentration_security : '暂无持仓' }}</small>
    </div>
    <div :class="['p12-summary-item', account.reconciliation_status === 'CLEAN' ? 'clean' : 'attention']" data-testid="p12-reconciliation">
      <span>事实核对</span>
      <b>{{ label('reconciliation_status', account.reconciliation_status) }}</b>
      <small>{{ account.pending_reconciliations ? `${account.pending_reconciliations} 项待核对` : `账户版本 ${account.account_revision}` }}</small>
    </div>
  </section>
</template>
<script>
import { formatMaybe, formatMoney, label, ratioToPercent } from '../format.js'

export default {
  props: { account: { type: Object, required: true } },
  computed: {
    equity () { return formatMaybe(this.account.economic_equity) },
    available () { return formatMaybe(this.account.cash_available_for_new) },
    positions () { return formatMaybe(this.account.positions_value) },
    reserve () { return formatMaybe(this.account.reserve) },
    concentration () { return formatMaybe(this.account.top_concentration, ratioToPercent) }
  },
  methods: { money: formatMoney, label }
}
</script>
