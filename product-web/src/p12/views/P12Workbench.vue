<template>
  <div class="page-wrap p12-page" data-testid="p12-workbench">
    <!-- Top Today Conclusion & Session Bar -->
    <div class="p12-today-bar" data-testid="p12-today-bar">
      <div class="p12-today-main">
        <div class="p12-today-title-row">
          <h1>模拟盘工作台</h1>
          <span
            v-if="workbench"
            :class="['p12-badge', workbench.scope.mode === 'synthetic' ? 'synthetic' : 'simulation']"
            data-testid="p12-mode"
          >{{ accountModeBadge }}</span>
          <span v-if="marketSessionBadge" :class="['p12-session-pill', marketSessionTone]" data-testid="p12-market-session">
            {{ marketSessionBadge }}
          </span>
        </div>
        <p class="p12-today-conclusion" data-testid="p12-today-conclusion">{{ todayConclusion }}</p>
        <p v-if="workbench && workbench.package" class="p12-today-sub">
          盘后计划基准日 {{ workbench.package.market_date }}（收盘判断 → 下一交易日开盘执行） · 发布于 {{ time(workbench.package.published_at) }}
        </p>
        <p v-else-if="workbench" class="p12-today-sub">
          {{ workbench.publication.market_date }} 盘后策略计划尚未发布（预计 {{ time(workbench.publication.published_at) }}）；实时行情查询、自主模拟委托与账户核对正常可用
        </p>
      </div>
      <div class="heading-actions">
        <select
          v-if="accounts.length > 1"
          class="p12-select"
          :value="accountId"
          aria-label="选择模拟账户"
          @change="selectAccount($event.target.value)"
        >
          <option v-for="item in accounts" :key="item.account_id" :value="item.account_id">
            {{ item.display_name }}（{{ item.synthetic ? '演练合成' : '个人模拟' }}）
          </option>
        </select>
        <span v-if="workbench" class="as-of" data-testid="p12-clock">
          <a-icon type="clock-circle" />{{ clockDisplay }}
        </span>
        <a-button icon="reload" :loading="loading" @click="refreshAll">刷新</a-button>
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

    <template v-if="workbench">
      <AccountSummary
        :account="workbench.account"
        :buckets="fundingBuckets"
        :account-id="accountId || ''"
        @select-bucket="onSelectBucket"
      />

      <!-- Honest notice when daily strategy adoption is gated -->
      <div v-if="workbench.action_blockers.length" class="p12-banner-note warning" data-testid="p12-strategy-gate-banner">
        <div>
          <strong>今天没有可采用的系统新增买入推荐（策略与日常数据准入待完成）</strong>
          <p>待完成项：{{ blockerSummary }}。在此期间，历史信号与行情浏览不冒充系统推荐；你仍可查询真实股票、提交自主模拟委托或登记已有成交。</p>
        </div>
        <router-link :to="{ path: '/p12/funding', query: accountRouteQuery }" class="p12-inline-link">查看准入详情 →</router-link>
      </div>

      <section v-if="!workbench.package && !customWatchItems.length && !extraHoldingItems.length" class="panel p12-unpublished" data-testid="p12-unpublished">
        <div class="p12-unpublished-inner">
          <div>
            <h2>{{ label('publication_status', workbench.publication.status) }}</h2>
            <p>{{ workbench.publication.market_date }} 收盘后的系统计划将于 {{ time(workbench.publication.published_at) }} 发布。发布前系统不展示未定稿的策略推荐；你可以直接查询真实 A 股行情并进行自主模拟操作。</p>
          </div>
          <form class="p12-lookup-inline" @submit.prevent="lookupSecurity()">
            <input
              v-model.trim="lookupCode"
              maxlength="6"
              inputmode="numeric"
              placeholder="输入 6 位证券代码，如 600000"
              aria-label="输入 6 位证券代码"
            />
            <a-button type="primary" html-type="submit">查询真实股票</a-button>
          </form>
        </div>
      </section>

      <div v-else class="p12-grid">
        <!-- Left Column: Real Stock Lookup + Priority Queue -->
        <aside class="panel p12-queue" aria-label="待办和机会队列" data-testid="p12-queue">
          <div class="p12-lookup-box">
            <form class="p12-lookup-form" @submit.prevent="lookupSecurity()">
              <input
                v-model.trim="lookupCode"
                maxlength="6"
                inputmode="numeric"
                placeholder="输入 6 位代码看行情/模拟下单"
                aria-label="查询 6 位股票代码"
                data-testid="p12-stock-search"
              />
              <button type="submit" class="p12-lookup-btn" data-testid="p12-stock-search-btn">看盘</button>
            </form>
            <div class="p12-shortcut-row">
              <span>实盘代码速查：</span>
              <button
                v-for="sc in realShortcuts"
                :key="sc.code"
                type="button"
                class="p12-shortcut-chip"
                :data-testid="`p12-shortcut-${sc.code}`"
                @click="lookupSecurity(sc.code)"
              >{{ sc.code }} {{ sc.hintName }}</button>
            </div>
            <p v-if="lookupError" class="p12-form-error">{{ lookupError }}</p>
          </div>

          <div class="panel-head p12-queue-head">
            <div>
              <h2>待办、持仓与观察</h2>
              <p>按紧急程度排序；支持键盘 ↑/↓ 或 J/K 切换</p>
            </div>
          </div>

          <div v-if="!groups.length" class="p12-empty">
            今天暂无系统候选标的；请在上方输入 6 位证券代码进行自主看盘与模拟委托。
          </div>

          <div v-for="group in groups" :key="group.key" class="p12-queue-group">
            <h3>
              <span>{{ groupTitle(group.key) }}</span>
              <small class="p12-num">{{ group.items.length }}</small>
            </h3>
            <button
              v-for="item in group.items"
              :key="item.opportunity_id"
              type="button"
              :class="['p12-queue-item', { active: item.opportunity_id === selectedId }]"
              :data-testid="`p12-queue-${item.opportunity_id}`"
              @click="selectOpportunity(item.opportunity_id)"
            >
              <div class="p12-queue-row-top">
                <span class="name">
                  <b>{{ displaySecurityName(item) }}</b>
                  <small>{{ displaySecurityBadge(item) }}</small>
                </span>
                <span :class="['p12-queue-action', actionTone(item)]">{{ queueActionLabel(item) }}</span>
              </div>
              <p class="p12-queue-brief">{{ queueBrief(item) }}</p>
              <div class="p12-queue-tags">
                <span :class="['status-tag', queueStatusTone(item)]">{{ queueStatusLabel(item) }}</span>
                <span v-if="item.selfDirected" class="p12-mini-tag neutral">自主看盘 · 非系统建议</span>
                <span v-else-if="isItemSynthetic(item)" class="p12-mini-tag amber">演练数据</span>
              </div>
            </button>
          </div>
        </aside>

        <!-- Center Column: Security Identity, Live Quote, Intraday/Daily Chart & Plain-Chinese Takeaways -->
        <section v-if="selected" class="panel p12-detail" data-testid="p12-detail">
          <!-- Security Identity Header -->
          <div class="p12-sec-header" data-testid="p12-security-header">
            <div class="p12-sec-title-block">
              <div class="p12-sec-title-line">
                <h2>{{ selectedDisplayName }}</h2>
                <span class="p12-sec-code p12-num">{{ selected.security_code }}</span>
                <span :class="['p12-exchange-tag', { synthetic: isCurrentSynthetic }]">{{ selectedExchangeLabel }}</span>
                <span :class="['status-tag', queueStatusTone(selected)]">{{ queueStatusLabel(selected) }}</span>
                <span v-if="selected.selfDirected" class="p12-source-pill self">自主看盘 · 非系统建议</span>
                <span v-else-if="isCurrentSynthetic" class="p12-source-pill syn">演练合成用例 · 非真实股价</span>
                <span v-else class="p12-source-pill sys">系统盘后计划标的</span>
              </div>
              <p v-if="isCurrentSynthetic" class="p12-syn-honesty">
                说明：当前展示的是固定演练账户标的「{{ selected.security_name }}」（示例代号 {{ selected.security_code }}），其 K 线与计划仅用于账本演练，不代表真实证券行情或今日推荐。
                <button type="button" class="p12-inline-btn" @click="lookupSecurity(selected.security_code)">
                  单独查看真实 {{ selected.security_code }} 盘中行情 →
                </button>
              </p>
              <p v-else class="p12-sec-sub">
                <template v-if="selected.selfDirected">由你主动查询的真实 A 股标的；所有委托均记为个人自主模拟操作，不代表策略推荐。</template>
                <template v-else>{{ label('stage', selected.stage) }} · 跟踪编号 {{ selected.opportunity_id }}</template>
              </p>
            </div>

            <!-- Real-time Quote Snapshot Strip (only for non-synthetic real stocks) -->
            <div v-if="!isCurrentSynthetic" class="p12-quote-strip" data-testid="p12-live-quote">
              <template v-if="quoteLoading && !liveQuote">
                <div class="p12-quote-unavailable">正在读取实时报价快照…</div>
              </template>
              <template v-else-if="liveQuote && liveQuote.last_price !== null">
                <div :class="['p12-quote-main', quoteChange ? quoteChange.direction : 'flat']">
                  <strong class="p12-quote-price p12-num">¥{{ liveQuote.last_price }}</strong>
                  <span v-if="quoteChange" class="p12-quote-delta p12-num">
                    {{ quoteChange.diffText }}（{{ quoteChange.pctText }}）
                  </span>
                  <span :class="['p12-quote-state', liveQuote.status === 'live' ? 'live' : 'muted']">
                    {{ label('quote_status', liveQuote.status) }} · {{ label('market_state', liveQuote.market_state) }}
                  </span>
                </div>
                <div class="p12-quote-ohlc p12-num">
                  <span>今开 <b>{{ liveQuote.open || '—' }}</b></span>
                  <span>最高 <b>{{ liveQuote.high || '—' }}</b></span>
                  <span>最低 <b>{{ liveQuote.low || '—' }}</b></span>
                  <span>昨收 <b>{{ liveQuote.previous_close || '—' }}</b></span>
                  <span v-if="liveQuote.volume_lots">成交 <b>{{ liveQuote.volume_lots }} 手</b></span>
                </div>
                <div class="p12-quote-provenance">
                  <span>快照来源：{{ label('quote_source', liveQuote.source) }}</span>
                  <span>源行情时间：{{ liveQuote.quote_time ? timeWithSec(liveQuote.quote_time) : '休市/无源时点' }}</span>
                  <span>接收时间：{{ liveQuote.received_at ? timeWithSec(liveQuote.received_at) : '—' }}</span>
                  <span class="p12-quote-caveat">仅供看盘与模拟限价触发参考，显示报价不等于已成交</span>
                </div>
              </template>
              <template v-else>
                <div class="p12-quote-unavailable" data-testid="p12-quote-unavailable">
                  <strong>{{ quoteUnavailableTitle }}</strong>
                  <span>{{ quoteUnavailableDetail }}</span>
                </div>
              </template>
            </div>
          </div>

          <!-- Per-Stock Account Position, Sellable T+1 & Cross-Page Links -->
          <div class="p12-stock-context" data-testid="p12-stock-position-bar">
            <div class="p12-stock-metrics p12-num">
              <span>本股持仓 <b>{{ qty(selectedPositionQty) }}</b></span>
              <span>今日可卖(T+1) <b>{{ qty(selectedSellableQty) }}</b></span>
              <span>持仓成本 <b>{{ selectedAvgCostText }}</b></span>
              <span v-if="selectedOpenOrderCount > 0" class="p12-active-hold">进行中委托 <b>{{ selectedOpenOrderCount }} 笔</b></span>
            </div>
            <div class="p12-stock-links">
              <router-link :to="{ path: '/p12/records', query: { ...accountRouteQuery, security: selected.security_code } }" class="p12-inline-link" data-testid="p12-jump-records">
                查本股委托与成交记录 →
              </router-link>
              <router-link :to="{ path: '/p12/funding', query: accountRouteQuery }" class="p12-inline-link">
                查资金桶与规则 →
              </router-link>
            </div>
          </div>

          <!-- Dual Intraday / Historical Daily Chart -->
          <PriceChart
            :security-key="selected.opportunity_id"
            :chart="selected.chart"
            :intraday="isCurrentSynthetic ? null : currentIntraday"
            :intraday-state="intradayState"
            :intraday-loading="quoteLoading"
            :prev-close="liveQuote ? liveQuote.previous_close : null"
            :synthetic="isCurrentSynthetic"
            :name="selectedDisplayName"
            :price-cap="selected.plan ? selected.plan.price_cap : (simForm.limit_price || null)"
            :cost-price="selectedCostPriceValue"
            :has-live-snapshot="Boolean(!isCurrentSynthetic && liveQuote && liveQuote.last_price)"
            :empty-text="selected.status_code === 'MISSING_INPUT' ? '行情数据未更新，暂不显示价格图' : '暂无已准入的历史日 K 线数据'"
          />

          <!-- Plain-Chinese Three Takeaway Cards -->
          <div class="p12-triple">
            <div class="p12-card" data-testid="p12-action">
              <span>建议动作</span>
              <b>{{ takeawayActionTitle }}</b>
              <p>{{ actionSentence }}</p>
            </div>
            <div class="p12-card" data-testid="p12-changes">
              <span>今天的变化</span>
              <ul v-if="selected.changes && selected.changes.length">
                <li v-for="change in selected.changes" :key="change">{{ label('change', change) }}</li>
              </ul>
              <p v-else>{{ defaultChangeText }}</p>
            </div>
            <div class="p12-card" data-testid="p12-constraints">
              <span>主要约束</span>
              <b>{{ primaryConstraintTitle }}</b>
              <p v-if="otherReasons.length">同时：{{ otherReasons.map(reason).join('；') }}</p>
              <p v-else-if="selected.selfDirected">自主模拟委托遵循 100 股整手、可用现金上限及 A 股 T+1 可卖约束。</p>
              <p v-if="endedReasons.length" class="p12-history">历史原因：{{ endedReasons.map(reason).join('；') }}</p>
            </div>
          </div>

          <!-- Collapsible Strategy Evidence Chain -->
          <div v-if="!selected.selfDirected" class="p12-chain">
            <button type="button" class="p12-link" @click="chainOpen = !chainOpen">
              {{ chainOpen ? '收起完整分析链' : '展开完整分析链（策略与风险依据）' }}
            </button>
            <label class="p12-pin"><input v-model="chainPinned" type="checkbox" /> 固定展开</label>
            <div v-if="chainOpen || chainPinned" class="p12-chain-body" data-testid="p12-chain">
              <div v-for="item in selected.explanations" :key="item.code" class="p12-chain-row">
                <span :class="['p12-use', item.use]">{{ label('explanation_use', item.use) }}</span>
                <p>{{ item.text }}</p>
                <code>来源组件 {{ item.component_ref }}</code>
              </div>
              <dl v-if="workbench.package" class="p12-audit-meta">
                <div><dt>发布批次</dt><dd><code>{{ workbench.package.package_id }}</code></dd></div>
                <div><dt>策略组合</dt><dd><code>{{ workbench.package.composition_ref.ref_id }}@{{ workbench.package.composition_ref.version }}</code></dd></div>
                <div v-if="selected.plan"><dt>风控核验</dt><dd><code>{{ selected.plan.risk_check_ref.slice(0, 19) }}…</code></dd></div>
                <div><dt>信号来源</dt><dd><code>{{ workbench.package.source_ref.ref_id }}@{{ workbench.package.source_ref.version }}</code></dd></div>
              </dl>
            </div>
          </div>
        </section>

        <!-- Right Column: Stock-Bound Action Ticket (Simulated Order + Execution Fact Check) -->
        <aside v-if="selected" class="panel p12-task" data-testid="p12-task">
          <div class="panel-head p12-task-head">
            <div>
              <h2>{{ taskTitle }}</h2>
              <p>{{ taskSubtitle }}</p>
            </div>
            <span class="p12-sec-chip p12-num">{{ selected.security_code }}</span>
          </div>

          <!-- Mode Switcher: Simulated Order vs Fact Reporting -->
          <div v-if="!selected.selfDirected" class="p12-action-tabs" role="tablist">
            <button
              type="button"
              :class="['p12-action-tab', { active: activeTicketTab === 'sim' }]"
              data-testid="p12-tab-sim-order"
              @click="ticketTab = 'sim'"
            >模拟委托下单</button>
            <button
              type="button"
              :class="['p12-action-tab', { active: activeTicketTab === 'report' }]"
              data-testid="p12-tab-report-fill"
              @click="ticketTab = 'report'"
            >计划与成交核对</button>
          </div>

          <!-- TAB A: Self-Directed / Paper Simulated Limit Order -->
          <div v-show="activeTicketTab === 'sim'" class="p12-sim-pane" data-testid="p12-sim-pane">
            <div class="p12-sim-origin-bar">
              <span class="p12-origin-tag">自主模拟委托 · 非系统推荐</span>
              <small>规则 {{ paperRuleVersion }} · 不发送实盘指令</small>
            </div>

            <form class="p12-form p12-sim-form" data-testid="p12-sim-order-form" @submit.prevent="submitPaperOrder">
              <div class="p12-side-switch" role="group" aria-label="买卖方向">
                <button
                  type="button"
                  :class="['p12-side-btn buy', { active: simForm.side === 'buy' }]"
                  data-testid="p12-sim-side-buy"
                  @click="simForm.side = 'buy'"
                >买入 {{ selected.security_code }}</button>
                <button
                  type="button"
                  :class="['p12-side-btn sell', { active: simForm.side === 'sell' }]"
                  data-testid="p12-sim-side-sell"
                  @click="simForm.side = 'sell'"
                >卖出 {{ selected.security_code }}</button>
              </div>

              <label>
                <span>限价（元）<small v-if="liveQuote && liveQuote.last_price">最新价 ¥{{ liveQuote.last_price }}</small></span>
                <input
                  v-model.trim="simForm.limit_price"
                  inputmode="decimal"
                  placeholder="输入委托限价，如 10.65"
                  data-testid="p12-sim-price"
                />
              </label>

              <label>
                <span>委托数量（股，100 股整数倍）</span>
                <input
                  v-model.trim="simForm.quantity"
                  inputmode="numeric"
                  placeholder="如 100、200、400"
                  data-testid="p12-sim-quantity"
                />
              </label>

              <div class="p12-quick-qty">
                <button type="button" @click="simForm.quantity = '100'">100 股</button>
                <button type="button" @click="simForm.quantity = '200'">200 股</button>
                <button type="button" @click="simForm.quantity = '400'">400 股</button>
                <button v-if="simForm.side === 'sell' && selectedSellableQty > 0" type="button" @click="simForm.quantity = String(selectedSellableQty)">全部可卖</button>
              </div>

              <dl class="p12-precheck p12-num">
                <div><dt>预计委托金额</dt><dd>{{ simEstimatedAmount ? money(simEstimatedAmount) : '—' }}</dd></div>
                <div v-if="simForm.side === 'buy'">
                  <dt>可用模拟现金</dt>
                  <dd>{{ formatMaybe(workbench.account.cash_available_for_new).text }}</dd>
                </div>
                <div v-else>
                  <dt>今日可卖 / 总持仓</dt>
                  <dd>{{ qty(selectedSellableQty) }} / {{ qty(selectedPositionQty) }}</dd>
                </div>
                <div><dt>模拟费用口径</dt><dd>测试免佣（0.00）</dd></div>
              </dl>

              <p v-if="simPrecheckHint" class="p12-precheck-warn" data-testid="p12-sim-precheck">{{ simPrecheckHint }}</p>
              <p v-if="simError" class="p12-form-error" data-testid="p12-sim-error">{{ simError }}</p>

              <a-button type="primary" html-type="submit" :loading="simSubmitting" data-testid="p12-sim-submit">
                {{ simForm.side === 'buy' ? '提交模拟买入委托' : '提交模拟卖出委托' }}
              </a-button>
              <small class="p12-hint">
                挂单后可离开页面；服务端在连续竞价时段收到满足限价的新快照时自动撮合，休市或停牌不偷偷成交。
              </small>
            </form>

            <!-- Simulated Order Outcome Receipt -->
            <div v-if="simOutcome" :class="['p12-outcome', simOutcome.status === 'rejected' ? 'rejected' : '']" data-testid="p12-sim-outcome">
              <h3>{{ simOutcomeTitle }}</h3>
              <p v-if="simOutcome.reason_code || simOutcome.reason_text" class="p12-deviation">
                原因：{{ simOutcome.reason_text || reason(simOutcome.reason_code) }}
              </p>
              <dl class="p12-num">
                <div><dt>委托状态</dt><dd>{{ label('sim_order_status', simOutcome.status) }}</dd></div>
                <div><dt>已成交数量</dt><dd>{{ qty(simOutcome.filled_qty || 0) }}</dd></div>
                <div v-if="simOutcome.avg_price"><dt>成交均价</dt><dd>¥{{ simOutcome.avg_price }}</dd></div>
                <div v-if="simOutcome.matched_quote_time"><dt>触发行情时间</dt><dd>{{ timeWithSec(simOutcome.matched_quote_time) }}</dd></div>
              </dl>
            </div>

            <!-- Active & Recent Simulated Orders List -->
            <div class="p12-sim-orders-box" data-testid="p12-sim-orders-box">
              <div class="p12-sim-orders-head">
                <strong>模拟委托单（{{ displayedSimOrders.length }}）</strong>
                <label v-if="simOrders.length > selectedStockSimOrders.length" class="p12-pin">
                  <input v-model="showAllSimOrders" type="checkbox" /> 显示全账户
                </label>
              </div>
              <p v-if="!simOrdersAvailable" class="p12-hint">
                当前运行环境尚未启用盘中模拟委托接口；如需登记外部已成交事实，请切到「计划与成交核对」。
              </p>
              <p v-else-if="!displayedSimOrders.length" class="p12-hint">
                当前股票暂无模拟挂单或当日委托记录。
              </p>
              <ul v-else class="p12-sim-order-list">
                <li v-for="ord in displayedSimOrders" :key="ord.order_id" :data-testid="`p12-sim-ord-${ord.order_id}`">
                  <div class="p12-sim-ord-top">
                    <span>
                      <b>{{ ord.side === 'sell' ? '卖出' : '买入' }} {{ ord.security_code }}</b>
                      <small class="p12-num">{{ qty(ord.quantity) }} @ ¥{{ ord.limit_price }}</small>
                    </span>
                    <span :class="['status-tag', simOrderTone(ord.status)]">{{ label('sim_order_status', ord.status) }}</span>
                  </div>
                  <div class="p12-sim-ord-meta p12-num">
                    <span>已成 {{ qty(ord.filled_qty || 0) }}<template v-if="ord.remaining_qty"> · 剩余 {{ qty(ord.remaining_qty) }}</template></span>
                    <span>{{ label('record_source', ord.source || 'sim_matched') }}</span>
                  </div>
                  <p v-if="ord.reject_reason" class="p12-sim-ord-reason">拒绝原因：{{ reason(ord.reject_reason) }}</p>
                  <div v-if="ord.status === 'open' || ord.status === 'partially_filled'" class="p12-sim-ord-actions">
                    <a-button
                      size="small"
                      :loading="cancellingOrderId === ord.order_id"
                      :data-testid="`p12-sim-cancel-${ord.order_id}`"
                      @click="cancelPaperOrder(ord.order_id)"
                    >撤回未成交余量</a-button>
                  </div>
                </li>
              </ul>
            </div>
          </div>

          <!-- TAB B: System Plan Details & Manual Execution Fact Reporting -->
          <div v-show="activeTicketTab === 'report'" class="p12-report-pane">
            <template v-if="selected.plan && selected.plan.side === 'buy'">
              <dl class="p12-plan p12-num" data-testid="p12-plan">
                <div><dt>当前持有</dt><dd>{{ qty(selected.applicability.current_qty) }}</dd></div>
                <div><dt>计划目标</dt><dd>{{ qty(selected.plan.target_qty) }}</dd></div>
                <div><dt>还差</dt><dd data-testid="p12-delta">{{ qty(selected.applicability.delta_qty) }}</dd></div>
                <div><dt>有效窗口</dt><dd>{{ time(selected.plan.eligible_at) }} 至 {{ time(selected.plan.expires_at).slice(11) }}</dd></div>
                <div><dt>价格上限</dt><dd>{{ selected.plan.price_cap }}</dd></div>
                <div><dt>资金上限</dt><dd>{{ money(selected.plan.cash_bound) }}（含预估费用 {{ money(selected.plan.estimated_fee) }}）</dd></div>
              </dl>
              <div class="p12-invalid">
                <span>失效条件</span>
                <ul><li v-for="item in selected.plan.invalidations" :key="item">{{ item }}</li></ul>
              </div>
              <p class="p12-next">{{ nextStepReadable }}</p>

              <form v-if="selected.plan.delta_qty > 0 && !outcome" class="p12-form" data-testid="p12-fill-form" @submit.prevent="submit">
                <h3>核对实际执行（我自主登记的成交）</h3>
                <p class="p12-hint">登记已实际成交的结果；价格或股数与计划不一致也照实填写，账本会保留原计划并标注偏离。</p>
                <label>成交数量（股）<input v-model.trim="form.quantity" inputmode="numeric" data-testid="p12-fill-quantity" /></label>
                <label>成交价（元）<input v-model.trim="form.price" inputmode="decimal" data-testid="p12-fill-price" /></label>
                <label>确认费用（元）<input v-model.trim="form.fee" inputmode="decimal" data-testid="p12-fill-fee" /></label>
                <label>成交时间<input v-model="form.time" type="datetime-local" data-testid="p12-fill-time" /></label>
                <p v-if="formError" class="p12-form-error" data-testid="p12-form-error">{{ formError }}</p>
                <a-button type="primary" html-type="submit" :loading="submitting" data-testid="p12-fill-submit">保存成交</a-button>
                <small class="p12-hint">重复点击不会重复入账。</small>
              </form>
            </template>

            <template v-else-if="selected.stage === 'exit_plan' && !isUnheldExit(selected)">
              <dl class="p12-plan p12-num" data-testid="p12-sell-plan">
                <div><dt>当前持有</dt><dd>{{ qty(sellTask ? sellTask.held : 0) }}</dd></div>
                <div><dt>卖出数量</dt><dd>{{ sellTask ? qty(sellTask.total) : '—' }}</dd></div>
                <div><dt>已成交</dt><dd data-testid="p12-sell-filled">{{ sellTask ? qty(sellTask.filled) : '0 股' }}</dd></div>
                <div><dt>剩余待成交</dt><dd data-testid="p12-sell-remaining">{{ sellTask ? qty(sellTask.remaining) : '—' }}</dd></div>
                <div v-if="!(sellTask && sellTask.settled)"><dt>执行窗口</dt><dd>{{ sellWindowText }}</dd></div>
              </dl>
              <p class="p12-next" data-testid="p12-sell-next">{{ sellNext }}</p>
              <div v-if="sellTask && !sellTask.settled" class="p12-form p12-sell-entry" data-testid="p12-sell-entry">
                <template v-if="sellTask.awaitingConfirmation">
                  <h3>确认卖出收尾</h3>
                  <p class="p12-hint">
                    {{ sellConfirmHint }}
                  </p>
                  <div class="p12-sell-confirm">
                    <a-button v-if="!sellTask.feeConfirmed" size="small" type="primary" data-testid="p12-open-fee-form" @click="prefillConfirm('fee')">确认整单费用（已预填）</a-button>
                    <a-button v-if="!sellTask.allTerminal" size="small" data-testid="p12-open-term-form" @click="prefillConfirm('terminal')">终结委托（已预填）</a-button>
                  </div>
                </template>
                <template v-else>
                  <h3>登记实际卖出成交</h3>
                  <p class="p12-hint">同一委托可分笔成交；登记每一笔后确认整单费用并终结委托，结算资金才会归回储备。</p>
                  <a-button size="small" type="primary" data-testid="p12-open-sell-form" @click="prefillSell">登记卖出成交（已预填）</a-button>
                </template>
              </div>
            </template>

            <template v-else-if="selected.plan">
              <dl class="p12-plan p12-num" data-testid="p12-plan">
                <div><dt>当前持有</dt><dd>{{ qty(selected.applicability.current_qty) }}</dd></div>
                <div><dt>计划目标</dt><dd>{{ qty(selected.plan.target_qty) }}</dd></div>
                <div><dt>还差</dt><dd data-testid="p12-delta">{{ qty(selected.applicability.delta_qty) }}</dd></div>
                <div><dt>有效窗口</dt><dd>{{ time(selected.plan.eligible_at) }} 至 {{ time(selected.plan.expires_at).slice(11) }}</dd></div>
                <div><dt>价格上限</dt><dd>{{ selected.plan.price_cap }}</dd></div>
              </dl>
              <div class="p12-invalid">
                <span>失效条件</span>
                <ul><li v-for="item in selected.plan.invalidations" :key="item">{{ item }}</li></ul>
              </div>
              <p class="p12-next">{{ nextStepReadable }}</p>
            </template>

            <div v-else class="p12-noop" data-testid="p12-noop-card">
              <b>{{ isUnheldExit(selected) ? '无需操作（未持有该股）' : label('action_code', selected.action_code) }}</b>
              <p v-if="isUnheldExit(selected)" data-testid="p12-unheld-exit-note">
                本账户此前未买入 {{ selectedDisplayName }}（当前持仓 0 股），本轮退出计划对你的账户不产生卖出任务或待办费用。
              </p>
              <p v-else-if="selected.stage === 'exit_plan' && selected.status_code === 'ENDED'" data-testid="p12-ended-note">{{ actionSentence }}</p>
              <p v-else-if="selected.stage === 'exit_plan'" data-testid="p12-sell-note">{{ actionSentence }}</p>
              <p v-else>{{ selected.next_observation || '今天无需执行操作，继续按条件观察' }}</p>
              <small v-if="workbench.package">最近盘后检查：{{ workbench.package.market_date }} 收盘</small>
            </div>

            <div v-if="outcome" class="p12-outcome" data-testid="p12-outcome">
              <h3>{{ label('receipt_status', outcome.status) }}</h3>
              <dl class="p12-num">
                <div><dt>模拟现金</dt><dd data-testid="p12-outcome-cash">{{ money(outcomeBefore) }} → {{ money(outcome.account.cash_total) }}</dd></div>
                <div><dt>当前持仓</dt><dd data-testid="p12-outcome-position">{{ positionText(outcome.account) }}</dd></div>
                <div><dt>费用状态</dt><dd>{{ outcome.status === 'POSTED' ? `已确认 ${money(lastFee)}` : `已登记 ${money(lastFee)}，待核对后入账` }}</dd></div>
              </dl>
              <p v-if="outcome.deviations.length" class="p12-deviation" data-testid="p12-outcome-deviations">偏离说明：{{ outcome.deviations.map(reason).join('；') }}</p>
              <p v-if="outcome.conflicts.length" class="p12-deviation">待核对事项：{{ outcome.conflicts.map(reason).join('；') }}</p>
              <p v-if="outcome.affected_plans.length" class="p12-hint">账户现金已更新，其余未执行计划将在下一轮按新余额重算。</p>
              <details class="p12-tech-details">
                <summary>查看核对凭据</summary>
                <code>{{ outcome.receipt_id }} · 账户修订 {{ outcome.account.account_revision }}</code>
              </details>
              <a-button size="small" @click="resetForm">继续登记下一笔</a-button>
            </div>

            <!-- Stock-bound Multi-Fill / Order Status / Fee Drawer inside Right Column -->
            <div class="p12-order-drawer" data-testid="p12-order-panel">
              <button type="button" class="p12-drawer-toggle" @click="orderDrawerOpen = !orderDrawerOpen">
                <span>分笔成交、撤单观察与整单费用登记（{{ selected.security_code }}）</span>
                <a-icon :type="orderDrawerOpen ? 'up' : 'down'" />
              </button>
              <div v-show="orderDrawerOpen" class="p12-order-drawer-body">
                <form class="p12-form p12-order-compact" data-testid="p12-order-form" @submit.prevent="submitOrder">
                  <label>委托单号<input v-model.trim="orderForm.order_id" data-testid="p12-order-id" placeholder="如 ORD-1" /></label>
                  <label>登记事项
                    <select v-model="orderForm.kind" class="p12-select" data-testid="p12-order-kind">
                      <option value="fill">登记一笔成交</option>
                      <option value="observation">更新委托状态（进行中/撤单/终结）</option>
                      <option value="fee">确认整单总费用</option>
                    </select>
                  </label>
                  <label v-if="orderForm.kind !== 'fee'">买卖方向
                    <select v-model="orderForm.side" class="p12-select" data-testid="p12-order-side">
                      <option value="buy">买入</option>
                      <option value="sell">卖出</option>
                    </select>
                  </label>
                  <label>发生时间<input v-model="orderForm.time" type="datetime-local" data-testid="p12-order-time" /></label>

                  <template v-if="orderForm.kind === 'fill'">
                    <label>证券代码<input v-model.trim="orderForm.security_code" data-testid="p12-order-security" /></label>
                    <label>本笔数量（股）<input v-model.trim="orderForm.quantity" inputmode="numeric" data-testid="p12-order-quantity" /></label>
                    <label>本笔成交价<input v-model.trim="orderForm.price" inputmode="decimal" data-testid="p12-order-price" /></label>
                    <label>本笔费用<input v-model.trim="orderForm.fee" inputmode="decimal" placeholder="0.00" data-testid="p12-order-fill-fee" /></label>
                    <label>原委托总股数<input v-model.trim="orderForm.accepted_total_qty" inputmode="numeric" placeholder="首次登记必填" data-testid="p12-order-fill-accepted" /></label>
                    <label>委托价格上界<input v-model.trim="orderForm.price_bound" inputmode="decimal" placeholder="首次登记必填" data-testid="p12-order-fill-bound" /></label>
                  </template>

                  <template v-if="orderForm.kind === 'observation'">
                    <label>当前委托状态
                      <select v-model="orderForm.order_status" class="p12-select" data-testid="p12-order-status">
                        <option value="in_progress">进行中</option>
                        <option value="cancel_requested">已申请撤单</option>
                        <option value="terminal">已终结</option>
                      </select>
                    </label>
                    <label v-if="orderForm.order_status === 'terminal'">终结累计成交股数<input v-model.trim="orderForm.final_cumulative_fill_qty" inputmode="numeric" placeholder="未知留空" data-testid="p12-order-final" /></label>
                    <label v-if="orderForm.order_status === 'terminal'">已撤单股数<input v-model.trim="orderForm.cancelled_qty" inputmode="numeric" placeholder="未知留空" data-testid="p12-order-cancelled" /></label>
                    <label v-if="orderForm.order_status === 'in_progress'">证券代码<input v-model.trim="orderForm.security_code" data-testid="p12-order-obs-security" /></label>
                    <label v-if="orderForm.order_status === 'in_progress'">委托总股数<input v-model.trim="orderForm.accepted_total_qty" inputmode="numeric" data-testid="p12-order-accepted" /></label>
                    <label v-if="orderForm.order_status === 'in_progress'">价格上界<input v-model.trim="orderForm.price_bound" inputmode="decimal" data-testid="p12-order-bound" /></label>
                    <label v-if="orderForm.order_status === 'in_progress'">预留费用<input v-model.trim="orderForm.fee_upper_bound" inputmode="decimal" placeholder="0.00" data-testid="p12-order-fee-bound" /></label>
                  </template>

                  <template v-if="orderForm.kind === 'fee'">
                    <label>整单确认费用（元）<input v-model.trim="orderForm.fee" inputmode="decimal" data-testid="p12-order-fee" /></label>
                  </template>

                  <p v-if="orderError" class="p12-form-error" data-testid="p12-order-error">{{ orderError }}</p>
                  <a-button type="primary" html-type="submit" :loading="orderSaving" data-testid="p12-order-submit">保存委托事实</a-button>
                </form>

                <div v-if="orderOutcome" class="p12-outcome" data-testid="p12-order-outcome">
                  <h3>{{ label('receipt_status', orderOutcome.status) }}</h3>
                  <dl class="p12-num">
                    <div><dt>模拟现金</dt><dd data-testid="p12-order-cash">{{ money(orderOutcome.account.cash_total) }}</dd></div>
                    <div><dt>冻结占用</dt><dd data-testid="p12-order-occupied">{{ money(orderOutcome.account.cash_occupied) }}</dd></div>
                    <div><dt>可买新仓</dt><dd data-testid="p12-order-available">{{ formatMaybe(orderOutcome.account.cash_available_for_new).text }}</dd></div>
                    <div><dt>账实核对</dt><dd>{{ label('reconciliation_status', orderOutcome.account.reconciliation_status) }}</dd></div>
                  </dl>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>
