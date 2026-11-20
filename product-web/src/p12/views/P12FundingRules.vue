<template>
  <div class="page-wrap p12-page" data-testid="p12-funding">
    <div class="p12-today-bar">
      <div class="p12-today-main">
        <div class="p12-today-title-row">
          <h1>资金与规则</h1>
          <span
            v-if="rules"
            :class="['p12-badge', rules.scope.mode === 'synthetic' ? 'synthetic' : 'simulation']"
          >{{ accountModeBadge }}</span>
        </div>
        <p class="p12-today-conclusion">
          查看固定桶额、共享储备与风险上限，预览参数调整对下一轮空桶与在途持仓的影响；未接通写入接口的能力如实标注，绝不假冒保存成功。
        </p>
      </div>
      <div class="heading-actions">
        <router-link :to="{ path: '/p12', query: accountRouteQuery }" class="p12-nav-pill">← 返回工作台</router-link>
        <router-link :to="{ path: '/p12/records', query: accountRouteQuery }" class="p12-nav-pill">委托与成交记录 →</router-link>
        <select
          v-if="accounts.length > 1"
          class="p12-select"
          :value="accountId"
          aria-label="选择账户"
          @change="selectAccount($event.target.value)"
        >
          <option v-for="item in accounts" :key="item.account_id" :value="item.account_id">{{ item.display_name }}</option>
        </select>
        <a-button icon="reload" :loading="loading" @click="refresh">刷新</a-button>
      </div>
    </div>

    <a-alert
      v-if="loadError"
      class="p12-alert"
      type="error"
      show-icon
      :message="loadError.text"
      :description="loadError.detail || loadError.reasonCode"
      data-testid="p12-error"
    />

    <template v-if="rules">
      <!-- Three-Tier Capability Boundary Legend -->
      <section class="panel p12-capability-strip" data-testid="p12-capability-legend">
        <div class="p12-cap-item">
          <span class="status-tag accepted">可用 · 已生效</span>
          <p>固定桶额分配（每桶一股）、清仓盈余归入共享储备、亏损按有限储备补足、100 股整手与账本留痕</p>
        </div>
        <div class="p12-cap-item">
          <span class="status-tag locked">{{ rules.scope.mode === 'synthetic' ? '演示 · 演练参数' : '本地预览可测算' }}</span>
          <p>{{ rules.scope.mode === 'synthetic' ? '当前展示合成演练账户固定参数（2 桶 × ¥5,000.00，60%/60%/80% 风险阈值）' : '桶数、单桶目标额与风险比例可在下方调整并实时测算下一轮影响' }}</p>
        </div>
        <div class="p12-cap-item">
          <span class="status-tag control">待开发 · 未开放写入</span>
          <p>盈亏留桶模式切换（当前不提供假开关）；个人资金参数持久化需待服务端开放配置写接口后落盘</p>
        </div>
      </section>

      <div class="p12-rules-grid">
        <!-- Left: Active Fixed-Bucket Ledger & Settlement Mode -->
        <section class="panel p12-section" data-testid="p12-funding-mode">
          <div class="panel-head">
            <div>
              <h2>当前生效资金模式：{{ label('funding_mode', rules.funding.mode) }}（默认开启）</h2>
              <p>每桶同时只持有一只股票；完整清仓后超出目标桶额的净盈利转入共享储备，亏损从有限共享储备补足</p>
            </div>
            <span class="status-tag accepted">可用 · 已生效</span>
          </div>

          <a-alert
            v-if="rules.funding.status_reason"
            class="p12-alert inner"
            type="info"
            show-icon
            :message="reason(rules.funding.status_reason)"
            :description="`初始分区对应账户修订 ${rules.funding.bound_account_revision}，当前账户修订 ${rules.account_revision}；资金周期绑定实际成交后更新实时桶内现金。`"
          />

          <div class="p12-table-wrap">
            <table class="p12-table">
              <thead>
                <tr>
                  <th>资金桶</th>
                  <th>目标桶额</th>
                  <th>桶内可用现金</th>
                  <th>欠资差额</th>
                  <th>下轮可用预算</th>
                  <th>当轮锁定预算</th>
                  <th>试仓上限</th>
                  <th>当前状态</th>
                  <th>关联周期 / 标的</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="bucket in rules.funding.buckets" :key="bucket.bucket_id">
                  <td>
                    <b>{{ bucketName(bucket.bucket_id) }}</b>
                    <br /><small><code>{{ bucket.bucket_id }}</code></small>
                  </td>
                  <td class="p12-num">{{ money(bucket.target_notional) }}</td>
                  <td class="p12-num" :title="bucketCashHint(bucket)">{{ formatBucketCell(bucket.cash, bucket) }}</td>
                  <td class="p12-num" :title="maybe(bucket.underfunded).reason">
                    {{ formatUnderfundedCell(bucket) }}
                    <span v-if="isOverspent(bucket)" class="p12-flag" data-testid="p12-underfunded-flag">待核对</span>
                  </td>
                  <td class="p12-num" :title="maybe(bucket.next_available).reason">{{ formatSecondaryBucketCell(bucket.next_available) }}</td>
                  <td class="p12-num" :title="maybe(bucket.locked_cycle_budget).reason">{{ formatSecondaryBucketCell(bucket.locked_cycle_budget) }}</td>
                  <td class="p12-num" :title="maybe(bucket.trial_notional_cap).reason">{{ formatSecondaryBucketCell(bucket.trial_notional_cap) }}</td>
                  <td class="p12-status-cell">
                    <b>{{ bucketStatus(bucket) }}</b>
                    <small v-if="isOverspent(bucket)" class="p12-overspent-note">
                      <br />{{ bucketCashHint(bucket) }}
                    </small>
                  </td>
                  <td>{{ formatCycleText(bucket.cycle_id) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <dl class="p12-inline p12-num">
            <div><dt>共享储备余额</dt><dd :title="maybe(rules.funding.reserve).reason">{{ maybe(rules.funding.reserve).text }}</dd></div>
            <div><dt>待分摊迟到差额</dt><dd>{{ maybe(rules.funding.unallocated_adjustment).text }}</dd></div>
            <div><dt>当前资金规则修订</dt><dd>{{ rules.funding.funding_revision === null ? '初始绑定' : `版本 ${rules.funding.funding_revision}` }}</dd></div>
            <div><dt>政策标识</dt><dd><code>{{ rules.funding.policy_ref.ref_id }}@{{ rules.funding.policy_ref.version }}</code></dd></div>
          </dl>

          <div class="p12-mode-roadmap">
            <div>
              <strong>盈亏留桶模式</strong>
              <span class="status-tag control">待开发 · 未开放切换</span>
            </div>
            <p class="p12-hint" data-testid="p12-retained-mode">
              盈亏留桶模式：后续能力，当前版本未开放，不能切换。开放后关闭“清仓恢复初始桶额”将使单桶盈利或亏损留在原桶作为下一轮预算基数，待完成同口径收益回撤对比与版本化准入后启用。
            </p>
          </div>
        </section>

        <!-- Right: Current Risk Limits & Execution Profiles -->
        <section class="panel p12-section" data-testid="p12-risk">
          <div class="panel-head">
            <div>
              <h2>账户风险上限与执行口径</h2>
              <p>新买入前按成交后的预计整户权益检查，触发任一上限时限制新增买单并给出明确原因</p>
            </div>
            <span :class="['status-tag', rules.scope.mode === 'synthetic' ? 'locked' : 'accepted']">
              {{ rules.scope.mode === 'synthetic' ? '演示参数' : '已生效' }}
            </span>
          </div>
          <dl class="p12-plan p12-num">
            <div><dt>单只股票市值上限</dt><dd>{{ percent(rules.risk.stock_limit) }}（占账户总经济权益）</dd></div>
            <div><dt>同一行业市值上限</dt><dd>{{ percent(rules.risk.industry_limit) }}（占账户总经济权益）</dd></div>
            <div><dt>整户最高总仓位</dt><dd>{{ percent(rules.risk.total_limit) }}（保留至少 {{ reserveCashFloorText }} 现金缓冲）</dd></div>
            <div><dt>最小买入交易单位</dt><dd>{{ rules.risk.lot_size }} 股（1 手）</dd></div>
            <div><dt>日频策略佣金口径</dt><dd>{{ percent(rules.risk.commission_rate) }}，单笔最低 {{ money(rules.risk.minimum_commission) }}</dd></div>
            <div><dt>系统盘后计划口径</dt><dd><code>{{ rules.execution_profile }}</code>（T 收盘决策 → T+1 开盘执行）</dd></div>
            <div><dt>盘中自主模拟口径</dt><dd><code>p12-paper-exec-v1</code>（连续竞价实时快照限价触发，免佣测试）</dd></div>
            <div><dt>当前组合版本</dt><dd><code>{{ rules.composition_ref.ref_id }}@{{ rules.composition_ref.version }}</code></dd></div>
          </dl>
        </section>
      </div>

      <!-- Interactive Configuration & Impact Preview Sandbox (Scene 7) -->
      <section class="panel p12-section p12-config-panel" data-testid="p12-config-panel">
        <div class="panel-head">
          <div>
            <h2>个人可调整项与生效影响预览</h2>
            <p>修改下方参数可即时预览下一轮桶额、试仓上限及对当前持仓的处理方式；已有账本流水绝不回写覆盖</p>
          </div>
          <span :class="['status-tag', isDirty ? 'control' : 'locked']">
            {{ isDirty ? '有未保存的预览修改' : '当前与生效值一致' }}
          </span>
        </div>

        <div class="p12-config-grid">
          <div class="p12-config-inputs">
            <div class="p12-config-row">
              <label>
                <span>资金桶数量（个）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.bucketCount" inputmode="numeric" data-testid="p12-cfg-bucket-count" />
              </label>
              <label>
                <span>单桶目标额度（元）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.targetNotional" inputmode="decimal" data-testid="p12-cfg-target-notional" />
              </label>
              <label>
                <span>单桶试仓比例（%）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.trialPercent" inputmode="numeric" data-testid="p12-cfg-trial-pct" />
              </label>
            </div>
            <div class="p12-config-row">
              <label>
                <span>单股仓位上限（%）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.stockLimitPct" inputmode="decimal" data-testid="p12-cfg-stock-limit" />
              </label>
              <label>
                <span>同行业上限（%）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.industryLimitPct" inputmode="decimal" data-testid="p12-cfg-ind-limit" />
              </label>
              <label>
                <span>整户总仓位上限（%）<em class="p12-cfg-tag">可预览</em></span>
                <input v-model.trim="draft.totalLimitPct" inputmode="decimal" data-testid="p12-cfg-total-limit" />
              </label>
            </div>

            <div class="p12-config-readonly-row">
              <span>固定不变 / 待开发项：</span>
              <span>结算模式 = <b>固定桶额</b>（盈亏留桶待开发，不可切换）</span>
              <span>整手单位 = <b>{{ rules.risk.lot_size }} 股</b>（交易所规则固定）</span>
            </div>

            <p v-if="previewValidationError" class="p12-form-error" data-testid="p12-cfg-validation-error">{{ previewValidationError }}</p>

            <div class="p12-config-actions">
              <a-button
                type="primary"
                :disabled="Boolean(previewValidationError) || !isDirty"
                :loading="savingConfig"
                data-testid="p12-funding-save"
                @click="submitConfig"
              >确认保存并回读</a-button>
              <a-button
                :disabled="!isDirty && !saveStatus"
                data-testid="p12-funding-cancel"
                @click="cancelConfigChanges"
              >取消修改，恢复生效值</a-button>
            </div>

            <a-alert
              v-if="saveStatus"
              class="p12-cfg-alert"
              :type="saveStatus.type"
              show-icon
              :message="saveStatus.title"
              :description="saveStatus.detail"
              :data-testid="saveStatus.testid"
            />
          </div>

          <!-- Impact Preview Box -->
          <div class="p12-preview-box" data-testid="p12-funding-preview">
            <h3>变更生效预览与本轮持仓处理</h3>
            <template v-if="impactPreview">
              <dl class="p12-plan p12-num">
                <div>
                  <dt>原配置总额 → 预览总额</dt>
                  <dd>{{ money(currentTotalTarget) }} → <b>{{ money(impactPreview.newTotalTarget) }}</b>（{{ impactPreview.bucketCount }} 桶 × {{ money(impactPreview.targetMoney) }}）</dd>
                </div>
                <div>
                  <dt>预览单桶试仓上限</dt>
                  <dd><b>{{ money(impactPreview.newTrialCap) }}</b>（按 {{ impactPreview.trialPct }}% 测算）</dd>
                </div>
                <div>
                  <dt>预览风险上限</dt>
                  <dd>单股 {{ impactPreview.stockPct }}% · 同行业 {{ impactPreview.indPct }}% · 整户 {{ impactPreview.totalPct }}%</dd>
                </div>
              </dl>

              <div class="p12-effective-rules">
                <p>
                  <strong>何时生效：</strong>
                  <span v-if="emptyBucketNames.length">当前处于空闲状态的 <b>{{ emptyBucketNames.join('、') }}</b> 在配置生效后的<b>下一轮新机会预约时</b>立即按新额度 {{ money(impactPreview.targetMoney) }} 计算。</span>
                  <span v-else>当前没有空闲桶，新参数将在任一资金桶完成清仓结算与冷却后、下一轮启用前生效。</span>
                </p>
                <p>
                  <strong>本轮持仓怎么处理：</strong>
                  <span v-if="occupiedBucketNames.length">正在占用中的 <b>{{ occupiedBucketNames.join('、') }}</b> <b>沿原规则结算</b>（保持原锁定预算与版本 {{ rules.funding.policy_ref.version }}），系统绝不因改参数触发自动加仓或强制卖出。</span>
                  <span v-else>当前无在途持仓桶；后续一旦建仓，该轮持仓在完整清仓前始终锁定建仓时的规则版本，沿原规则结算。</span>
                </p>
                <p v-if="impactPreview.exceedsFundedCash" class="p12-deviation">
                  额度提示：预览总目标桶额（{{ money(impactPreview.newTotalTarget) }}）高于当前已配置桶总额（{{ money(currentTotalTarget) }}）；若账户可用储备不足补齐新增桶额，启用时将按实际可用净现金缩小当轮有效预算。
                </p>
              </div>
            </template>
            <p v-else class="p12-hint">请填写有效的正整数桶数、正金额与 1–100 之间的比例以生成预览。</p>
          </div>
        </div>
      </section>

      <!-- Rule Adoption Status Table (Human-readable missing fields) -->
      <section class="panel p12-section" data-testid="p12-adoption">
        <div class="panel-head">
          <div>
            <h2>系统策略与日常模拟准入进度</h2>
            <p>演练参考账户使用封闭合成配置；个人日常模拟需下表各项准入齐备后，系统才会自动生成每日盘后推荐</p>
          </div>
          <span :class="['status-tag', rules.action_blockers.length ? 'control' : 'accepted']">
            {{ rules.action_blockers.length ? '日常策略待准入（自主模拟可用）' : '全部规则已就绪' }}
          </span>
        </div>
        <div class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr>
                <th>编号</th>
                <th>能力模块</th>
                <th>影响的日常功能</th>
                <th>负责团队</th>
                <th v-for="mode in modes" :key="mode">{{ label('mode', mode) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in adoptionRows" :key="row.id">
                <td><b>{{ row.id }}</b></td>
                <td>{{ row.title }}</td>
                <td>{{ row.consumer }}</td>
                <td>{{ row.owner }}</td>
                <td v-for="mode in modes" :key="mode">
                  <template v-if="row.byMode[mode]">
                    <span :class="['status-tag', tone(row.byMode[mode].status)]">
                      {{ label('adoption_status', row.byMode[mode].status) }}
                    </span>
                    <small v-if="row.byMode[mode].missing_fields.length">
                      <br />待补：{{ row.byMode[mode].missing_fields.map(humanField).join('、') }}
                    </small>
                  </template>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
<script>
import accountMixin from '../accountMixin.js'
import { loadFundingRules, saveFundingConfig } from '../client.js'
import {
  centsToMoney,
  compareMoney,
  formatMaybe,
  formatMoney,
  humanizeBucketId,
  humanizeMissingField,
  label,
  moneyToCents,
  multiplyPriceQty,
  normaliseMoneyInput,
  ratioToPercent,
  reasonText,
  subtractMoney
} from '../format.js'

function ratioToCleanPctInput (ratioStr) {
  const pct = ratioToPercent(ratioStr)
  if (!pct || pct === '—') return ''
  return pct.replace('%', '').replace(/\.00$/, '')
}

export default {
  mixins: [accountMixin],
  data: () => ({
    rules: null,
    draft: {
      bucketCount: '',
      targetNotional: '',
      trialPercent: '30',
      stockLimitPct: '',
      industryLimitPct: '',
      totalLimitPct: ''
    },
    baselineDraft: null,
    savingConfig: false,
    saveStatus: null
  }),
  computed: {
    accountRouteQuery () {
      return this.accountId ? { account: this.accountId } : {}
    },
    accountModeBadge () {
      if (!this.rules) return ''
      return this.rules.scope.mode === 'synthetic'
        ? `演练参考账户 · ${this.rules.scope.account_id}（合成数据）`
        : `个人模拟盘 · ${this.rules.scope.account_id}`
    },
    modes () {
      return [...new Set((this.rules?.adoption || []).map(item => item.mode))]
    },
    adoptionRows () {
      const rows = new Map()
      for (const item of this.rules?.adoption || []) {
        const row = rows.get(item.binding_id) || { id: item.binding_id, title: item.title, consumer: item.consumer, owner: item.owner, byMode: {} }
        row.byMode[item.mode] = item
        rows.set(item.binding_id, row)
      }
      return [...rows.values()].sort((a, b) => a.id.localeCompare(b.id))
    },
    reserveCashFloorText () {
      const totalPct = ratioToPercent(this.rules?.risk?.total_limit)
      if (totalPct === '80.00%') return '20.00%'
      const diff = subtractMoney('100.00', totalPct.replace('%', ''))
      return diff ? `${diff}%` : '现金缓冲'
    },
    currentTotalTarget () {
      const buckets = this.rules?.funding?.buckets || []
      let sum = 0n
      for (const b of buckets) {
        const c = moneyToCents(b.target_notional)
        if (c !== null) sum += c
      }
      return centsToMoney(sum) || '0.00'
    },
    emptyBucketNames () {
      return (this.rules?.funding?.buckets || [])
        .filter(b => b.phase === 'empty' && !this.isOverspent(b))
        .map(b => humanizeBucketId(b.bucket_id))
    },
    occupiedBucketNames () {
      return (this.rules?.funding?.buckets || [])
        .filter(b => b.phase !== 'empty' || this.isOverspent(b))
        .map(b => humanizeBucketId(b.bucket_id))
    },
    isDirty () {
      if (!this.baselineDraft) return false
      return Object.keys(this.baselineDraft).some(k => String(this.draft[k] ?? '').trim() !== String(this.baselineDraft[k] ?? '').trim())
    },
    previewValidationError () {
      const count = /^\d+$/.test(this.draft.bucketCount) ? Number.parseInt(this.draft.bucketCount, 10) : null
      if (!count || count < 1 || count > 20) return '资金桶数量须为 1 至 20 之间的整数'
      const target = normaliseMoneyInput(this.draft.targetNotional)
      if (!target || target === '0.00') return '单桶目标额度须为大于 0 的金额（最多两位小数）'
      const trial = /^\d+$/.test(this.draft.trialPercent) ? Number.parseInt(this.draft.trialPercent, 10) : null
      if (!trial || trial < 10 || trial > 100) return '单桶试仓比例须为 10% 至 100% 之间的整数'
      for (const [labelName, val] of [
        ['单股仓位上限', this.draft.stockLimitPct],
        ['同行业上限', this.draft.industryLimitPct],
        ['整户总仓位上限', this.draft.totalLimitPct]
      ]) {
        const norm = normaliseMoneyInput(val)
        const cents = norm ? moneyToCents(norm) : null
        if (cents === null || cents <= 0n || cents > 10000n) {
          return `${labelName}须在 1% 至 100% 之间`
        }
      }
      return ''
    },
    impactPreview () {
      if (this.previewValidationError) return null
      const bucketCount = Number.parseInt(this.draft.bucketCount, 10)
      const targetMoney = normaliseMoneyInput(this.draft.targetNotional)
      const trialPct = Number.parseInt(this.draft.trialPercent, 10)
      const newTotalTarget = multiplyPriceQty(targetMoney, bucketCount)
      const targetCents = moneyToCents(targetMoney)
      const newTrialCap = centsToMoney((targetCents * BigInt(trialPct)) / 100n)
      const exceedsFundedCash = compareMoney(newTotalTarget, this.currentTotalTarget) === 1
      return {
        bucketCount,
        targetMoney,
        trialPct,
        newTotalTarget,
        newTrialCap,
        exceedsFundedCash,
        stockPct: normaliseMoneyInput(this.draft.stockLimitPct),
        indPct: normaliseMoneyInput(this.draft.industryLimitPct),
        totalPct: normaliseMoneyInput(this.draft.totalLimitPct)
      }
    }
  },
  methods: {
    async load (accountId) {
      this.rules = await loadFundingRules(accountId)
      this.initDraftFromRules()
    },
    initDraftFromRules () {
      if (!this.rules) return
      const buckets = this.rules.funding.buckets || []
      const firstBucket = buckets[0]
      const target = firstBucket ? firstBucket.target_notional : '5000.00'
      let trialPercent = '30'
      if (firstBucket?.trial_notional_cap?.value && firstBucket?.target_notional) {
        const capC = moneyToCents(firstBucket.trial_notional_cap.value)
        const tgtC = moneyToCents(firstBucket.target_notional)
        if (capC !== null && tgtC && tgtC > 0n) {
          trialPercent = ((capC * 100n) / tgtC).toString()
        }
      }
      const initial = {
        bucketCount: String(buckets.length || 2),
        targetNotional: target,
        trialPercent,
        stockLimitPct: ratioToCleanPctInput(this.rules.risk.stock_limit),
        industryLimitPct: ratioToCleanPctInput(this.rules.risk.industry_limit),
        totalLimitPct: ratioToCleanPctInput(this.rules.risk.total_limit)
      }
      this.draft = { ...initial }
      this.baselineDraft = { ...initial }
      this.saveStatus = null
    },
    cancelConfigChanges () {
      if (this.baselineDraft) {
        this.draft = { ...this.baselineDraft }
      }
      this.saveStatus = {
        type: 'info',
        title: '已取消未保存的修改',
        detail: '上方参数已恢复为当前账户正在生效的规则值，账户版本与在途持仓没有任何变动。',
        testid: 'p12-funding-cancelled-notice'
      }
    },
    async submitConfig () {
      if (this.previewValidationError || !this.impactPreview) return
      this.savingConfig = true
      this.saveStatus = null
      try {
        await saveFundingConfig(this.accountId, {
          unit_count: this.impactPreview.bucketCount,
          bucket_budget: this.impactPreview.targetMoney,
          trial_fraction_pct: this.impactPreview.trialPct,
          stock_limit_pct: this.impactPreview.stockPct,
          industry_limit_pct: this.impactPreview.indPct,
          total_limit_pct: this.impactPreview.totalPct
        })
        await this.load(this.accountId)
        this.saveStatus = {
          type: 'success',
          title: '资金与风险参数已保存并完成回读确认',
          detail: `已从服务端重新读取最新规则（修订版本 ${this.rules.funding.funding_revision ?? '最新'}）；空闲桶将在下一轮采用新额度，当前占用桶继续沿原规则结算。`,
          testid: 'p12-funding-saved-notice'
        }
      } catch (error) {
        this.saveStatus = {
          type: 'warning',
          title: '未能写入生效：当前服务尚未开放个人资金参数持久化接口',
          detail: `${reasonText(error.reasonCode)}。当前账户仍保持原生效规则（${this.baselineDraft.bucketCount} 桶 × ${formatMoney(this.baselineDraft.targetNotional)}），不会用“保存成功”提示冒充入账；你可以点击“取消修改”恢复原值，或保留上方预览供核对。`,
          testid: 'p12-funding-save-blocked'
        }
      } finally {
        this.savingConfig = false
      }
    },
    tone (status) { return status === 'adopted' || status === 'configured' ? 'accepted' : 'control' },
    maybe: value => formatMaybe(value),
    money: formatMoney,
    percent: ratioToPercent,
    reason: reasonText,
    bucketName: humanizeBucketId,
    humanField: humanizeMissingField,
    isOverspent (bucket) {
      return bucket.cash.value === null && bucket.cash.reason_code === 'INSUFFICIENT_CASH'
    },
    formatBucketCell (cell, bucket) {
      if (!cell) return '—'
      if (cell.value !== null) return formatMoney(cell.value)
      if (this.isOverspent(bucket)) return '超额待核对'
      if (cell.reason_code === 'FUNDING_CYCLE_NOT_BOUND') return '待绑定实际成交'
      return formatMaybe(cell).text
    },
    formatUnderfundedCell (bucket) {
      const cell = bucket.underfunded
      if (!cell) return '¥0.00'
      if (cell.value !== null) return formatMoney(cell.value)
      if (cell.reason_code === 'FUNDING_CYCLE_NOT_BOUND') return '¥0.00（未发生）'
      return formatMaybe(cell).text
    },
    formatSecondaryBucketCell (cell) {
      if (!cell) return '—'
      if (cell.value !== null) return formatMoney(cell.value)
      if (cell.reason_code === 'FUNDING_CYCLE_NOT_BOUND') return '随成交周期计算'
      return formatMaybe(cell).text
    },
    formatCycleText (cycleId) {
      if (!cycleId) return '当前空闲'
      return cycleId
    },
    bucketStatus (bucket) {
      if (this.isOverspent(bucket)) return '欠资已入账'
      if (bucket.cash.value === null) return '未接入实际成交'
      if (bucket.phase === 'empty') return '空闲可用'
      if (bucket.phase === 'reserved') return '已预约计划'
      if (bucket.phase === 'active' || bucket.phase === 'holding') return '持仓运行中'
      if (bucket.phase === 'cooldown') return '清仓冷却中'
      return bucket.phase
    },
    bucketCashHint (bucket) {
      if (this.isOverspent(bucket)) {
        const gap = bucket.underfunded?.value ? formatMoney(bucket.underfunded.value) : '待确认金额'
        return `该笔成交已记入账户账本，超出单桶目标额度 ${formatMoney(bucket.target_notional)} 的部分记为桶预算的欠资（${gap}）`
      }
      return formatMaybe(bucket.cash).reason
    },
    label
  }
}
</script>
