<template>
  <div class="page-wrap p12-page" data-testid="p12-records">
    <div class="p12-today-bar">
      <div class="p12-today-main">
        <div class="p12-today-title-row">
          <h1>委托与成交记录</h1>
          <span
            v-if="records"
            :class="['p12-badge', isRehearsal ? 'synthetic' : 'simulation']"
          >{{ accountModeBadge }}</span>
        </div>
        <p class="p12-today-conclusion">
          按股票、日期或委托单号找回原计划、模拟委托、成交事实与更正链；任何更正均以追加差额入账，原记录永久保留。
        </p>
      </div>
      <div class="heading-actions">
        <router-link :to="{ path: '/p12', query: backWorkbenchQuery }" class="p12-nav-pill" data-testid="p12-back-workbench">
          ← 返回工作台{{ filterKeyword ? `（${filterKeyword}）` : '' }}
        </router-link>
        <router-link :to="{ path: '/p12/funding', query: accountRouteQuery }" class="p12-nav-pill">
          资金与规则 →
        </router-link>
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

    <template v-if="records">
      <!-- Filter Bar: Stock / Date / Order / Source -->
      <section class="panel p12-filter-bar" data-testid="p12-records-filter">
        <div class="p12-filter-controls">
          <label class="p12-filter-field">
            <span>按股票代码 / 简称 / 委托单号查找</span>
            <input
              v-model.trim="filterKeyword"
              placeholder="输入如 600000、浦发银行、ORD-1"
              data-testid="p12-filter-keyword"
            />
          </label>
          <label class="p12-filter-field">
            <span>按日期筛选</span>
            <input
              v-model="filterDate"
              type="date"
              data-testid="p12-filter-date"
            />
          </label>
          <label class="p12-filter-field">
            <span>记录来源</span>
            <select v-model="filterSource" class="p12-select" data-testid="p12-filter-source">
              <option value="all">全部来源</option>
              <option value="sim_matched">模拟撮合委托</option>
              <option value="user_reported">我自主登记的成交</option>
              <option value="system_plan">系统盘后原计划</option>
            </select>
          </label>
          <div class="p12-filter-reset">
            <a-button v-if="hasActiveFilter" size="small" data-testid="p12-filter-clear" @click="clearFilters">清空筛选</a-button>
          </div>
        </div>

        <div v-if="knownSecurityChips.length || knownDateChips.length" class="p12-filter-chips">
          <template v-if="knownSecurityChips.length">
            <span class="p12-chip-label">涉及证券：</span>
            <button
              v-for="chip in knownSecurityChips"
              :key="'sec-' + chip.code"
              type="button"
              :class="['p12-shortcut-chip', { active: filterKeyword === chip.code }]"
              :data-testid="`p12-chip-sec-${chip.code}`"
              @click="filterKeyword = filterKeyword === chip.code ? '' : chip.code"
            >{{ chip.text }}</button>
          </template>
          <template v-if="knownDateChips.length">
            <span class="p12-chip-label">涉及日期：</span>
            <button
              v-for="d in knownDateChips"
              :key="'dt-' + d"
              type="button"
              :class="['p12-shortcut-chip', { active: filterDate === d }]"
              @click="filterDate = filterDate === d ? '' : d"
            >{{ d }}</button>
          </template>
        </div>

        <div class="p12-filter-summary p12-num">
          <span>当前筛选结果：原计划 <b>{{ filteredPlans.length }}</b> 条 · 模拟委托 <b>{{ filteredSimOrders.length }}</b> 笔 · 有效成交事实 <b>{{ filteredFacts.length }}</b> 笔 · 原始报告 <b>{{ filteredReports.length }}</b> 份 · 账本流水 <b>{{ filteredEvents.length }}</b> 条</span>
          <span>当前模拟现金 <b data-testid="p12-records-cash">{{ money(records.account.cash_total) }}</b></span>
        </div>
      </section>

      <!-- Section 1: Recorded Fills & Append-Only Correction Chain (Primary user job on Records page) -->
      <section v-if="filterSource === 'all' || filterSource === 'user_reported' || filterSource === 'sim_matched'" class="panel p12-section" data-testid="p12-records-facts">
        <div class="panel-head">
          <div>
            <h2>成交事实与更正链</h2>
            <p>当前有效持仓与成本来自更正链末端；发起更正不会覆盖原记录，系统自动追加差额流水</p>
          </div>
          <span class="status-tag accepted">只追加 · 不覆盖原值</span>
        </div>
        <p v-if="!filteredFacts.length" class="p12-empty">
          {{ hasActiveFilter ? '当前筛选条件下没有匹配的成交事实。' : '当前账户还没有已入账的成交事实。' }}
        </p>
        <div v-for="fact in filteredFacts" :key="fact.fact_id" class="p12-fact">
          <div class="p12-fact-head">
            <div class="p12-fact-main">
              <span class="p12-sec-inline">
                <b>{{ formatSecurityLabel(fact.security_code) }}</b>
                <span class="p12-mini-tag neutral">{{ factSourceLabel(fact) }}</span>
              </span>
              <span class="p12-num p12-fact-figure">当前有效：<b>{{ qty(fact.quantity) }}</b> × <b>¥{{ fact.price }}</b></span>
              <small>修订版本 {{ fact.fact_revision }} · 记录号 {{ fact.fact_id }}</small>
            </div>
            <div class="p12-fact-actions">
              <a-button size="small" :data-testid="`p12-correct-${fact.fact_id}`" @click="openCorrection(fact)">
                追加更正
              </a-button>
            </div>
          </div>
          <ol v-if="fact.corrections.length" class="p12-chain-list">
            <li v-for="item in fact.corrections" :key="item.correction_id">
              {{ time(item.received_at) }} · 基于版本 {{ item.expected_fact_revision }} 追加更正为 <b>{{ qty(item.quantity) }} @ ¥{{ item.price }}</b> · 原因：{{ item.reason }} ·
              <span :class="['status-tag', item.status === 'POSTED' ? 'accepted' : 'control']">{{ label('receipt_status', item.status) }}</span>
              <span v-if="item.error_code">（{{ reason(item.error_code) }}）</span>
            </li>
          </ol>
          <form v-if="editing && editing.fact_id === fact.fact_id" class="p12-form compact" data-testid="p12-correction-form" @submit.prevent="submitCorrection">
            <div class="p12-correction-notice">
              原记录（版本 {{ editing.revision }}：{{ qty(editing.origQuantity) }} @ ¥{{ editing.origPrice }}）将完整保留；提交后仅追加新版本并入账现金与股数差额。
            </div>
            <label>更正后股数<input v-model.trim="editing.quantity" inputmode="numeric" data-testid="p12-correct-quantity" /></label>
            <label>更正后成交价（元）<input v-model.trim="editing.price" inputmode="decimal" data-testid="p12-correct-price" /></label>
            <label class="wide">更正原因（必填）<input v-model.trim="editing.reason" maxlength="200" placeholder="例如：核对交割单，实际成交价为 12.25" data-testid="p12-correct-reason" /></label>
            <p v-if="editError" class="p12-form-error">{{ editError }}</p>
            <div class="p12-form-actions">
              <a-button type="primary" html-type="submit" :loading="saving" data-testid="p12-correct-submit">提交追加更正</a-button>
              <a-button @click="editing = null">取消</a-button>
            </div>
          </form>
        </div>
        <div v-if="correctionOutcome" class="p12-outcome" data-testid="p12-correction-outcome">
          <b>{{ label('receipt_status', correctionOutcome.status) }}</b> · 现金差额 {{ signed(correctionOutcome.cash_delta) }} · 股数差额 {{ correctionOutcome.quantity_delta }} 股 · 更新后模拟现金 {{ money(correctionOutcome.account.cash_total) }}
        </div>
      </section>

      <!-- Section 2: Simulated Orders (sim-orders contract) -->
      <section v-if="(filterSource === 'all' || filterSource === 'sim_matched') && (simOrdersAvailable || filteredSimOrders.length)" class="panel p12-section" data-testid="p12-records-sim-orders">
        <div class="panel-head">
          <div>
            <h2>模拟盘委托记录（挂单、撮合与撤单）</h2>
            <p>区分盘中模拟撮合委托与个人自报成交；未成交余量可直接在此撤单解冻资金</p>
          </div>
          <span class="status-tag locked">规则 p12-paper-exec-v1</span>
        </div>
        <p v-if="!filteredSimOrders.length" class="p12-empty">暂无匹配的模拟盘委托记录。</p>
        <div v-else class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr>
                <th>委托单号</th>
                <th>证券</th>
                <th>方向与限价</th>
                <th>委托 / 已成 / 待成余量</th>
                <th>成交均价</th>
                <th>来源与时间</th>
                <th>状态与说明</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ord in filteredSimOrders" :key="ord.order_id">
                <td><code>{{ ord.order_id }}</code></td>
                <td>{{ formatSecurityLabel(ord.security_code, ord.security_name) }}</td>
                <td class="p12-num">{{ ord.side === 'sell' ? '卖出' : '买入' }} @ ¥{{ ord.limit_price }}</td>
                <td class="p12-num">{{ qty(ord.quantity) }} / {{ qty(ord.filled_qty || 0) }} / {{ qty(activeRemaining(ord)) }}</td>
                <td class="p12-num">{{ ord.avg_price ? `¥${ord.avg_price}` : '—' }}</td>
                <td>
                  <span class="p12-mini-tag neutral">{{ simOrigin(ord) }}</span>
                  <br /><small>{{ time(ord.accepted_at || ord.occurred_at || ord.created_at || ord.last_quote_time || ord.matched_quote_time) }}</small>
                </td>
                <td>
                  <span :class="['status-tag', ord.status === 'filled' ? 'accepted' : ((ord.status === 'rejected' || ord.status === 'expired') ? 'control' : 'locked')]">
                    {{ label('sim_order_status', ord.status) }}
                  </span>
                  <small v-if="ord.reject_reason"><br />拒绝原因：{{ reason(ord.reject_reason) }}</small>
                </td>
                <td>
                  <a-button
                    v-if="ord.status === 'open' || ord.status === 'partially_filled'"
                    size="small"
                    :loading="cancellingId === ord.order_id"
                    @click="cancelOrder(ord.order_id)"
                  >撤单</a-button>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Section 3: Historical + Latest Original Plans (Combines package + prior_packages so Day 1 plans never vanish!) -->
      <section v-if="filterSource === 'all' || filterSource === 'system_plan'" class="panel p12-section" data-testid="p12-records-plans">
        <div v-if="records.package" class="panel-head">
          <div>
            <h2>原计划（含当日与历史决策包）</h2>
            <p v-if="isEmptyPaperPackage">个人模拟账户当前无已准入的系统盘后推荐批次；后续签发的计划与历史批次将一并留档，发布后永不改写</p>
            <p v-else>最新批次 {{ records.package.market_date }} 收盘发布；历史批次一并保留，发布后永不改写</p>
          </div>
          <span class="status-tag locked">共 {{ allPackagesCount }} 个批次</span>
        </div>
        <div v-else class="panel-head">
          <div>
            <h2>原计划</h2>
            <p>{{ records.publication.market_date }} 的盘后计划将于 {{ time(records.publication.published_at) }} 发布</p>
          </div>
        </div>
        <p v-if="!filteredPlans.length" class="p12-empty">
          {{ hasActiveFilter ? '当前筛选条件下没有匹配的原计划记录。' : '当前及历史决策包均无生成的买卖计划（例如账户无对应持仓或未触发买点）。' }}
        </p>
        <div v-else class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr>
                <th>批次日期</th>
                <th>证券</th>
                <th>方向与目标股数</th>
                <th>价格上限</th>
                <th>有效执行窗口</th>
                <th>计划状态</th>
                <th>计划编号</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="plan in filteredPlans"
                :key="plan.package_id + ':' + plan.plan_id"
                :class="{ 'p12-row-highlight': highlightedPlanId === plan.plan_id }"
              >
                <td>
                  <b>{{ plan.market_date }}</b>
                  <br /><small>{{ plan.isLatest ? '最新批次' : '历史批次' }}</small>
                </td>
                <td>{{ formatSecurityLabel(plan.security_code) }}</td>
                <td class="p12-num">{{ plan.side === 'sell' ? '卖出至' : '买入目标' }} {{ qty(plan.target_qty) }}（差额 {{ plan.delta_qty > 0 ? '+' : '' }}{{ plan.delta_qty }} 股）</td>
                <td class="p12-num">¥{{ plan.price_cap }}</td>
                <td class="p12-num">{{ time(plan.eligible_at) }} – {{ time(plan.expires_at).slice(11) }}</td>
                <td>{{ planStatusText(plan) }}</td>
                <td><code>{{ plan.plan_id }}</code></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Section 4: Original Execution Reports -->
      <section v-if="filterSource === 'all' || filterSource === 'user_reported'" class="panel p12-section" data-testid="p12-records-reports">
        <div class="panel-head">
          <div>
            <h2>原报告（提交留底与偏离对照）</h2>
            <p>每份提交保留原始填写值与幂等回执；偏离原计划上限或股数时如实标注</p>
          </div>
        </div>
        <p v-if="!filteredReports.length" class="p12-empty">
          {{ hasActiveFilter ? '当前筛选条件下没有匹配的原报告。' : '还没有登记过成交报告。' }}
        </p>
        <div v-else class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr>
                <th>证券</th>
                <th>登记成交与费用</th>
                <th>对照原计划</th>
                <th>偏离说明</th>
                <th>入账状态</th>
                <th>接收时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredReports" :key="item.report_id">
                <td>
                  <b>{{ formatSecurityLabel(item.security_code) }}</b>
                  <br /><small><code>{{ item.report_id }}</code></small>
                </td>
                <td class="p12-num">
                  {{ item.side === 'sell' ? '卖出' : '买入' }} {{ qty(item.quantity) }} @ ¥{{ item.price }} · 费用 {{ money(item.confirmed_fee) }}
                  <br /><small>成交于 {{ time(item.occurred_at) }}</small>
                </td>
                <td class="p12-num">
                  {{ qty(item.intended_quantity) }} ≤ ¥{{ item.intended_price_cap }}
                  <button
                    v-if="item.plan_id"
                    type="button"
                    class="p12-inline-btn"
                    @click="highlightedPlanId = item.plan_id"
                  >定位原计划</button>
                </td>
                <td>{{ item.deviations.length ? item.deviations.map(reason).join('；') : '符合原计划边界' }}</td>
                <td>
                  <span :class="['status-tag', item.status === 'POSTED' ? 'accepted' : 'control']">{{ label('receipt_status', item.status) }}</span>
                  <small v-if="item.conflicts.length" data-testid="p12-report-conflicts"><br />{{ item.conflicts.map(reason).join('；') }}</small>
                </td>
                <td class="p12-num">{{ time(item.received_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Section 5: Multi-Fill Order Records -->
      <section v-if="filteredOrders.length || filteredObservations.length || filteredFees.length" class="panel p12-section" data-testid="p12-records-orders">
        <div class="panel-head">
          <div>
            <h2>分笔委托与费用核对明细</h2>
            <p>同一委托单的分笔入账、终结观察与整单费用确认；撤单后未报齐的成交继续保留占用</p>
          </div>
        </div>
        <div v-if="filteredOrders.length" class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr><th>委托单号</th><th>方向与证券</th><th>委托总量</th><th>已入账</th><th>待补报</th><th>当前占用</th><th>已确认费用</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="order in filteredOrders" :key="order.order_id" :data-testid="`p12-order-${order.order_id}`">
                <td><code>{{ order.order_id }}</code></td>
                <td>{{ label('order_side', order.side) }} {{ formatSecurityLabel(order.security_code) }}</td>
                <td class="p12-num">{{ qty(order.accepted_total_qty) }}</td>
                <td class="p12-num">{{ qty(order.known_fill_qty) }}</td>
                <td class="p12-num">{{ qty(order.unreported_fill_qty) }}</td>
                <td class="p12-num">{{ money(order.hold_amount) }}（{{ qty(order.hold_qty) }}）</td>
                <td class="p12-num">{{ money(order.fee_confirmed_total) }}</td>
                <td>
                  <span :class="['status-tag', order.needs_reconciliation ? 'control' : (order.status === 'terminal' ? 'accepted' : 'locked')]">
                    {{ label('order_status', order.status) }}
                  </span>
                  <small v-if="order.reason_codes.length"><br />{{ order.reason_codes.map(reason).join('；') }}</small>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="filteredObservations.length" class="p12-table-wrap">
          <table class="p12-table">
            <thead><tr><th>状态记录号</th><th>委托单号</th><th>委托状态</th><th>终结累计成交</th><th>取消股数</th><th>记录时间</th></tr></thead>
            <tbody>
              <tr v-for="obs in filteredObservations" :key="obs.observation_id">
                <td><code>{{ obs.observation_id }}</code></td>
                <td><code>{{ obs.order_id }}</code></td>
                <td>{{ label('order_status', obs.status) }}</td>
                <td class="p12-num">{{ obs.final_cumulative_fill_qty === null ? '未报' : qty(obs.final_cumulative_fill_qty) }}</td>
                <td class="p12-num">{{ obs.cancelled_qty === null ? '—' : qty(obs.cancelled_qty) }}</td>
                <td class="p12-num">{{ time(obs.observed_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="filteredFees.length" class="p12-table-wrap">
          <table class="p12-table">
            <thead><tr><th>费用记录号</th><th>关联委托单</th><th>确认金额</th><th>发生时间</th></tr></thead>
            <tbody>
              <tr v-for="fee in filteredFees" :key="fee.fee_id">
                <td><code>{{ fee.fee_id }}</code></td>
                <td><code>{{ fee.order_id }}</code></td>
                <td class="p12-num">{{ money(fee.amount) }}</td>
                <td class="p12-num">{{ time(fee.occurred_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Section 6: Balanced Ledger Events -->
      <section class="panel p12-section" data-testid="p12-records-events">
        <div class="panel-head">
          <div>
            <h2>账本流水</h2>
            <p>按入账时间先后排列，每一笔现金与持仓成本变动均保持双向平衡</p>
          </div>
          <span class="status-tag locked">共 {{ filteredEvents.length }} 条分录</span>
        </div>
        <p v-if="!filteredEvents.length" class="p12-empty">暂无匹配的账本分录。</p>
        <div v-else class="p12-table-wrap">
          <table class="p12-table">
            <thead>
              <tr><th>序号</th><th>变动类型</th><th>关联记录</th><th>现金变动</th><th>持仓成本变动</th><th>股数变动</th><th>记录时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="event in filteredEvents" :key="event.event_id">
                <td class="p12-num">{{ event.event_sequence }}</td>
                <td>{{ label('event_kind', event.kind) }}</td>
                <td><code>{{ event.fact_id }}</code></td>
                <td class="p12-num">{{ signed(event.cash_delta) }}</td>
                <td class="p12-num">{{ signed(event.position_delta) }}</td>
                <td class="p12-num">{{ event.quantity_delta > 0 ? '+' : '' }}{{ event.quantity_delta }} 股</td>
                <td class="p12-num">{{ time(event.recorded_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Section 7: Quiet Baseline & Audit Footer -->
      <section class="panel p12-section p12-baseline-card">
        <div class="panel-head">
          <div>
            <h2>账户起点与账本核对摘要</h2>
            <p>建立于 {{ time(records.baseline.as_of) }} · 初始资金 {{ money(records.baseline.cash) }}</p>
          </div>
          <span class="status-tag locked">账户修订 {{ records.account.account_revision }}</span>
        </div>
        <dl class="p12-inline p12-num">
          <div><dt>初始模拟现金</dt><dd>{{ money(records.baseline.cash) }}</dd></div>
          <div><dt>当前模拟现金</dt><dd>{{ money(records.account.cash_total) }}</dd></div>
          <div><dt>覆盖范围</dt><dd>{{ baselineCoverageLabel }}</dd></div>
          <div><dt>账本一致性摘要</dt><dd><code>{{ records.account.ledger_digest.slice(0, 19) }}…</code></dd></div>
        </dl>
      </section>
    </template>
  </div>
</template>
<script>
import accountMixin from '../accountMixin.js'
import { cancelSimOrder, correctFill, listSimOrders, loadMarketSession, loadRecords, loadSecurityIdentity, newClientId } from '../client.js'
import {
  formatMoney,
  formatQuantity,
  formatSignedMoney,
  formatTime,
  inferExchangeLabel,
  isRehearsalAccount,
  isTimestampPast,
  label,
  normaliseMoneyInput,
  normalizeSecurityName,
  reasonText,
  simOrderActiveRemaining,
  simOrderOriginLabel
} from '../format.js'

export default {
  mixins: [accountMixin],
  data: () => ({
    records: null,
    marketSession: null,
    simOrders: [],
    simOrdersAvailable: false,
    securityNames: {},
    filterKeyword: '',
    filterDate: '',
    filterSource: 'all',
    highlightedPlanId: null,
    cancellingId: null,
    editing: null,
    editError: '',
    saving: false,
    correctionOutcome: null
  }),
  computed: {
    accountRouteQuery () {
      return this.accountId ? { account: this.accountId } : {}
    },
    backWorkbenchQuery () {
      const q = { ...this.accountRouteQuery }
      if (/^\d{6}$/.test(this.filterKeyword)) q.security = this.filterKeyword
      return q
    },
    accountModeBadge () {
      if (!this.records) return ''
      if (this.isRehearsal) {
        return `演练参考账户 · ${this.records.scope.account_id}（封闭合成数据）`
      }
      const display = this.currentAccount?.display_name
      return display && display !== this.records.scope.account_id
        ? `个人模拟盘 · ${display}（${this.records.scope.account_id}）`
        : `个人模拟盘 · ${this.records.scope.account_id}`
    },
    isRehearsal () {
      return isRehearsalAccount(
        this.currentAccount,
        this.records?.scope,
        '',
        this.records?.package?.source_ref?.ref_id || ''
      )
    },
    isEmptyPaperPackage () {
      return this.records?.package?.source_ref?.ref_id === 'p12-paper-decisions' && !this.allPlanRows.length
    },
    effectiveNowIso () {
      if (!this.isRehearsal && this.marketSession?.server_time) return this.marketSession.server_time
      return this.records?.account?.as_of || ''
    },
    baselineCoverageLabel () {
      const cov = this.records?.baseline?.coverage_id
      if (cov === 'complete-cash-only') return '纯现金初始建账（无历史遗留持仓）'
      return cov || '—'
    },
    hasActiveFilter () {
      return Boolean(this.filterKeyword || this.filterDate || this.filterSource !== 'all')
    },
    allPackages () {
      if (!this.records) return []
      const map = new Map()
      if (this.records.package) {
        map.set(this.records.package.package_id, { pkg: this.records.package, isLatest: true })
      }
      for (const p of (this.records.prior_packages || [])) {
        if (!map.has(p.package_id)) map.set(p.package_id, { pkg: p, isLatest: false })
      }
      return [...map.values()].sort((a, b) => String(b.pkg.market_date).localeCompare(String(a.pkg.market_date)))
    },
    allPackagesCount () {
      return this.allPackages.length
    },
    allPlanRows () {
      const rows = []
      for (const { pkg, isLatest } of this.allPackages) {
        for (const plan of (pkg.plans || [])) {
          rows.push({
            ...plan,
            package_id: pkg.package_id,
            market_date: pkg.market_date,
            published_at: pkg.published_at,
            isLatest
          })
        }
      }
      return rows
    },
    knownSecurityChips () {
      const codes = new Set()
      for (const p of this.allPlanRows) if (p.security_code) codes.add(p.security_code)
      for (const r of (this.records?.reports || [])) if (r.security_code) codes.add(r.security_code)
      for (const f of (this.records?.facts || [])) if (f.security_code) codes.add(f.security_code)
      for (const o of (this.records?.orders || [])) if (o.security_code) codes.add(o.security_code)
      for (const s of this.simOrders) if (s.security_code) codes.add(s.security_code)
      return [...codes].map(code => ({ code, text: this.formatSecurityLabel(code) }))
    },
    knownDateChips () {
      const dates = new Set()
      for (const { pkg } of this.allPackages) if (pkg.market_date) dates.add(pkg.market_date)
      for (const r of (this.records?.reports || [])) if (r.occurred_at) dates.add(r.occurred_at.slice(0, 10))
      for (const e of (this.records?.events || [])) if (e.recorded_at) dates.add(e.recorded_at.slice(0, 10))
      for (const s of this.simOrders) {
        const dt = String(s.accepted_at || s.occurred_at || s.created_at || s.last_quote_time || s.matched_quote_time || '').slice(0, 10)
        if (dt) dates.add(dt)
      }
      return [...dates].sort((a, b) => b.localeCompare(a))
    },
    filteredPlans () {
      return this.allPlanRows.filter(plan => {
        if (!this.matchesKeyword([plan.security_code, this.securityNames[plan.security_code], plan.plan_id, plan.opportunity_id])) return false
        if (this.filterDate && plan.market_date !== this.filterDate && !String(plan.eligible_at || '').startsWith(this.filterDate)) return false
        return true
      })
    },
    filteredReports () {
      return (this.records?.reports || []).filter(r => {
        if (!this.matchesKeyword([r.security_code, this.securityNames[r.security_code], r.report_id, r.order_id, r.plan_id])) return false
        if (this.filterDate && !String(r.occurred_at || '').startsWith(this.filterDate) && !String(r.received_at || '').startsWith(this.filterDate)) return false
        return true
      })
    },
    filteredFacts () {
      return (this.records?.facts || []).filter(f => {
        if (this.filterSource === 'sim_matched' && this.factSourceKey(f) !== 'sim_matched') return false
        if (this.filterSource === 'user_reported' && this.factSourceKey(f) !== 'user_reported') return false
        if (!this.matchesKeyword([f.security_code, this.securityNames[f.security_code], f.fact_id])) return false
        if (this.filterDate) {
          const matchedReport = (this.records?.reports || []).find(r => r.report_id === f.fact_id)
          const matchedEvent = (this.records?.events || []).find(e => e.fact_id === f.fact_id)
          const dateStr = (matchedReport?.occurred_at || matchedEvent?.recorded_at || '').slice(0, 10)
          if (dateStr && dateStr !== this.filterDate) return false
        }
        return true
      })
    },
    filteredSimOrders () {
      return this.simOrders.filter(o => {
        if (!this.matchesKeyword([o.security_code, o.security_name, this.securityNames[o.security_code], o.order_id])) return false
        if (this.filterDate) {
          const dt = String(o.accepted_at || o.occurred_at || o.created_at || o.last_quote_time || o.matched_quote_time || '').slice(0, 10)
          if (dt && dt !== this.filterDate) return false
        }
        return true
      })
    },
    filteredOrders () {
      return (this.records?.orders || []).filter(o => {
        if (!this.matchesKeyword([o.security_code, this.securityNames[o.security_code], o.order_id])) return false
        return true
      })
    },
    filteredObservations () {
      const orderIds = new Set(this.filteredOrders.map(o => o.order_id))
      return (this.records?.observations || []).filter(obs => {
        if (this.filterKeyword && !orderIds.has(obs.order_id) && !this.matchesKeyword([obs.observation_id, obs.order_id])) return false
        if (this.filterDate && !String(obs.observed_at || '').startsWith(this.filterDate)) return false
        return true
      })
    },
    filteredFees () {
      const orderIds = new Set(this.filteredOrders.map(o => o.order_id))
      return (this.records?.fees || []).filter(fee => {
        if (this.filterKeyword && !orderIds.has(fee.order_id) && !this.matchesKeyword([fee.fee_id, fee.order_id])) return false
        if (this.filterDate && !String(fee.occurred_at || '').startsWith(this.filterDate)) return false
        return true
      })
    },
    filteredEvents () {
      const factIds = new Set([
        ...this.filteredFacts.map(f => f.fact_id),
        ...this.filteredReports.map(r => r.report_id),
        ...this.filteredOrders.map(o => o.order_id),
        ...this.filteredSimOrders.map(o => o.order_id)
      ])
      return (this.records?.events || []).filter(ev => {
        if (this.filterKeyword && !factIds.has(ev.fact_id) && !this.matchesKeyword([ev.fact_id, ev.event_id])) return false
        if (this.filterDate && !String(ev.recorded_at || '').startsWith(this.filterDate)) return false
        return true
      })
    }
  },
  created () {
    if (this.$route.query.security) this.filterKeyword = String(this.$route.query.security)
    if (this.$route.query.order) this.filterKeyword = String(this.$route.query.order)
    if (this.$route.query.date) this.filterDate = String(this.$route.query.date)
  },
  methods: {
    async load (accountId) {
      const [rec, simRes, sessionRes] = await Promise.all([
        loadRecords(accountId),
        listSimOrders(accountId),
        loadMarketSession()
      ])
      this.records = rec
      this.simOrdersAvailable = simRes.available
      this.simOrders = simRes.orders
      this.marketSession = sessionRes.available ? sessionRes.data : null
      await this.resolveRealSecurityNames()
    },
    async resolveRealSecurityNames () {
      const synFixtureCodes = new Set(['600000', '000002', '300001'])
      const codes = new Set()
      for (const p of this.allPlanRows) if (p.security_code) codes.add(p.security_code)
      for (const f of (this.records?.facts || [])) if (f.security_code) codes.add(f.security_code)
      for (const r of (this.records?.reports || [])) if (r.security_code) codes.add(r.security_code)
      for (const o of (this.records?.orders || [])) if (o.security_code) codes.add(o.security_code)
      for (const s of this.simOrders) if (s.security_code) codes.add(s.security_code)
      await Promise.all([...codes].map(async code => {
        if (this.isRehearsal && synFixtureCodes.has(code)) return
        const res = await loadSecurityIdentity(code)
        if (res.available && res.data && res.data.security_name) {
          this.$set(this.securityNames, code, normalizeSecurityName(res.data.security_name))
        }
      }))
    },
    matchesKeyword (fields) {
      const kw = String(this.filterKeyword || '').trim().toLowerCase()
      if (!kw) return true
      return fields.some(val => val && String(val).toLowerCase().includes(kw))
    },
    clearFilters () {
      this.filterKeyword = ''
      this.filterDate = ''
      this.filterSource = 'all'
      this.highlightedPlanId = null
    },
    formatSecurityLabel (code, explicitName) {
      if (!code) return '—'
      const cleanExplicit = normalizeSecurityName(explicitName || '')
      if (cleanExplicit && cleanExplicit !== code && !cleanExplicit.startsWith('合成')) {
        const ex = inferExchangeLabel(code, '', false)
        return `${ex} · ${code} ${cleanExplicit}`
      }
      if (this.isRehearsal) {
        const synMap = { '600000': '合成证券甲', '000002': '合成证券乙', '300001': '合成证券丙' }
        if (synMap[code]) return `${synMap[code]}（演练示例代号 ${code}）`
      }
      const name = normalizeSecurityName(cleanExplicit !== code ? cleanExplicit : (this.securityNames[code] || '')) || normalizeSecurityName(this.securityNames[code] || '')
      const ex = inferExchangeLabel(code, '', false)
      return name ? `${ex} · ${code} ${name}` : `${ex} · ${code}`
    },
    factSourceKey (fact) {
      const id = String(fact.fact_id || '')
      if (id.startsWith('sim') || this.simOrders.some(o => o.order_id === id)) return 'sim_matched'
      return 'user_reported'
    },
    factSourceLabel (fact) {
      return label('record_source', this.factSourceKey(fact))
    },
    planStatus (planId) {
      return this.records.plans.find(item => item.plan_id === planId)?.status || null
    },
    planStatusText (plan) {
      const liveStatus = this.planStatus(plan.plan_id)
      if ((!liveStatus || liveStatus === 'READY') && plan.expires_at && isTimestampPast(this.effectiveNowIso, plan.expires_at)) {
        return '窗口已过'
      }
      if (liveStatus) return label('plan_status', liveStatus)
      return plan.isLatest ? '有效' : '历史已归档'
    },
    async cancelOrder (orderId) {
      this.cancellingId = orderId
      try {
        await cancelSimOrder(this.accountId, orderId)
        await this.load(this.accountId)
      } catch (_) {
        this.$message.error('撤单未成功，请刷新核对订单状态')
      } finally {
        this.cancellingId = null
      }
    },
    openCorrection (fact) {
      this.editing = {
        fact_id: fact.fact_id,
        revision: fact.fact_revision,
        origQuantity: fact.quantity,
        origPrice: fact.price,
        quantity: String(fact.quantity),
        price: fact.price,
        reason: '',
        correctionId: newClientId('corr')
      }
      this.editError = ''
      this.correctionOutcome = null
    },
    async submitCorrection () {
      const quantity = /^\d+$/.test(this.editing.quantity) ? Number.parseInt(this.editing.quantity, 10) : null
      const price = normaliseMoneyInput(this.editing.price)
      if (!quantity) { this.editError = '更正后股数需为正整数'; return }
      if (!price || price === '0.00') { this.editError = '更正后成交价需为最多两位小数的正数'; return }
      if (!this.editing.reason) { this.editError = '请写明更正原因，例如“核对交割单，实际成交为 300 股”'; return }
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
        this.editError = reasonText(error.reasonCode) || '更正提交失败'
      } finally {
        this.saving = false
      }
    },
    money: formatMoney,
    signed: formatSignedMoney,
    qty: formatQuantity,
    time: formatTime,
    reason: reasonText,
    simOrigin: simOrderOriginLabel,
    activeRemaining: simOrderActiveRemaining,
    label
  }
}
</script>
