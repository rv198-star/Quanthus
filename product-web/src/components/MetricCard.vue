<template><div class="metric-card"><div><span>{{ label }}</span><a-tooltip v-if="hint" :title="hint"><a-icon type="info-circle" /></a-tooltip></div><strong :class="tone">{{ value }}</strong><small>{{ detail }}</small></div></template>
<script>export default { props: { label: String, value: String, detail: String, hint: String, tone: String } }</script>
