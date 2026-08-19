<template>
  <div class="page-wrap" data-testid="attribution-page">
    <div class="page-heading"><div><span class="eyebrow">ATTRIBUTION</span><h1>归因分析</h1><p>只展示经过发布与校验的组件贡献证据。</p></div><div class="heading-actions"><a-button icon="reload" :loading="loading" @click="load">刷新目录</a-button></div></div>
    <a-alert v-if="error" type="error" show-icon message="归因发布目录暂时不可用" />
    <section v-else-if="!loading && releases.length === 0" class="panel attribution-empty"><div class="empty-icon"><a-icon type="deployment-unit" /></div><h2>暂无正式归因发布</h2><p>当前目录没有可作为策略结论的正式产物。合成夹具仅用于合同和界面验证，因此不会在这里冒充研究结论。</p><div class="empty-boundaries"><span><a-icon type="check-circle" />正式发布才展示</span><span><a-icon type="stop" />合成结果不冒充结论</span><span><a-icon type="audit" />摘要与来源校验</span></div></section>
    <section v-else class="panel release-list"><div class="panel-head"><div><h2>已发布归因</h2><p>{{ releases.length }} 个服务端发布</p></div></div><div v-for="item in releases" :key="item.release_id" class="release-row"><span class="strategy-icon"><a-icon type="branches" /></span><div><b>{{ item.release_id }}</b><small>{{ item.workspace_path }}</small></div><span class="status-tag accepted">已发布</span></div></section>
    <section class="attribution-method"><div><span class="step">1</span><b>策略回放</b><small>冻结输入与版本</small></div><i></i><div><span class="step">2</span><b>组件干预</b><small>对照与交互效应</small></div><i></i><div><span class="step">3</span><b>发布校验</b><small>摘要、状态与来源</small></div></section>
  </div>
</template>
<script>
import { loadAttributionCatalog } from '../api'
export default { data: () => ({ releases: [], loading: false, error: false }), created () { this.load() }, methods: { async load () { this.loading = true; this.error = false; try { const result = await loadAttributionCatalog(); this.releases = result.releases || [] } catch (_) { this.error = true } finally { this.loading = false } } } }
</script>
