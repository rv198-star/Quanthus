<template>
  <div class="p12-summary-wrap">
    <section class="p12-summary" data-testid="p12-account-summary">
      <div class="p12-summary-item primary">
        <span>总经济权益</span>
        <b class="p12-num" :class="{ unknown: equity.unknown }">{{ equity.text }}</b>
        <small>{{ equity.unknown ? equity.reason : '现金 + 持仓按同一价格快照估值' }}</small>
      </div>
      <div class="p12-summary-item">
        <span>模拟现金</span>
        <b class="p12-num">{{ money(account.cash_total) }}</b>
        <small>可买新仓 <em class="p12-num" :class="{ unknown: available.unknown }" :title="available.reason">{{ available.text }}</em> · 冻结 {{ money(account.cash_occupied) }}</small>
      </div>
      <div class="p12-summary-item">
        <span>持仓市值</span>
        <b class="p12-num" :class="{ unknown: positions.unknown }">{{ positions.text }}</b>
        <small>{{ account.positions.length }} 只持仓</small>
      </div>
      <div class="p12-summary-item">
        <span>共享储备</span>
        <b class="p12-num" :class="{ unknown: reserve.unknown }">{{ reserve.text }}</b>
        <small>{{ reserve.unknown ? reserve.reason : '固定桶额结算盈余池' }}</small>
      </div>
      <div class="p12-summary-item">
        <span>最大单股占比</span>
        <b class="p12-num" :class="{ unknown: concentration.unknown }">{{ concentration.text }}</b>
        <small>{{ account.top_concentration_security ? `最高：${account.top_concentration_security}` : '暂无持仓集中度' }}</small>
      </div>
      <div :class="['p12-summary-item', account.reconciliation_status === 'CLEAN' ? 'clean' : 'attention']" data-testid="p12-reconciliation">
        <span>账实核对</span>
        <b>{{ label('reconciliation_status', account.reconciliation_status) }}</b>
        <small v-if="account.pending_reconciliations">
          <router-link :to="{ path: '/p12/records', query: accountQuery }" class="p12-inline-link">{{ account.pending_reconciliations }} 项待核对 →</router-link>
        </small>
        <small v-else>账目一致 · 流水已对齐</small>
      </div>
    </section>

    <div v-if="buckets && buckets.length" class="p12-swimlane-bar" data-testid="p12-swimlane-bar">
      <div class="p12-swimlane-head">
        <button type="button" class="p12-swimlane-toggle" data-testid="p12-swimlane-toggle" @click="swimlaneOpen = !swimlaneOpen">
          <a-icon :type="(swimlaneOpen || swimlanePinned) ? 'down' : 'right'" />
          <strong>资金桶占用（固定桶额 · {{ buckets.length }} 桶）</strong>
          <span>点击定位桶内股票或核对剩余空闲桶</span>
        </button>
        <div class="p12-swimlane-actions">
          <label class="p12-pin"><input v-model="swimlanePinned" type="checkbox" /> 固定展开</label>
          <router-link :to="{ path: '/p12/funding', query: accountQuery }" class="p12-inline-link">管理资金与规则 →</router-link>
        </div>
      </div>
      <div v-if="swimlaneOpen || swimlanePinned" class="p12-swimlane-grid" data-testid="p12-swimlane-grid">
        <button
          v-for="bucket in buckets"
          :key="bucket.bucket_id"
          type="button"
          :class="['p12-bucket-lane', bucketTone(bucket)]"
          @click="$emit('select-bucket', bucket)"
        >
          <div class="p12-bucket-lane-top">
            <b>{{ bucketName(bucket.bucket_id) }}</b>
            <span :class="['status-tag', bucketTagTone(bucket)]">{{ bucketPhaseText(bucket) }}</span>
          </div>
          <div class="p12-bucket-lane-nums p12-num">
            <span>目标 {{ money(bucket.target_notional) }}</span>
            <span v-if="bucket.trial_notional_cap && bucket.trial_notional_cap.value">试仓上限 {{ money(bucket.trial_notional_cap.value) }}</span>
            <span v-else-if="bucket.cash && bucket.cash.value">桶内现金 {{ money(bucket.cash.value) }}</span>
          </div>
          <small class="p12-bucket-lane-sub">{{ bucketCycleText(bucket) }}</small>
        </button>
      </div>
    </div>
  </div>
</template>
<script>
import { formatMaybe, formatMoney, humanizeBucketId, label, ratioToPercent } from '../format.js'

export default {
  props: {
    account: { type: Object, required: true },
    buckets: { type: Array, default: () => [] },
    accountId: { type: String, default: '' }
  },
  data: () => ({
    swimlaneOpen: false,
    swimlanePinned: false
  }),
  computed: {
    equity () { return formatMaybe(this.account.economic_equity) },
    available () { return formatMaybe(this.account.cash_available_for_new) },
    positions () { return formatMaybe(this.account.positions_value) },
    reserve () { return formatMaybe(this.account.reserve) },
    concentration () { return formatMaybe(this.account.top_concentration, ratioToPercent) },
    accountQuery () { return this.accountId ? { account: this.accountId } : {} }
  },
  methods: {
    money: formatMoney,
    label,
    bucketName: humanizeBucketId,
    isOverspent (bucket) {
      return bucket.cash && bucket.cash.value === null && bucket.cash.reason_code === 'INSUFFICIENT_CASH'
    },
    bucketTone (bucket) {
      if (this.isOverspent(bucket)) return 'overspent'
      if (bucket.phase === 'empty') return 'idle'
      return 'occupied'
    },
    bucketTagTone (bucket) {
      if (this.isOverspent(bucket)) return 'control'
      if (bucket.phase === 'empty') return 'accepted'
      return 'locked'
    },
    bucketPhaseText (bucket) {
      if (this.isOverspent(bucket)) return '超额欠资待核对'
      if (bucket.phase === 'empty') return '空闲可用'
      if (bucket.phase === 'reserved') return '已预约计划'
      if (bucket.phase === 'active' || bucket.phase === 'holding') return '持仓占用中'
      if (bucket.phase === 'cooldown') return '清仓冷却中'
      return bucket.phase
    },
    bucketCycleText (bucket) {
      if (this.isOverspent(bucket)) {
        const gap = bucket.underfunded && bucket.underfunded.value ? formatMoney(bucket.underfunded.value) : '待核对'
        return `实际买价高于桶预算，欠资 ${gap}`
      }
      if (!bucket.cycle_id) return '当前未绑定股票，下一轮新机会可优先分配'
      const codeMatch = /(\d{6})/.exec(bucket.cycle_id)
      return codeMatch ? `关联标的代号 ${codeMatch[1]} · 点击定位` : `占用周期 ${bucket.cycle_id}`
    }
  }
}
</script>