<script>
import AccountSummary from '../components/AccountSummary.vue'
import PriceChart from '../components/PriceChart.vue'
import accountMixin from '../accountMixin.js'
import {
  cancelSimOrder,
  listSimOrders,
  loadFundingRules,
  loadMarketSession,
  loadSecurityIdentity,
  loadSecurityIntraday,
  loadSecurityQuote,
  loadWorkbench,
  newClientId,
  submitFill,
  submitOrderEvent,
  submitSimOrder
} from '../client.js'
import {
  calcQuoteChange,
  compareMoney,
  formatMaybe,
  formatMoney,
  formatQuantity,
  formatTime,
  humanizeMissingField,
  inferExchangeLabel,
  isSyntheticItem,
  label,
  multiplyPriceQty,
  nextDate,
  normaliseMoneyInput,
  normalizeSecurityName,
  reasonText,
  timezoneSuffix
} from '../format.js'
import { PAPER_EXEC_RULE_VERSION, REAL_SECURITY_SHORTCUTS } from '../paperContract.js'

const GROUPS = ['reconcile', 'exit', 'entry', 'holding', 'sim_open', 'unassigned', 'watch', 'self_watch', 'ended']

export default {
  components: { AccountSummary, PriceChart },
  mixins: [accountMixin],
  data: () => ({
    workbench: null,
    fundingBuckets: [],
    marketSession: null,
    simOrders: [],
    simOrdersAvailable: false,
    selectedId: null,
    chainOpen: false,
    chainPinned: false,
    ticketTab: null,
    orderDrawerOpen: false,
    showAllSimOrders: false,
    // Real stock lookup & live market data per selected stock
    lookupCode: '',
    lookupError: '',
    customWatchItems: [],
    liveQuote: null,
    quoteState: null,
    currentIntraday: null,
    intradayState: null,
    securityIdentity: null,
    quoteLoading: false,
    quoteReqSeq: 0,
    // Simulated order form
    simForm: { side: 'buy', quantity: '100', limit_price: '', order_id: '' },
    simError: '',
    simSubmitting: false,
    simOutcome: null,
    cancellingOrderId: null,
    // Plan / manual report forms
    form: {},
    formError: '',
    submitting: false,
    outcome: null,
    outcomeBefore: null,
    lastFee: null,
    orderForm: {},
    orderError: '',
    orderSaving: false,
    orderOutcome: null,
    paperRuleVersion: PAPER_EXEC_RULE_VERSION,
    realShortcuts: REAL_SECURITY_SHORTCUTS
  }),
  computed: {
    accountRouteQuery () {
      return this.accountId ? { account: this.accountId } : {}
    },
    accountModeBadge () {
      if (!this.workbench) return ''
      if (this.workbench.scope.mode === 'synthetic') {
        return `演练参考账户 · ${this.workbench.scope.account_id}（合成数据）`
      }
      return `个人模拟盘 · ${this.workbench.scope.account_id}`
    },
    marketSessionBadge () {
      if (this.marketSession && this.marketSession.market_state) {
        const st = label('market_state', this.marketSession.market_state)
        const td = this.marketSession.trading_date ? ` · 交易日 ${this.marketSession.trading_date}` : ''
        return `${st}${td}`
      }
      if (this.workbench && this.workbench.scope.mode === 'synthetic') {
        return '固定演练时段'
      }
      return ''
    },
    marketSessionTone () {
      const state = this.marketSession?.market_state
      if (state === 'trading') return 'live'
      if (state === 'lunch_break' || state === 'pre_open' || state === 'call_auction_close') return 'amber'
      return 'muted'
    },
    clockDisplay () {
      if (!this.workbench) return ''
      if (this.marketSession && this.marketSession.server_time) {
        return `行情时钟 ${formatTime(this.marketSession.server_time)}`
      }
      return `${label('clock_kind', this.workbench.clock_kind)} ${formatTime(this.workbench.clock)}`
    },
    blockerSummary () {
      return (this.workbench?.action_blockers || []).map(humanizeMissingField).join('、')
    },
    extraHoldingItems () {
      const oppCodes = new Set((this.workbench?.opportunities || []).map(o => o.security_code))
      const customCodes = new Set(this.customWatchItems.map(c => c.security_code))
      const list = []
      for (const pos of (this.workbench?.account?.positions || [])) {
        if (pos.quantity > 0 && !oppCodes.has(pos.security_code) && !customCodes.has(pos.security_code)) {
          list.push({
            opportunity_id: `HOLD-${pos.security_code}`,
            security_code: pos.security_code,
            security_name: pos.security_name || pos.security_code,
            stage: 'holding',
            queue_group: 'holding',
            action_code: 'HOLD',
            status_code: 'READY',
            reason_codes: [],
            primary_constraint: null,
            changes: [],
            explanations: [],
            next_observation: '持仓跟踪中；可在右侧发起模拟卖出或查看分时走势',
            chart: null,
            cost_price: pos.average_cost || { value: null, reason_code: null },
            plan: null,
            applicability: null,
            selfDirected: true
          })
        }
      }
      return list
    },
    openSimOrderItems () {
      const knownCodes = new Set([
        ...(this.workbench?.opportunities || []).map(o => o.security_code),
        ...this.extraHoldingItems.map(o => o.security_code),
        ...this.customWatchItems.map(o => o.security_code)
      ])
      const seen = new Set()
      const items = []
      for (const ord of this.simOrders) {
        if ((ord.status === 'open' || ord.status === 'partially_filled') && !knownCodes.has(ord.security_code) && !seen.has(ord.security_code)) {
          seen.add(ord.security_code)
          items.push({
            opportunity_id: `SIMORD-${ord.security_code}`,
            security_code: ord.security_code,
            security_name: ord.security_name || ord.security_code,
            stage: 'waiting',
            queue_group: 'sim_open',
            action_code: ord.side === 'sell' ? 'SELL' : 'BUY',
            status_code: 'WAITING',
            reason_codes: [],
            primary_constraint: null,
            changes: [],
            explanations: [],
            next_observation: '模拟限价单挂单等待有效报价中',
            chart: null,
            cost_price: { value: null, reason_code: null },
            plan: null,
            applicability: null,
            selfDirected: true
          })
        }
      }
      return items
    },
    allQueueItems () {
      const rawOpps = (this.workbench?.opportunities || []).map(item => {
        // Fix Opus P0-2: an exit_plan on a stock the account never held and has no sell orders for is NOT an urgent exit todo.
        if (this.isUnheldExit(item)) {
          return { ...item, effective_group: 'ended' }
        }
        return { ...item, effective_group: item.queue_group }
      })
      const extras = [
        ...this.extraHoldingItems.map(i => ({ ...i, effective_group: 'holding' })),
        ...this.openSimOrderItems.map(i => ({ ...i, effective_group: 'sim_open' })),
        ...this.customWatchItems.map(i => ({ ...i, effective_group: 'self_watch' }))
      ]
      return [...rawOpps, ...extras]
    },
    groups () {
      return GROUPS
        .map(key => ({ key, items: this.allQueueItems.filter(item => item.effective_group === key) }))
        .filter(group => group.items.length > 0)
    },
    flatOrderedIds () {
      return this.groups.flatMap(g => g.items.map(i => i.opportunity_id))
    },
    selected () {
      return this.allQueueItems.find(item => item.opportunity_id === this.selectedId) || null
    },
    isCurrentSynthetic () {
      if (!this.selected) return false
      if (this.selected.selfDirected) return false
      return isSyntheticItem(this.selected, this.workbench?.scope?.mode)
    },
    selectedDisplayName () {
      if (!this.selected) return ''
      if (!this.isCurrentSynthetic) {
        const fromQuote = normalizeSecurityName(this.liveQuote && this.securityIdentityName)
        if (fromQuote) return fromQuote
      }
      return normalizeSecurityName(this.selected.security_name) || this.selected.security_code
    },
    securityIdentityName () {
      if (this.securityIdentity && this.securityIdentity.security_name) return this.securityIdentity.security_name
      return this.selected?.quote_name || ''
    },
    selectedExchangeLabel () {
      if (!this.selected) return ''
      const explicit = (!this.isCurrentSynthetic && (this.securityIdentity?.exchange || this.selected.exchange)) || ''
      return inferExchangeLabel(this.selected.security_code, explicit, this.isCurrentSynthetic)
    },
    quoteChange () {
      if (!this.liveQuote || !this.liveQuote.last_price || !this.liveQuote.previous_close) return null
      return calcQuoteChange(this.liveQuote.last_price, this.liveQuote.previous_close)
    },
    quoteUnavailableTitle () {
      if (this.quoteState && !this.quoteState.available) {
        return reasonText(this.quoteState.reasonCode) || '实时报价接口尚未启用'
      }
      if (this.liveQuote && this.liveQuote.status === 'halt') return '该股票当前停牌或零成交'
      return '当前暂无可用实时报价'
    },
    quoteUnavailableDetail () {
      if (this.quoteState && !this.quoteState.available) {
        return this.quoteState.detail || '当前后端未接通实时报价路由；系统不生成模拟价格。'
      }
      if (this.liveQuote && this.liveQuote.reason) return reasonText(this.liveQuote.reason)
      return '休市或数据源暂未返回有效价格快照。'
    },
    selectedPosition () {
      const code = this.selected?.security_code
      if (!code) return null
      return (this.workbench?.account?.positions || []).find(p => p.security_code === code) || null
    },
    selectedPositionQty () {
      return this.selectedPosition ? this.selectedPosition.quantity : 0
    },
    selectedSellableQty () {
      if (!this.selectedPosition) return 0
      if (typeof this.selectedPosition.sellable_quantity === 'number') return this.selectedPosition.sellable_quantity
      return this.selectedPosition.quantity
    },
    selectedAvgCostText () {
      if (!this.selectedPosition) return '—'
      return formatMaybe(this.selectedPosition.average_cost).text
    },
    selectedCostPriceValue () {
      if (this.selected?.cost_price?.value) return this.selected.cost_price.value
      if (this.selectedPosition?.average_cost?.value) return this.selectedPosition.average_cost.value
      return null
    },
    selectedStockSimOrders () {
      const code = this.selected?.security_code
      if (!code) return []
      return this.simOrders.filter(o => o.security_code === code)
    },
    displayedSimOrders () {
      return this.showAllSimOrders ? this.simOrders : this.selectedStockSimOrders
    },
    selectedOpenOrderCount () {
      const simOpen = this.selectedStockSimOrders.filter(o => o.status === 'open' || o.status === 'partially_filled').length
      const manualOpen = (this.workbench?.orders || []).filter(o => o.security_code === this.selected?.security_code && o.status !== 'terminal').length
      return simOpen + manualOpen
    },
    todayConclusion () {
      if (!this.workbench) return ''
      const reconcileCount = this.workbench.account.pending_reconciliations || 0
      const exitCount = this.allQueueItems.filter(i => i.effective_group === 'exit').length
      const entryCount = this.allQueueItems.filter(i => i.effective_group === 'entry' && i.applicability?.status === 'ACTIONABLE').length
      const openSimCount = this.simOrders.filter(o => o.status === 'open' || o.status === 'partially_filled').length
      const parts = []
      if (reconcileCount > 0) parts.push(`${reconcileCount} 项成交记录待核对`)
      if (exitCount > 0) parts.push(`${exitCount} 只持仓触发卖出计划`)
      if (entryCount > 0) parts.push(`${entryCount} 项建仓计划在可执行窗口内`)
      if (openSimCount > 0) parts.push(`${openSimCount} 笔模拟委托挂单中`)
      if (parts.length) return `今日重点：${parts.join('；')}。请先核对右侧任务并按顺序处理。`
      if (this.workbench.action_blockers.length) {
        return '今日无系统推荐操作（日常策略准入待放行）；你可以自主查询真实股票并提交模拟限价委托。'
      }
      if (!this.workbench.package) {
        return '盘后策略计划尚未到发布时间；当前可查看已有持仓、核对历史成交或自主看盘。'
      }
      return '今日无紧急买卖待办，现有持仓与观察候选按既定条件继续跟踪。'
    },
    activeTicketTab () {
      if (!this.selected) return 'sim'
      if (this.selected.selfDirected) return 'sim'
      if (this.ticketTab) return this.ticketTab
      if (this.workbench?.scope?.mode === 'synthetic' && (this.selected.plan || (this.selected.stage === 'exit_plan' && !this.isUnheldExit(this.selected)))) {
        return 'report'
      }
      if (this.simOrdersAvailable) return 'sim'
      return 'report'
    },
    simEstimatedAmount () {
      const qty = /^\d+$/.test(this.simForm.quantity) ? Number.parseInt(this.simForm.quantity, 10) : null
      const price = normaliseMoneyInput(this.simForm.limit_price)
      if (!qty || !price) return null
      return multiplyPriceQty(price, qty)
    },
    simPrecheckHint () {
      const qty = /^\d+$/.test(this.simForm.quantity) ? Number.parseInt(this.simForm.quantity, 10) : null
      if (!qty) return ''
      if (this.simForm.side === 'buy' && qty % 100 !== 0) {
        return `预检提示：${qty} 股不是 100 股（1 手）整数倍，提交后将按整手规则被拒绝。`
      }
      if (this.simForm.side === 'buy' && this.simEstimatedAmount && this.workbench?.account?.cash_available_for_new?.value) {
        if (compareMoney(this.simEstimatedAmount, this.workbench.account.cash_available_for_new.value) === 1) {
          return `预检提示：预计委托金额 ${formatMoney(this.simEstimatedAmount)} 超过当前可用现金 ${formatMoney(this.workbench.account.cash_available_for_new.value)}。`
        }
      }
      if (this.simForm.side === 'sell' && qty > this.selectedSellableQty) {
        return `预检提示：委托卖出 ${qty} 股超过当前 T+1 可卖股数（${this.selectedSellableQty} 股）。`
      }
      return ''
    },
    simOutcomeTitle () {
      if (!this.simOutcome) return ''
      if (this.simOutcome.status === 'filled') return '模拟委托已撮合成交'
      if (this.simOutcome.status === 'open') return '模拟委托已受理挂单'
      if (this.simOutcome.status === 'partially_filled') return '模拟委托部分成交，余量继续挂单'
      if (this.simOutcome.status === 'rejected') return '模拟委托未通过校验（账户未变动）'
      return label('sim_order_status', this.simOutcome.status)
    },
    otherReasons () {
      const item = this.selected
      if (!item || !item.reason_codes) return []
      const ended = item.status_code === 'ENDED' || this.isUnheldExit(item)
      return item.reason_codes.filter(code =>
        code !== item.primary_constraint &&
        code !== 'CAPACITY_AVAILABLE' &&
        !code.startsWith('NEW_') &&
        code !== 'WAITING_FOR_PULLBACK' &&
        !(ended && code === 'EXIT_SIGNAL'))
    },
    endedReasons () {
      const item = this.selected
      if (!item || (item.status_code !== 'ENDED' && !this.isUnheldExit(item))) return []
      return (item.reason_codes || []).filter(code => code === 'EXIT_SIGNAL')
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
      const allTerminal = orders.length > 0 && open.length === 0
      const position = (this.workbench?.account?.positions || []).find(p => p.security_code === item.security_code)
      const held = position ? position.quantity : 0
      const unheld = held === 0 && filled === 0 && orders.length === 0
      if (unheld) {
        return { filled: 0, total: 0, remaining: 0, open: [], allTerminal: false, settled: true, awaitingConfirmation: false, feeConfirmed: false, feeTotal: '', planned: 0, held: 0, unheld: true }
      }
      const total = accepted ? accepted.accepted_total_qty : (planned || held + filled)
      const remaining = Math.max((total || 0) - filled, 0)
      const ready = orders.length > 0 && orders.every(o => o.status === 'terminal' && o.settlement_ready)
      const allFilled = total !== null && total > 0 && filled >= total
      const settled = item.status_code === 'ENDED' || (allFilled && !open.length && ready)
      const awaitingConfirmation = allFilled && !settled
      const feeTotals = orders.map(o => o.fee_confirmed_total).filter(v => v && v !== '0.00')
      const feeConfirmed = orders.length > 0 && feeTotals.length === orders.length
      const feeTotal = feeTotals.length === 1 ? feeTotals[0] : feeTotals.join(' + ')
      return { filled, total, remaining, open, allTerminal, settled, awaitingConfirmation, feeConfirmed, feeTotal, planned, held, unheld: false }
    },
    sellConfirmHint () {
      const t = this.sellTask
      if (!t) return ''
      if (t.allTerminal && !t.feeConfirmed) {
        return '委托已终结，还需确认整单费用；补齐费用后本轮资金自动归回储备。'
      }
      if (t.feeConfirmed) {
        return '卖出股数与整单费用均已确认，请按同一订单编号终结委托，结算资金才会归回储备。'
      }
      return '卖出股数已全部成交。请按同一订单编号确认整单费用，再终结委托，结算资金才会归回储备。'
    },
    taskTitle () {
      const item = this.selected
      if (!item) return '今天的任务'
      if (item.selfDirected) return '自主模拟操作单'
      if (this.isUnheldExit(item)) return '账户核对结论'
      if (item.stage === 'exit_plan') return this.sellIsTomorrow ? '下一交易日计划' : '今天的任务'
      return item.plan ? '下一交易日计划' : '今天的任务'
    },
    taskSubtitle () {
      const item = this.selected
      if (!item) return ''
      if (item.selfDirected) return '基于实时行情自主委托，与系统推荐分开记账'
      if (this.isUnheldExit(item)) return '未持有该股，无需卖出操作'
      if (item.applicability) {
        if (item.applicability.status === 'NEEDS_REASSESSMENT') {
          return '本轮已有成交入账，剩余差额将在下一交易日收盘后重评'
        }
        return label('applicability_status', item.applicability.status)
      }
      return label('stage', item.stage)
    },
    nextStepReadable () {
      const app = this.selected?.applicability
      if (!app) return ''
      if (app.status === 'NEEDS_REASSESSMENT') {
        return `本账户已买入 ${formatQuantity(app.current_qty)} 入账；剩余差额暂停按旧计划追买，将在下一交易日盘后结合最新现金与仓位重新评估（如有同单未报完的分笔成交，可在下方抽屉继续补报）。`
      }
      return label('next_step', app.next_step)
    },
    sellIsTomorrow () {
      const clock = (this.workbench?.clock || '').slice(0, 10)
      const md = this.workbench?.package?.market_date || this.workbench?.publication?.market_date || ''
      return !!clock && !!md && clock <= md
    },
    sellWindowText () {
      if (this.sellTask && this.sellTask.settled) return ''
      const md = this.workbench?.package?.market_date || this.workbench?.publication?.market_date || ''
      if (this.sellIsTomorrow) return `次日（${nextDate(md)}）09:30–15:00`
      const today = (this.workbench?.clock || '').slice(0, 10)
      return `今天（${today.slice(5)}）09:30–15:00`
    },
    sellNext () {
      const item = this.selected
      if (!item || item.stage !== 'exit_plan') return ''
      if (this.isUnheldExit(item)) {
        return '本账户未持有该股（0 股），无需执行卖出或费用确认'
      }
      const t = this.sellTask || { filled: 0, remaining: null, settled: false, open: [], allTerminal: false, total: null }
      if (item.status_code === 'ENDED' || t.settled) {
        return `该机会已卖出 ${formatQuantity(t.filled || t.total || 0)} 结束，无需操作`
      }
      if (t.awaitingConfirmation) {
        if (t.allTerminal && !t.feeConfirmed) {
          return `委托已终结，还需确认整单费用（已卖出 ${formatQuantity(t.filled || t.total || 0)}）`
        }
        if (t.feeConfirmed) {
          return `卖出已全部成交（${formatQuantity(t.filled || t.total || 0)}），整单费用 ${formatMoney(t.feeTotal)} 已确认，还需终结委托`
        }
        return `卖出已全部成交（${formatQuantity(t.filled || t.total || 0)}），还需确认整单费用并终结委托`
      }
      if (t.filled > 0) {
        const open = t.open.length
        return `已成交 ${formatQuantity(t.filled)}，剩余 ${formatQuantity(t.remaining)}；同一委托待终结确认${open ? `（${open} 笔进行中）` : ''}`
      }
      return this.sellIsTomorrow
        ? `下一交易日计划：卖出 ${formatQuantity(t.total || 0)}，在交易窗口内登记实际成交`
        : `今天任务：卖出 ${formatQuantity(t.total || 0)}，在交易窗口内登记实际成交`
    },
    takeawayActionTitle () {
      const item = this.selected
      if (!item) return '—'
      if (item.selfDirected) return this.selectedPositionQty > 0 ? '自主持仓跟踪' : '自主看盘与模拟委托'
      if (this.isUnheldExit(item)) return '无需操作'
      return label('action_code', item.action_code)
    },
    defaultChangeText () {
      if (this.selected?.selfDirected) {
        if (this.liveQuote && this.liveQuote.last_price) {
          return `实时报价 ¥${this.liveQuote.last_price}（${label('quote_status', this.liveQuote.status)}）`
        }
        return '你主动纳入看盘的标的，暂无系统盘后策略变化记录'
      }
      return '与上一交易日相比没有新增触发变化'
    },
    primaryConstraintTitle () {
      if (!this.selected) return '无'
      if (this.isUnheldExit(this.selected)) return '本账户未持有该股'
      return this.selected.primary_constraint ? reasonText(this.selected.primary_constraint) : '无额外阻断约束'
    },
    actionSentence () {
      const item = this.selected
      if (!item) return ''
      if (item.selfDirected) {
        return '该标的未绑定系统盘后推荐；你可在右侧按实时行情提交自主模拟买入或卖出限价单。'
      }
      if (this.isUnheldExit(item)) {
        return '策略已发出退出信号，但本账户此前未买入该股（持仓 0 股），今日无需任何操作。'
      }
      const plan = item.plan
      if (item.action_code === 'BUY' && plan) {
        if (item.applicability && item.applicability.status === 'NEEDS_REASSESSMENT') {
          return `本轮已买入 ${formatQuantity(item.applicability.current_qty)} 入账；剩余额度待下一交易日收盘后结合新余额复核`
        }
        return `${formatTime(plan.eligible_at).slice(0, 10)} 开盘后买入 ${formatQuantity(item.applicability.delta_qty)}，成交价不高于 ${plan.price_cap}`
      }
      if (item.action_code === 'SELL') {
        const t = this.sellTask || { filled: 0, remaining: Math.abs(item.applicability ? item.applicability.delta_qty : 0) }
        if (t.filled > 0 && !t.remaining) {
          if (t.allTerminal && !t.feeConfirmed) return `委托已终结，还需确认整单费用`
          return t.feeConfirmed
            ? `已卖出 ${formatQuantity(t.filled)}，待终结委托`
            : `已卖出 ${formatQuantity(t.filled)}，待确认费用并终结委托`
        }
        if (t.filled > 0) return `已卖出 ${formatQuantity(t.filled)}，剩余 ${formatQuantity(t.remaining)} 待成交并在同委托终结`
        const qty = t.remaining || Math.abs((item.applicability && item.applicability.delta_qty) || 0)
        const md = this.workbench?.package?.market_date || this.workbench?.publication?.market_date || ''
        const when = this.sellIsTomorrow ? `次日（${nextDate(md)}）开盘后` : '今天'
        return `${when}卖出 ${formatQuantity(qty)}，在交易窗口内登记实际成交`
      }
      if (item.stage === 'exit_plan' && item.status_code === 'ENDED') {
        const t = this.sellTask || { filled: 0, total: 0 }
        return `该机会已卖出 ${formatQuantity(t.filled || t.total || 0)} 结束，无需操作`
      }
      if (item.next_observation) return item.next_observation
      return item.explanations && item.explanations[0] ? item.explanations[0].text : '继续观察'
    }
  },
  watch: {
    selected () {
      this.ticketTab = null
      this.resetForm()
      this.resetOrderForm()
      this.resetSimForm()
      this.syncSelectedMarketData()
    }
  },
  mounted () {
    window.addEventListener('keydown', this.onGlobalKeydown)
  },
  beforeDestroy () {
    window.removeEventListener('keydown', this.onGlobalKeydown)
  },
  methods: {
    async load (accountId) {
      const [wb, rulesRes, sessionRes, simRes] = await Promise.all([
        loadWorkbench(accountId),
        loadFundingRules(accountId).catch(() => null),
        loadMarketSession(),
        listSimOrders(accountId)
      ])
      this.workbench = wb
      this.fundingBuckets = rulesRes?.funding?.buckets || []
      this.marketSession = sessionRes.available ? sessionRes.data : null
      this.simOrdersAvailable = simRes.available
      this.simOrders = simRes.orders
      const requestedSec = this.$route.query.security
      if (requestedSec && /^\d{6}$/.test(String(requestedSec))) {
        const match = this.allQueueItems.find(i => i.security_code === String(requestedSec) && !this.isItemSynthetic(i))
        if (match) {
          this.selectedId = match.opportunity_id
        } else {
          await this.lookupSecurity(String(requestedSec))
          return
        }
      }
      const ids = this.allQueueItems.map(item => item.opportunity_id)
      if (!ids.includes(this.selectedId)) {
        // Prefer first non-ended item if available.
        const activeFirst = this.allQueueItems.find(item => item.effective_group !== 'ended')
        this.selectedId = (activeFirst && activeFirst.opportunity_id) || ids[0] || null
      }
      this.resetForm()
      this.resetOrderForm()
      this.resetSimForm()
      await this.syncSelectedMarketData()
    },
    async refreshAll () {
      await this.refresh()
    },
    selectOpportunity (oppId) {
      this.selectedId = oppId
    },
    onSelectBucket (bucket) {
      const codeMatch = /(\d{6})/.exec(bucket.cycle_id || '')
      if (codeMatch) {
        const found = this.allQueueItems.find(i => i.security_code === codeMatch[1])
        if (found) {
          this.selectedId = found.opportunity_id
          return
        }
      }
      this.$router.push({ path: '/p12/funding', query: this.accountRouteQuery }).catch(() => {})
    },
    onGlobalKeydown (event) {
      const tag = (event.target && event.target.tagName) ? event.target.tagName.toLowerCase() : ''
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.metaKey || event.ctrlKey || event.altKey) return
      const ids = this.flatOrderedIds
      if (!ids.length) return
      const idx = ids.indexOf(this.selectedId)
      if (event.key === 'ArrowDown' || event.key === 'j' || event.key === 'J') {
        event.preventDefault()
        const next = idx < 0 ? 0 : (idx + 1) % ids.length
        this.selectedId = ids[next]
      } else if (event.key === 'ArrowUp' || event.key === 'k' || event.key === 'K') {
        event.preventDefault()
        const prev = idx <= 0 ? ids.length - 1 : idx - 1
        this.selectedId = ids[prev]
      }
    },
    isItemSynthetic (item) {
      if (!item || item.selfDirected) return false
      return isSyntheticItem(item, this.workbench?.scope?.mode)
    },
    heldQtyFor (securityCode) {
      const pos = (this.workbench?.account?.positions || []).find(p => p.security_code === securityCode)
      return pos ? pos.quantity : 0
    },
    sellOrdersFor (securityCode) {
      return (this.workbench?.orders || []).filter(o => o.security_code === securityCode && o.side === 'sell')
    },
    isUnheldExit (item) {
      if (!item || item.stage !== 'exit_plan') return false
      if (item.status_code === 'ENDED') return false
      return this.heldQtyFor(item.security_code) === 0 && this.sellOrdersFor(item.security_code).length === 0
    },
    groupTitle (key) {
      const map = {
        reconcile: '待核对事项',
        exit: '卖出 / 减仓待办',
        entry: '今日建仓计划',
        holding: '当前持仓',
        sim_open: '模拟委托挂单中',
        unassigned: '未分配资金桶',
        watch: '观察候选',
        self_watch: '自主看盘',
        ended: '已结束 / 未参与'
      }
      return map[key] || label('queue_group', key)
    },
    displaySecurityName (item) {
      return normalizeSecurityName(item.quote_name || item.security_name) || item.security_code
    },
    displaySecurityBadge (item) {
      const syn = this.isItemSynthetic(item)
      const ex = inferExchangeLabel(item.security_code, item.exchange, syn)
      return `${ex} · ${item.security_code}`
    },
    queueActionLabel (item) {
      if (item.selfDirected) {
        return this.heldQtyFor(item.security_code) > 0 ? `持仓 ${this.heldQtyFor(item.security_code)} 股` : '看盘 / 下单'
      }
      if (this.isUnheldExit(item)) return '无需卖出'
      if (item.action_code === 'BUY' && item.applicability && item.applicability.delta_qty > 0) {
        return `买入 ${item.applicability.delta_qty} 股`
      }
      if (item.action_code === 'SELL' && this.heldQtyFor(item.security_code) > 0) {
        return `卖出 ${this.heldQtyFor(item.security_code)} 股`
      }
      return label('action_code', item.action_code)
    },
    actionTone (item) {
      if (this.isUnheldExit(item)) return 'muted'
      if (item.action_code === 'SELL') return 'sell'
      if (item.action_code === 'BUY') return 'buy'
      return 'neutral'
    },
    queueStatusLabel (item) {
      if (item.selfDirected) return '自主模拟'
      if (this.isUnheldExit(item)) return '未持仓'
      return label('status_code', item.status_code)
    },
    queueStatusTone (item) {
      if (item.selfDirected) return 'locked'
      if (this.isUnheldExit(item)) return 'locked'
      return this.statusTone(item.status_code)
    },
    queueBrief (item) {
      if (item.selfDirected) {
        return item.quote_summary || item.next_observation || '点击查看当日真实报价、分时走势并可提交模拟限价委托'
      }
      if (this.isUnheldExit(item)) {
        return '此前未买入该股（持仓 0 股），本轮退出信号无需操作'
      }
      if (item.applicability && item.applicability.status === 'NEEDS_REASSESSMENT') {
        return `已买入 ${item.applicability.current_qty} 股入账，剩余股数待次日盘后复核`
      }
      if (item.explanations && item.explanations.length) {
        return item.explanations[0].text
      }
      if (item.next_observation) return item.next_observation
      if (item.primary_constraint) return reasonText(item.primary_constraint)
      return '按规则持续跟踪'
    },
    async lookupSecurity (presetCode) {
      const code = String(presetCode || this.lookupCode || '').trim()
      this.lookupError = ''
      if (!/^\d{6}$/.test(code)) {
        this.lookupError = '请输入完整的 6 位数字证券代码，例如 600000 或 000002'
        return
      }
      this.lookupCode = ''
      const existing = this.customWatchItems.find(item => item.security_code === code)
      if (existing) {
        this.selectedId = existing.opportunity_id
        await this.syncSelectedMarketData()
        return
      }
      const shortcut = REAL_SECURITY_SHORTCUTS.find(s => s.code === code)
      const oppId = `SELF-${code}`
      const entry = {
        opportunity_id: oppId,
        security_code: code,
        security_name: shortcut ? shortcut.hintName : `证券 ${code}`,
        exchange: shortcut ? shortcut.exchange : inferExchangeLabel(code, '', false),
        stage: 'waiting',
        queue_group: 'self_watch',
        action_code: 'NONE',
        status_code: 'READY',
        reason_codes: [],
        primary_constraint: null,
        changes: [],
        explanations: [],
        next_observation: '自主查询标的；支持读取实时报价、当日完整分钟分时与提交模拟委托',
        chart: null,
        cost_price: { value: null, reason_code: null },
        plan: null,
        applicability: null,
        selfDirected: true,
        quote_name: '',
        quote_summary: ''
      }
      this.customWatchItems = [entry, ...this.customWatchItems]
      this.selectedId = oppId
    },
    async syncSelectedMarketData () {
      const item = this.selected
      const seq = ++this.quoteReqSeq
      this.liveQuote = null
      this.quoteState = null
      this.currentIntraday = null
      this.intradayState = null
      this.securityIdentity = null
      if (!item || this.isCurrentSynthetic) {
        this.quoteLoading = false
        return
      }
      this.quoteLoading = true
      const code = item.security_code
      const [quoteRes, intraRes, idRes] = await Promise.all([
        loadSecurityQuote(code),
        loadSecurityIntraday(code),
        loadSecurityIdentity(code)
      ])
      if (seq !== this.quoteReqSeq) return
      this.quoteLoading = false
      this.quoteState = quoteRes
      this.intradayState = intraRes
      if (idRes.available && idRes.data) {
        this.securityIdentity = idRes.data
      }
      if (quoteRes.available && quoteRes.data) {
        this.liveQuote = quoteRes.data.quote
        const officialName = normalizeSecurityName(quoteRes.data.security_name || idRes.data?.security_name)
        const officialEx = quoteRes.data.exchange || idRes.data?.exchange
        if (officialName) {
          this.$set(item, 'quote_name', officialName)
          if (item.selfDirected) this.$set(item, 'security_name', officialName)
        }
        if (officialEx) this.$set(item, 'exchange', officialEx)
        if (this.liveQuote.last_price) {
          this.$set(item, 'quote_summary', `最新报价 ¥${this.liveQuote.last_price} · ${label('quote_status', this.liveQuote.status)}`)
          if (!this.simForm.limit_price) {
            this.simForm.limit_price = this.liveQuote.last_price
          }
        }
      }
      if (intraRes.available && intraRes.data) {
        this.currentIntraday = intraRes.data
      }
    },
    resetSimForm () {
      const plan = this.selected?.plan
      const defaultSide = (this.selected?.stage === 'exit_plan' && !this.isUnheldExit(this.selected)) ? 'sell' : 'buy'
      this.simForm = {
        side: defaultSide,
        quantity: plan && plan.delta_qty > 0 ? String(plan.delta_qty) : (defaultSide === 'sell' && this.selectedSellableQty > 0 ? String(this.selectedSellableQty) : '100'),
        limit_price: plan ? plan.price_cap : '',
        order_id: newClientId('sim')
      }
      this.simError = ''
      this.simOutcome = null
    },
    async submitPaperOrder () {
      this.simError = ''
      const quantity = /^\d+$/.test(this.simForm.quantity) ? Number.parseInt(this.simForm.quantity, 10) : null
      const limitPrice = normaliseMoneyInput(this.simForm.limit_price)
      if (!quantity || quantity <= 0) {
        this.simError = '请输入正整数委托股数（例如 100）'
        return
      }
      if (!limitPrice || limitPrice === '0.00') {
        this.simError = '请输入有效的委托限价（最多两位小数）'
        return
      }
      this.simSubmitting = true
      try {
        const nowIso = this.marketSession?.server_time || this.workbench?.clock || new Date().toISOString()
        const result = await submitSimOrder(this.accountId, {
          order_id: this.simForm.order_id || newClientId('sim'),
          side: this.simForm.side,
          security_code: this.selected.security_code,
          quantity,
          limit_price: limitPrice,
          occurred_at: nowIso
        })
        this.simOutcome = result
        this.simForm.order_id = newClientId('sim')
        const [wb, simRes] = await Promise.all([
          loadWorkbench(this.accountId),
          listSimOrders(this.accountId)
        ])
        this.workbench = wb
        this.simOrdersAvailable = simRes.available
        this.simOrders = simRes.orders
      } catch (error) {
        this.simError = error.detail ? `${reasonText(error.reasonCode)}（${error.detail}）` : (reasonText(error.reasonCode) || '模拟委托提交失败')
      } finally {
        this.simSubmitting = false
      }
    },
    async cancelPaperOrder (orderId) {
      this.cancellingOrderId = orderId
      this.simError = ''
      try {
        await cancelSimOrder(this.accountId, orderId)
        const [wb, simRes] = await Promise.all([
          loadWorkbench(this.accountId),
          listSimOrders(this.accountId)
        ])
        this.workbench = wb
        this.simOrders = simRes.orders
      } catch (error) {
        this.simError = reasonText(error.reasonCode) || '撤单失败，请稍后刷新核对'
      } finally {
        this.cancellingOrderId = null
      }
    },
    resetForm () {
      const plan = this.selected?.plan
      this.form = {
        quantity: plan ? String(plan.delta_qty) : '',
        price: plan ? plan.price_cap : '',
        fee: plan ? plan.estimated_fee : '5.00',
        time: (this.workbench?.clock || '').slice(0, 16),
        reportId: newClientId('fill')
      }
      this.formError = ''
      this.outcome = null
    },
    resetOrderForm () {
      const code = this.selected?.security_code || ''
      const defaultSide = (this.selected?.stage === 'exit_plan' && !this.isUnheldExit(this.selected)) ? 'sell' : 'buy'
      this.orderForm = {
        report_id: newClientId('ord'),
        order_id: '',
        kind: 'fill',
        side: defaultSide,
        security_code: code,
        quantity: '',
        price: '',
        fee: '0.00',
        accepted_total_qty: '',
        price_bound: '',
        fee_upper_bound: '0.00',
        order_status: 'in_progress',
        final_cumulative_fill_qty: '',
        cancelled_qty: '',
        time: (this.workbench?.clock || '').slice(0, 16)
      }
      this.orderError = ''
    },
    prefillSell () {
      const item = this.selected
      const task = this.sellTask
      if (!item || !task) return
      this.ticketTab = 'report'
      this.orderDrawerOpen = true
      const open = task.open[0]
      const existing = this.sellOrders[0]
      const orderId = open ? open.order_id : (existing ? existing.order_id : `SELL-${item.security_code}-1`)
      const priceHint = item.chart?.bars?.length ? item.chart.bars[item.chart.bars.length - 1].close : ''
      this.orderForm = {
        ...this.orderForm,
        report_id: newClientId('ord'),
        order_id: orderId,
        kind: 'fill',
        side: 'sell',
        security_code: item.security_code,
        quantity: task.remaining ? String(task.remaining) : '',
        price: priceHint,
        fee: '0.00',
        accepted_total_qty: existing ? '' : String(task.total || ''),
        price_bound: existing ? '' : priceHint,
        time: (this.workbench?.clock || '').slice(0, 16)
      }
      this.orderError = ''
      this.orderOutcome = null
    },
    prefillConfirm (step) {
      const item = this.selected
      const task = this.sellTask
      const order = task?.open[0] || this.sellOrders[0]
      if (!item || !task || !order) return
      this.ticketTab = 'report'
      this.orderDrawerOpen = true
      const isFee = step === 'fee'
      this.orderForm = {
        ...this.orderForm,
        report_id: newClientId('ord'),
        order_id: order.order_id,
        kind: isFee ? 'fee' : 'observation',
        side: 'sell',
        security_code: item.security_code,
        fee: isFee ? '5.00' : this.orderForm.fee,
        order_status: 'terminal',
        final_cumulative_fill_qty: String(task.filled),
        cancelled_qty: '0',
        time: (this.workbench?.clock || '').slice(0, 16)
      }
      this.orderError = ''
      this.orderOutcome = null
    },
    nextOrderRevision (orderId) {
      const existing = (this.workbench?.orders || []).find(item => item.order_id === orderId)
      return existing ? existing.revision + 1 : 1
    },
    async submitOrder () {
      this.orderError = ''
      const f = this.orderForm
      if (!f.order_id) { this.orderError = '请填写委托单号'; return }
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(f.time)) { this.orderError = '请选择发生时间'; return }
      const tz = timezoneSuffix(this.workbench.clock)
      const base = {
        report_id: f.report_id,
        order_id: f.order_id,
        order_revision: this.nextOrderRevision(f.order_id),
        kind: f.kind,
        side: f.side,
        security_code: f.security_code,
        occurred_at: `${f.time}:00${tz}`,
        quantity: null,
        price: null,
        confirmed_fee: null,
        accepted_total_qty: null,
        price_bound: null,
        fee_upper_bound: null,
        order_status: null,
        final_cumulative_fill_qty: null,
        cancelled_qty: null
      }
      if (f.kind === 'fill') {
        const qty = /^\d+$/.test(f.quantity) ? Number.parseInt(f.quantity, 10) : null
        const price = normaliseMoneyInput(f.price)
        const fee = normaliseMoneyInput(f.fee || '0')
        if (!f.security_code || !qty || !price || price === '0.00' || fee === null) {
          this.orderError = '请填写证券代码、正整数股数、有效成交价和费用'
          return
        }
        base.quantity = qty
        base.price = price
        base.confirmed_fee = fee
        if (f.accepted_total_qty) base.accepted_total_qty = Number.parseInt(f.accepted_total_qty, 10)
        if (f.price_bound) base.price_bound = normaliseMoneyInput(f.price_bound)
      } else if (f.kind === 'observation') {
        base.order_status = f.order_status
        if (f.order_status === 'in_progress') {
          const total = /^\d+$/.test(f.accepted_total_qty) ? Number.parseInt(f.accepted_total_qty, 10) : null
          const bound = normaliseMoneyInput(f.price_bound)
          const feeBound = normaliseMoneyInput(f.fee_upper_bound || '0')
          if (!f.security_code || !total || !bound || feeBound === null) {
            this.orderError = '进行中委托需填写证券代码、总股数、价格上界和预留费用'
            return
          }
          base.accepted_total_qty = total
          base.price_bound = bound
          base.fee_upper_bound = feeBound
        } else if (f.order_status === 'terminal') {
          if (f.final_cumulative_fill_qty !== '') base.final_cumulative_fill_qty = Number.parseInt(f.final_cumulative_fill_qty, 10)
          if (f.cancelled_qty !== '') base.cancelled_qty = Number.parseInt(f.cancelled_qty, 10)
        }
      } else {
        const fee = normaliseMoneyInput(f.fee)
        if (fee === null) { this.orderError = '请填写有效的整单确认费用'; return }
        base.confirmed_fee = fee
      }
      this.orderSaving = true
      try {
        this.orderOutcome = await submitOrderEvent(this.accountId, base)
        this.workbench = await loadWorkbench(this.accountId)
        this.resetOrderForm()
      } catch (error) {
        this.orderError = reasonText(error.reasonCode) || '委托事实保存失败'
      } finally {
        this.orderSaving = false
      }
    },
    async submit () {
      this.formError = ''
      const quantity = /^\d+$/.test(this.form.quantity) ? Number.parseInt(this.form.quantity, 10) : null
      const price = normaliseMoneyInput(this.form.price)
      const fee = normaliseMoneyInput(this.form.fee)
      if (!quantity || quantity <= 0) { this.formError = '成交数量需为正整数'; return }
      if (!price || price === '0.00') { this.formError = '成交价需为最多两位小数的正数'; return }
      if (fee === null) { this.formError = '费用需为最多两位小数的非负数'; return }
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(this.form.time)) { this.formError = '请选择成交日期和时间'; return }
      this.submitting = true
      this.outcomeBefore = this.workbench.account.cash_total
      this.lastFee = fee
      try {
        const tz = timezoneSuffix(this.workbench.clock)
        const outcome = await submitFill(this.accountId, {
          report_id: this.form.reportId,
          plan_id: this.selected.plan.plan_id,
          quantity,
          price,
          confirmed_fee: fee,
          occurred_at: `${this.form.time}:00${tz}`
        })
        this.workbench = await loadWorkbench(this.accountId)
        this.outcome = outcome
      } catch (error) {
        this.formError = reasonText(error.reasonCode) || '成交保存失败，请检查输入后重试'
      } finally {
        this.submitting = false
      }
    },
    positionText (account) {
      const line = account.positions.find(item => item.security_code === this.selected?.security_code)
      return line ? `${this.selectedDisplayName}（${line.security_code}）${formatQuantity(line.quantity)}` : '0 股'
    },
    statusTone (code) {
      if (code === 'READY') return 'accepted'
      if (code === 'NEEDS_RECONCILIATION' || code === 'MISSING_INPUT' || code === 'POLICY_NOT_ADOPTED') return 'control'
      return 'locked'
    },
    simOrderTone (status) {
      if (status === 'filled') return 'accepted'
      if (status === 'rejected') return 'control'
      return 'locked'
    },
    timeWithSec (iso) {
      if (!iso) return '—'
      return String(iso).slice(0, 19).replace('T', ' ')
    },
    formatMaybe,
    money: formatMoney,
    qty: formatQuantity,
    time: formatTime,
    reason: reasonText,
    label
  }
}
</script>
