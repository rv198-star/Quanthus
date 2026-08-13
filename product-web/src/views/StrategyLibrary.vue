<template>
  <div class="page-wrap" data-testid="candidates-page">
    <div class="page-heading">
      <div><span class="eyebrow">CANDIDATE GOVERNANCE</span><h1>候选验证工作台</h1><p>回放结果、证据复核、治理决定和执行授权分别记录，互不替代。</p></div>
      <div class="heading-actions"><a-input-search v-model="query" placeholder="搜索名称或策略 ID" allow-clear /><a-select v-model="axis"><a-select-option value="review">按复核筛选</a-select-option><a-select-option value="governance">按治理筛选</a-select-option></a-select><a-select v-model="filter"><a-select-option value="all">全部状态</a-select-option><a-select-option v-for="option in filterOptions" :key="option.value" :value="option.value">{{ option.label }}</a-select-option></a-select><a-select v-model="sort"><a-select-option value="return-desc">收益从高到低</a-select-option><a-select-option value="drawdown-asc">回撤从低到高</a-select-option><a-select-option value="trades-desc">交易数从多到少</a-select-option></a-select></div>
    </div>
    <div class="workflow-strip" aria-label="候选治理流程"><span class="done"><b>1</b>候选版本</span><i></i><span class="done"><b>2</b>DWD 回放</span><i></i><span class="active"><b>3</b>证据复核</span><i></i><span><b>4</b>治理决定</span><i></i><span><b>5</b>执行授权</span></div>
    <p class="workflow-note"><a-icon type="database" /> 五个候选共享一次数据读取与特征面板构建，避免在个人机器重复占用资源。</p>
    <ReplayJobs ref="replayJobs" :user="user" />
    <div class="library-summary"><span><b>{{ strategies.length }}</b> 个候选版本</span><span><b>{{ replayedCount }}</b> 回放成功</span><span><b>{{ reviewPendingCount }}</b> 待复核</span><span><b>{{ governanceReviewCount }}</b> 治理审查中</span><span><b>{{ admittedCount }}</b> 治理准入</span><span><b>{{ authorizedCount }}</b> 执行授权</span><span class="boundary"><i></i>执行权限独立关闭</span></div>
    <section class="panel table-panel">
      <div class="strategy-table-head approval-head"><span>候选版本</span><span>回放</span><span>证据复核</span><span>治理</span><span>执行</span><span>收益 / 回撤</span><span></span></div>
      <button v-for="item in filtered" :key="item.strategy_id" class="strategy-row strategy-button approval-row" :data-testid="`strategy-${item.strategy_id}`" @click="openCandidate(item)">
        <div class="strategy-primary"><span class="strategy-icon"><a-icon type="experiment" /></span><div><h2>{{ item.name }}</h2><code>{{ item.strategy_id }}@{{ item.version }}</code><p>{{ item.summary }}</p><small v-if="item.external_conformance" class="conformance-note"><a-icon type="warning" /> {{ item.external_conformance.fill_date_side_match }} · 存在已解释执行差异</small></div></div>
        <div><span class="status-tag accepted">回放成功</span><small>{{ item.closed_trades }} 笔闭合交易</small></div>
        <div><span :class="['status-tag', stateClass(reviewState(item))]">{{ stateLabel(reviewState(item)) }}</span><small>证据包复核</small></div>
        <div><span :class="['status-tag', stateClass(governanceState(item))]">{{ stateLabel(governanceState(item)) }}</span><small>{{ packageLabel(item) }}</small></div>
        <div><span class="status-tag locked">未授权</span><small>治理准入不授权</small></div>
        <div class="paired-metric"><b :class="item.total_return >= 0 ? 'positive' : 'negative'">{{ signed(item.total_return) }}</b><small>回撤 {{ percent(item.max_drawdown) }}</small></div>
        <div class="row-action"><a-icon type="right" /></div>
      </button>
      <a-empty v-if="!filtered.length" description="没有匹配的候选" />
    </section>
  </div>
</template>
<script>
import ReplayJobs from '../components/ReplayJobs.vue'
const REVIEW_OPTIONS = [
  { value: 'pending-review', label: '待复核' }, { value: 'reviewing', label: '复核中' },
  { value: 'changes-requested', label: '待补证' }, { value: 'review-passed', label: '复核通过' },
  { value: 'review-failed', label: '复核未通过' }
]
const GOVERNANCE_OPTIONS = [
  { value: 'governance-not-submitted', label: '未提交治理' }, { value: 'governance-under-review', label: '治理审查中' },
  { value: 'governance-returned', label: '治理退回' }, { value: 'governance-withdrawn', label: '已撤回' },
  { value: 'governance-rejected', label: '治理拒绝' }, { value: 'governance-admitted', label: '治理准入' }
]
const LABELS = Object.fromEntries([...REVIEW_OPTIONS, ...GOVERNANCE_OPTIONS].map(item => [item.value, item.label]))

export default {
  components: { ReplayJobs },
  props: { catalog: Object, user: Object },
  data: () => ({ query: '', axis: 'review', filter: 'all', sort: 'return-desc' }),
  computed: {
    strategies () { return this.catalog?.strategies || [] },
    filterOptions () { return this.axis === 'review' ? REVIEW_OPTIONS : GOVERNANCE_OPTIONS },
    replayedCount () { return this.strategies.filter(x => x.approval?.replay?.state === 'replay-succeeded').length },
    reviewPendingCount () { return this.strategies.filter(x => this.reviewState(x) === 'pending-review').length },
    governanceReviewCount () { return this.strategies.filter(x => this.governanceState(x) === 'governance-under-review').length },
    admittedCount () { return this.strategies.filter(x => this.governanceState(x) === 'governance-admitted').length },
    authorizedCount () { return this.strategies.filter(x => x.approval?.execution?.authorized === true).length },
    filtered () {
      const q = this.query.trim().toLowerCase()
      const stateOf = this.axis === 'review' ? this.reviewState : this.governanceState
      const rows = this.strategies.filter(x => (this.filter === 'all' || stateOf(x) === this.filter) && (!q || `${x.name} ${x.strategy_id}`.toLowerCase().includes(q)))
      const comparisons = { 'return-desc': (a, b) => b.total_return - a.total_return, 'drawdown-asc': (a, b) => a.max_drawdown - b.max_drawdown, 'trades-desc': (a, b) => b.closed_trades - a.closed_trades }
      return [...rows].sort(comparisons[this.sort])
    }
  },
  watch: { axis () { this.filter = 'all' } },
  methods: {
    openCandidate (item) { this.$router.push(`/candidates/${encodeURIComponent(item.strategy_id)}`) },
    reviewState: item => item.approval?.review?.state || 'pending-review',
    governanceState: item => item.approval?.governance?.state || 'governance-not-submitted',
    packageLabel: item => item.approval?.governance?.package_id ? '证据包已绑定' : '尚无治理包',
    percent: v => `${Number(v).toFixed(2)}%`,
    signed: v => `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`,
    stateLabel: state => LABELS[state] || state,
    stateClass: state => state.includes('passed') || state.includes('admitted') ? 'accepted' : state.includes('failed') || state.includes('rejected') || state.includes('changes') || state.includes('returned') ? 'rejected' : state.includes('not-submitted') || state.includes('withdrawn') ? 'locked' : 'control'
  }
}
</script>
