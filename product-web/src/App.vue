<template>
  <LoginView v-if="!authenticated" @authenticated="onAuthenticated" />
  <div v-else class="app-shell">
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand">
        <img :src="`${assetRoot}quantdinger-logo-white.png`" alt="Quanthus" />
        <div><strong>Quanthus</strong><span>Research Platform</span></div>
      </div>
      <nav aria-label="产品导航">
        <router-link v-for="item in navigation" :key="item.path" :to="item.path" @click.native="mobileOpen = false">
          <a-icon :type="item.icon" /><span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-foot">
        <a href="/operator"><a-icon type="setting" /><span>系统管理</span></a>
        <div class="powered">Powered by QuantDinger</div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <button class="mobile-menu" aria-label="打开导航" @click="mobileOpen = !mobileOpen"><a-icon type="menu" /></button>
        <div class="page-identity"><span>A 股量化研究</span><b>{{ $route.meta.title }}</b></div>
        <div class="top-actions">
          <span class="boundary"><i></i>研究环境</span>
          <a-dropdown placement="bottomRight">
            <button class="user-button"><span>{{ initials }}</span><b>{{ user.username }}</b><a-icon type="down" /></button>
            <a-menu slot="overlay"><a-menu-item @click="logout"><a-icon type="logout" />退出登录</a-menu-item></a-menu>
          </a-dropdown>
        </div>
      </header>
      <main><router-view :catalog="catalog" :catalog-loading="loading" :user="user" @catalog-changed="refreshCatalog" /></main>
    </div>
    <button v-if="mobileOpen" class="drawer-mask" aria-label="关闭导航" @click="mobileOpen = false"></button>
  </div>
</template>

<script>
import LoginView from './views/LoginView.vue'
import { clearSession, loadCatalog, loadMe, loadSession } from './api'

export default {
  components: { LoginView },
  data: () => ({
    assetRoot: '/product-assets/',
    authenticated: Boolean(loadSession()),
    user: { username: '' },
    catalog: null,
    loading: false,
    mobileOpen: false,
    navigation: [
      { path: '/research', label: 'A 股研究', icon: 'fund' },
      { path: '/candidates', label: '候选验证', icon: 'experiment' },
      { path: '/attribution', label: '归因分析', icon: 'deployment-unit' }
    ]
  }),
  computed: {
    initials () { return (this.user.username || 'Q').slice(0, 2).toUpperCase() }
  },
  created () { if (this.authenticated) this.bootstrap() },
  methods: {
    async bootstrap () {
      this.loading = true
      try {
        const [user, catalog] = await Promise.all([loadMe(), loadCatalog()])
        this.user = user
        this.catalog = catalog
      } catch (error) {
        if (error.status === 401) this.logout()
        else this.$message.error('研究目录暂时无法读取')
      } finally { this.loading = false }
    },
    async refreshCatalog () {
      try { this.catalog = await loadCatalog() } catch (_) { this.$message.error('候选队列暂时无法刷新') }
    },
    onAuthenticated (user) { this.authenticated = true; this.user = user; this.bootstrap() },
    logout () { clearSession(); this.authenticated = false; this.catalog = null }
  }
}
</script>
