<template>
  <div class="p12-chart" data-testid="p12-chart">
    <div class="p12-chart-toolbar">
      <div class="p12-chart-tabs" role="tablist" aria-label="行情视图切换">
        <button
          type="button"
          role="tab"
          :aria-selected="String(activeMode === 'intraday')"
          :class="['p12-chart-tab', { active: activeMode === 'intraday' }]"
          data-testid="p12-tab-intraday"
          @click="manualMode = 'intraday'"
        >
          当日分时
          <small v-if="intraday && intraday.last_complete_minute">{{ intraday.last_complete_minute }}</small>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="String(activeMode === 'daily')"
          :class="['p12-chart-tab', { active: activeMode === 'daily' }]"
          data-testid="p12-tab-daily"
          @click="manualMode = 'daily'"
        >
          日 K 线走势
          <small v-if="latestBar">{{ latestBar.session.slice(5) }}</small>
        </button>
      </div>

      <div v-if="activeMode === 'intraday'" class="p12-chart-meta" data-testid="p12-intraday-meta">
        <template v-if="intradayLoading">
          <span>正在读取所选股票当日分时…</span>
        </template>
        <template v-else-if="hasIntraday">
          <span>来源：{{ intradaySourceText }}</span>
          <span v-if="intraday.trade_date">交易日 {{ intraday.trade_date }}</span>
          <strong class="p12-num">最近完整分钟 {{ intraday.last_complete_minute }}（{{ intraday.first_minute }}–{{ intraday.last_complete_minute }}，共 {{ intraday.points.length }} 点）</strong>
        </template>
        <template v-else>
          <span>{{ synthetic ? '演练标的未连接真实分时' : intradayUnavailableTag }}</span>
        </template>
      </div>
      <div v-else class="p12-chart-meta">
        <span v-if="chart && chart.bars.length">来源：{{ basisText }} · 截至 {{ asOf }}</span>
        <span v-else>暂无已准入历史日 K 线</span>
      </div>
    </div>

    <!-- Intraday Minute View (UI draws only received points, preserving lunch break & gaps) -->
    <template v-if="activeMode === 'intraday'">
      <svg
        v-if="hasIntraday"
        :viewBox="`0 0 ${width} ${height}`"
        role="img"
        :aria-label="`${name} 当日分时走势`"
        data-testid="p12-intraday-svg"
      >
        <g class="grid">
          <line v-for="tick in intradayTicks" :key="'ig' + tick.label" :x1="pad" :x2="width - pad - 44" :y1="tick.y" :y2="tick.y" />
          <text v-for="tick in intradayTicks" :key="'it' + tick.label" :x="width - pad - 38" :y="tick.y + 3">{{ tick.label }}</text>
        </g>
        <!-- Lunch break divider 11:30 / 13:00 -->
        <g class="lunch-divider">
          <line :x1="lunchX" :x2="lunchX" :y1="priceTop" :y2="intradayBottom" stroke="#d9e0ea" stroke-dasharray="3 3" />
          <text :x="lunchX" :y="priceTop - 3" text-anchor="middle" fill="#8a95a6" font-size="9">11:30 / 13:00 午休</text>
        </g>
        <!-- Previous close reference -->
        <g v-if="intradayPrevClose !== null" class="level prev">
          <line :x1="pad" :x2="width - pad - 44" :y1="iy(intradayPrevClose)" :y2="iy(intradayPrevClose)" />
          <text :x="pad + 4" :y="iy(intradayPrevClose) - 4">昨收 {{ prevClose || intraday.previous_close }}</text>
        </g>
        <!-- Limit / cap / cost reference lines -->
        <g v-for="level in intradayLevels" :key="'il' + level.key" :class="['level', level.key]">
          <line :x1="pad" :x2="width - pad - 44" :y1="iy(level.value)" :y2="iy(level.value)" />
          <text :x="pad + 4" :y="iy(level.value) - 5">{{ level.label }} {{ level.text }}</text>
        </g>
        <!-- Segmented intraday path: breaks across lunch and any missing-minute gaps -->
        <path
          v-for="(seg, sIdx) in intradaySegments"
          :key="'seg' + sIdx"
          :d="seg"
          fill="none"
          class="intraday-line"
        />
        <!-- Last complete minute dot & callout -->
        <g v-if="lastIntradayPoint" class="intraday-last">
          <circle :cx="lastIntradayPoint.x" :cy="lastIntradayPoint.y" r="3.4" class="intraday-dot" />
          <text
            :x="Math.min(lastIntradayPoint.x + 6, width - pad - 52)"
            :y="Math.max(priceTop + 10, lastIntradayPoint.y - 5)"
            class="intraday-last-label"
          >{{ lastIntradayPoint.hm }} {{ lastIntradayPoint.price }}</text>
        </g>
        <text class="axis-date" :x="pad" :y="height - 6">09:30</text>
        <text class="axis-date" :x="pad + plotWidth * (60 / 241)" :y="height - 6" text-anchor="middle">10:30</text>
        <text class="axis-date" :x="lunchX" :y="height - 6" text-anchor="middle">11:30/13:00</text>
        <text class="axis-date" :x="pad + plotWidth * (181 / 241)" :y="height - 6" text-anchor="middle">14:00</text>
        <text class="axis-date" :x="width - pad - 44" :y="height - 6" text-anchor="end">15:00</text>
      </svg>
      <div v-else class="p12-chart-empty" data-testid="p12-intraday-empty">
        <div v-if="intradayLoading">
          <strong>正在读取所选股票的当日真实分时…</strong>
          <p>切换股票时一起刷新最近完整分钟点。</p>
        </div>
        <div v-else-if="synthetic">
          <strong>当前为演练合成标的，不伪造盘中真实分时线</strong>
          <p>演练数据仅包含固定日 K 线（点击上方「日 K 线走势」可核对）；如需查看真实股票当天分时，请在左侧点击或输入 6 位证券代码（如 600000 浦发银行）。</p>
        </div>
        <div v-else>
          <strong>{{ intradayEmptyTitle }}</strong>
          <p>{{ intradayEmptyDesc }}</p>
        </div>
      </div>
      <p class="p12-chart-note">
        分时说明：严格绘制服务端返回的当天已完成分钟点（午休与断点保留原貌）<template v-if="intraday && intraday.as_of"> · 最后源时刻 {{ formatTime(intraday.as_of) || intraday.as_of }}</template>；模拟委托成交另按带源时钟的实时报价快照触发。
      </p>
    </template>

    <!-- Historical Daily Candlestick View -->
    <template v-else>
      <svg v-if="chart && chart.bars.length" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="`${name} 价格与成交量`">
        <g class="grid">
          <line v-for="tick in priceTicks" :key="'g' + tick.label" :x1="pad" :x2="width - pad - 44" :y1="tick.y" :y2="tick.y" />
          <text v-for="tick in priceTicks" :key="'t' + tick.label" :x="width - pad - 38" :y="tick.y + 3">{{ tick.label }}</text>
        </g>
        <g class="candles">
          <g v-for="(bar, index) in bars" :key="bar.session" :class="bar.up ? 'up' : 'down'">
            <line :x1="x(index)" :x2="x(index)" :y1="y(bar.high)" :y2="y(bar.low)" />
            <rect :x="x(index) - candle / 2" :width="candle" :y="Math.min(y(bar.open), y(bar.close))" :height="Math.max(1, Math.abs(y(bar.open) - y(bar.close)))" />
          </g>
        </g>
        <g v-for="level in levels" :key="level.key" :class="['level', level.key]">
          <line :x1="pad" :x2="width - pad - 44" :y1="y(level.value)" :y2="y(level.value)" />
          <text :x="pad + 4" :y="y(level.value) - 5">{{ level.label }} {{ level.text }}</text>
        </g>
        <g class="volume">
          <rect v-for="(bar, index) in bars" :key="'v' + bar.session" :x="x(index) - candle / 2" :width="candle" :y="vy(bar.volume)" :height="volumeBase - vy(bar.volume)" :class="bar.up ? 'up' : 'down'" />
          <line v-if="chart.volume_baseline" class="baseline" :x1="pad" :x2="width - pad - 44" :y1="vy(chart.volume_baseline)" :y2="vy(chart.volume_baseline)" />
          <text v-if="chart.volume_baseline" :x="pad + 4" :y="vy(chart.volume_baseline) - 4">前 {{ chart.volume_baseline_window }} 日均量</text>
        </g>
        <text class="axis-date" :x="pad" :y="height - 4">{{ bars[0].session }}</text>
        <text class="axis-date" :x="width - pad - 44" :y="height - 4" text-anchor="end">{{ bars[bars.length - 1].session }}</text>
      </svg>
      <div v-else class="p12-chart-empty">
        <div>
          <strong>{{ emptyText }}</strong>
          <p v-if="hasLiveSnapshot">实时快照、当日分时与历史日 K 线各自独立标注来源；未接入合格日频数据时，系统不以实时快照拼凑日线图。</p>
          <p v-else>缺少合格日频行情时暂停依赖该走势的新增建仓建议；你已持有的仓位与账户核对不受影响。</p>
        </div>
      </div>
      <p v-if="chart" class="p12-chart-note">价格口径：{{ basisText }} · 数据截至 {{ asOf }} · 图形坐标仅用于显示</p>
    </template>
  </div>
