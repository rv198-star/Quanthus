<template>
  <div class="p12-chart" data-testid="p12-chart">
    <svg v-if="chart && chart.bars.length" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="`${name} 价格与成交量`">
      <g class="grid">
        <line v-for="tick in priceTicks" :key="'g' + tick.label" :x1="pad" :x2="width - pad" :y1="tick.y" :y2="tick.y" />
        <text v-for="tick in priceTicks" :key="'t' + tick.label" :x="width - pad + 6" :y="tick.y + 4">{{ tick.label }}</text>
      </g>
      <g class="candles">
        <g v-for="(bar, index) in bars" :key="bar.session" :class="bar.up ? 'up' : 'down'">
          <line :x1="x(index)" :x2="x(index)" :y1="y(bar.high)" :y2="y(bar.low)" />
          <rect :x="x(index) - candle / 2" :width="candle" :y="Math.min(y(bar.open), y(bar.close))" :height="Math.max(1, Math.abs(y(bar.open) - y(bar.close)))" />
        </g>
      </g>
      <g v-for="level in levels" :key="level.key" :class="['level', level.key]">
        <line :x1="pad" :x2="width - pad" :y1="y(level.value)" :y2="y(level.value)" />
        <text :x="pad + 4" :y="y(level.value) - 5">{{ level.label }} {{ level.text }}</text>
      </g>
      <g class="volume">
        <rect v-for="(bar, index) in bars" :key="'v' + bar.session" :x="x(index) - candle / 2" :width="candle" :y="vy(bar.volume)" :height="volumeBase - vy(bar.volume)" :class="bar.up ? 'up' : 'down'" />
        <line v-if="chart.volume_baseline" class="baseline" :x1="pad" :x2="width - pad" :y1="vy(chart.volume_baseline)" :y2="vy(chart.volume_baseline)" />
        <text v-if="chart.volume_baseline" :x="pad + 4" :y="vy(chart.volume_baseline) - 4">前 {{ chart.volume_baseline_window }} 日均量</text>
      </g>
      <text class="axis-date" :x="pad" :y="height - 4">{{ bars[0].session }}</text>
      <text class="axis-date" :x="width - pad" :y="height - 4" text-anchor="end">{{ bars[bars.length - 1].session }}</text>
    </svg>
    <div v-else class="p12-chart-empty">{{ emptyText }}</div>
    <p v-if="chart" class="p12-chart-note">价格口径：{{ basisText }} · 数据截至 {{ asOf }} · 图形坐标仅用于显示</p>
  </div>
</template>
<script>
import { formatTime } from '../format.js'

// Coordinates use Number() for drawing only; displayed values stay the server's decimal text.
export default {
  props: { chart: Object, name: String, priceCap: String, costPrice: String, emptyText: { type: String, default: '暂无合格价格数据' } },
  data: () => ({ width: 640, height: 300, pad: 14, priceTop: 16, priceBottom: 196, volumeTop: 214, volumeBase: 282 }),
  computed: {
    bars () {
      return (this.chart?.bars || []).map(bar => ({ ...bar, up: Number(bar.close) >= Number(bar.open) }))
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
    candle () { return Math.max(3, (this.width - this.pad * 2 - 40) / Math.max(this.bars.length, 1) * 0.6) },
    priceTicks () {
      const steps = 4
      return Array.from({ length: steps + 1 }, (_, index) => {
        const value = this.range.min + (this.range.max - this.range.min) * index / steps
        return { y: this.y(value), label: value.toFixed(2) }
      })
    },
    basisText () { return this.chart?.price_basis === 'unadjusted_synthetic' ? '合成数据，未复权' : (this.chart?.price_basis || '—') },
    asOf () { return formatTime(this.chart?.as_of) }
  },
  methods: {
    x (index) { return this.pad + 10 + (this.width - this.pad * 2 - 50) * (index + 0.5) / Math.max(this.bars.length, 1) },
    y (value) { const { min, max } = this.range; return this.priceBottom - (Number(value) - min) / (max - min) * (this.priceBottom - this.priceTop) },
    vy (volume) { return this.volumeBase - volume / this.maxVolume * (this.volumeBase - this.volumeTop) }
  }
}
</script>
