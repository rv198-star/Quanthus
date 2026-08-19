<template><div ref="chart" class="equity-chart" role="img" aria-label="策略真实回放权益曲线"></div></template>
<script>
import * as echarts from 'echarts'
export default {
  props: { strategy: { type: Object, default: null }, equityCurve: { type: Array, default: () => [] } },
  data: () => ({ chart: null, observer: null }),
  watch: { strategy: 'renderChart', equityCurve: 'renderChart' },
  mounted () { this.chart = echarts.init(this.$refs.chart); this.observer = new ResizeObserver(() => this.chart.resize()); this.observer.observe(this.$refs.chart); this.renderChart() },
  beforeDestroy () { this.observer?.disconnect(); this.chart?.dispose() },
  methods: {
    renderChart () {
      if (!this.chart || !this.strategy) return
      const rows = this.equityCurve || []
      if (!rows.length) return
      const initial = Number(rows[0].system_equity)
      const dates = rows.map(row => row.market_date)
      const curve = rows.map(row => +(Number(row.system_equity) / initial * 100).toFixed(2))
      this.chart.setOption({ animation: false, grid: { top: 18, right: 18, bottom: 42, left: 52 }, tooltip: { trigger: 'axis', valueFormatter: value => `${Number(value).toFixed(2)}` }, dataZoom: [{ type: 'inside' }], xAxis: { type: 'category', data: dates, boundaryGap: false, axisLine: { lineStyle: { color: '#dfe4ea' } }, axisLabel: { color: '#7a8698', formatter: value => value.slice(0, 7) } }, yAxis: { type: 'value', scale: true, axisLabel: { color: '#7a8698' }, splitLine: { lineStyle: { color: '#edf0f4' } } }, series: [{ name: this.strategy.name, type: 'line', data: curve, smooth: false, showSymbol: false, lineStyle: { width: 2, color: '#2878ff' }, areaStyle: { color: 'rgba(40,120,255,.08)' } }] })
    }
  }
}
</script>