</template>
<script>
import { formatTime, label, reasonText } from '../format.js'

function hmToSlot (hm) {
  const m = /^(\d{2}):(\d{2})$/.exec(hm || '')
  if (!m) return null
  const minutes = Number(m[1]) * 60 + Number(m[2])
  // Morning continuous trading: 09:30 (570) .. 11:30 (690) -> slot 0..120
  if (minutes >= 570 && minutes <= 690) return { slot: minutes - 570, session: 'am', rawMin: minutes }
  // Afternoon continuous trading: 13:00 (780) .. 15:00 (900) -> slot 121..241
  if (minutes >= 780 && minutes <= 900) return { slot: 121 + (minutes - 780), session: 'pm', rawMin: minutes }
  return null
}

// Coordinates use Number() for drawing only; displayed values stay the server's decimal text.
export default {
  props: {
    securityKey: { type: String, default: '' },
    chart: Object,
    intraday: Object,
    intradayState: Object,
    intradayLoading: { type: Boolean, default: false },
    prevClose: String,
    synthetic: { type: Boolean, default: false },
    name: String,
    priceCap: String,
    costPrice: String,
    hasLiveSnapshot: { type: Boolean, default: false },
    emptyText: { type: String, default: '暂无合格价格数据' }
  },
  data: () => ({
    width: 660,
    height: 242,
    pad: 12,
    priceTop: 16,
    priceBottom: 160,
    intradayBottom: 218,
    volumeTop: 176,
    volumeBase: 226,
    manualMode: null
  }),
  computed: {
    hasIntraday () {
      return Boolean(
        this.intraday &&
        this.intraday.status !== 'unavailable' &&
        Array.isArray(this.intraday.points) &&
        this.intraday.points.length > 0
      )
    },
    activeMode () {
      if (this.manualMode) return this.manualMode
      if (this.hasIntraday) return 'intraday'
      if (!this.synthetic && (!this.chart || !this.chart.bars || !this.chart.bars.length)) return 'intraday'
      return 'daily'
    },
    intradaySourceText () {
      return label('quote_source', this.intraday?.source || 'tencent')
    },
    intradayUnavailableTag () {
      if (this.intradayState && !this.intradayState.available) return '分时接口待服务接通'
      return '当日暂无完整分时点'
    },
    intradayEmptyTitle () {
      if (this.intradayState && !this.intradayState.available) {
        return reasonText(this.intradayState.reasonCode) || '当日分时接口待当前服务接通'
      }
      return '当前暂无可绘制的当日完整分钟点'
    },
    intradayEmptyDesc () {
      if (this.intraday && this.intraday.reason) {
        return `原因：${reasonText(this.intraday.reason) || this.intraday.reason}`
      }
      if (this.intradayState && !this.intradayState.available) {
        return this.intradayState.detail || '当前运行的服务尚未开放当日分时接口；前端已就绪按股票读取完整分钟点并标明最近完整分钟，切换股票时自动重读。'
      }
      return '当前处于开盘前、停牌零成交或行情源尚未返回已完成分钟点，系统保留真实空态，不伪造走势线。'
    },
    bars () {
      return (this.chart?.bars || []).map(bar => ({ ...bar, up: Number(bar.close) >= Number(bar.open) }))
    },
    latestBar () {
      return this.bars.length ? this.bars[this.bars.length - 1] : null
    },
    levels () {
      const items = []
      if (this.priceCap) items.push({ key: 'cap', label: '价格上限', text: this.priceCap, value: Number(this.priceCap) })
      if (this.chart?.support) items.push({ key: 'support', label: '支撑', text: this.chart.support, value: Number(this.chart.support) })
      if (this.costPrice) items.push({ key: 'cost', label: '持仓成本', text: this.costPrice, value: Number(this.costPrice) })
      return items
    },
    range () {
      const values = this.bars.flatMap(bar => [Number(bar.high), Number(bar.low)]).concat(this.levels.map(level => level.value))
      const min = Math.min(...values)
      const max = Math.max(...values)
      const margin = (max - min) * 0.08 || 1
      return { min: min - margin, max: max + margin }
    },
    maxVolume () { return Math.max(...this.bars.map(bar => bar.volume), this.chart?.volume_baseline || 0) || 1 },
    candle () { return Math.max(3, (this.width - this.pad * 2 - 56) / Math.max(this.bars.length, 1) * 0.6) },
    priceTicks () {
      const steps = 4
      return Array.from({ length: steps + 1 }, (_, index) => {
        const value = this.range.min + (this.range.max - this.range.min) * index / steps
        return { y: this.y(value), label: value.toFixed(2) }
      })
    },
    plotWidth () { return this.width - this.pad * 2 - 48 },
    lunchX () { return this.pad + this.plotWidth * (120.5 / 241) },
    intradayPrevClose () {
      const raw = this.prevClose || this.intraday?.previous_close
      return raw ? Number(raw) : null
    },
    intradayLevels () {
      const items = []
      if (this.priceCap) items.push({ key: 'cap', label: '限价/上限', text: this.priceCap, value: Number(this.priceCap) })
      if (this.costPrice) items.push({ key: 'cost', label: '持仓成本', text: this.costPrice, value: Number(this.costPrice) })
      return items
    },
    intradayRange () {
      const prices = (this.intraday?.points || []).map(b => Number(b.price))
      if (this.intradayPrevClose !== null) prices.push(this.intradayPrevClose)
      for (const lv of this.intradayLevels) prices.push(lv.value)
      const min = Math.min(...prices)
      const max = Math.max(...prices)
      const margin = (max - min) * 0.12 || 0.05
      return { min: min - margin, max: max + margin }
    },
    intradayPlotted () {
      return (this.intraday?.points || []).map(pt => {
        const mapped = hmToSlot(pt.hm)
        if (!mapped) return null
        const xCoord = this.pad + this.plotWidth * (mapped.slot / 241)
        return {
          ...pt,
          slot: mapped.slot,
          session: mapped.session,
          rawMin: mapped.rawMin,
          x: xCoord,
          y: this.iy(Number(pt.price))
        }
      }).filter(Boolean)
    },
    intradaySegments () {
      const pts = this.intradayPlotted
      if (!pts.length) return []
      const segments = []
      let current = []
      for (let i = 0; i < pts.length; i++) {
        const pt = pts[i]
        const prev = i > 0 ? pts[i - 1] : null
        // Break segment across lunch break OR any missing-minute gap (> 1 min inside same session).
        const contiguous = prev && prev.session === pt.session && (pt.rawMin - prev.rawMin === 1)
        if (!contiguous && current.length) {
          segments.push(current)
          current = []
        }
        current.push(pt)
      }
      if (current.length) segments.push(current)
      return segments.map(group => {
        if (group.length === 1) {
          const p = group[0]
          return `M ${(p.x - 1.2).toFixed(1)} ${p.y.toFixed(1)} L ${(p.x + 1.2).toFixed(1)} ${p.y.toFixed(1)}`
        }
        return group.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
      })
    },
    lastIntradayPoint () {
      const pts = this.intradayPlotted
      return pts.length ? pts[pts.length - 1] : null
    },
    intradayTicks () {
      const steps = 4
      return Array.from({ length: steps + 1 }, (_, index) => {
        const value = this.intradayRange.min + (this.intradayRange.max - this.intradayRange.min) * index / steps
        return { y: this.iy(value), label: value.toFixed(2) }
      })
    },
    basisText () { return this.chart?.price_basis === 'unadjusted_synthetic' ? '固定演练合成日线（未复权，非真实行情）' : (this.chart?.price_basis || '—') },
    asOf () { return formatTime(this.chart?.as_of) }
  },
  watch: {
    securityKey () {
      this.manualMode = null
    },
    name () {
      this.manualMode = null
    }
  },
  methods: {
    formatTime,
    x (index) { return this.pad + 8 + (this.width - this.pad * 2 - 58) * (index + 0.5) / Math.max(this.bars.length, 1) },
    y (value) { const { min, max } = this.range; return this.priceBottom - (Number(value) - min) / (max - min) * (this.priceBottom - this.priceTop) },
    vy (volume) { return this.volumeBase - volume / this.maxVolume * (this.volumeBase - this.volumeTop) },
    iy (value) { const { min, max } = this.intradayRange; return this.intradayBottom - (Number(value) - min) / (max - min) * (this.intradayBottom - this.priceTop) }
  }
}
</script>
