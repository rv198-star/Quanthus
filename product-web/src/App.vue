<template>
  <LoginView v-if="!authenticated" @authenticated="onAuthenticated" />
  <div v-else class="app-shell">
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand">
        <img :src="`${assetRoot}quantdinger-logo-white.png`" alt="Quanthus" />
        <div><strong>Quanthus</strong><span>个人模拟盘</span></div>
      </div>
      <nav aria-label="产品导航">
        <span class="nav-group">{{ primaryGroup.label }}</span>
        <router-link v-for="item in primaryGroup.items" :key="item.path" :to="item.path" :exact="item.exact" @click.native="mobileOpen = false">
          <a-icon :type="item.icon" /><span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-foot">
        <button type="button" class="research-toggle" :aria-expanded="String(showResearchNav)" data-testid="toggle-research-nav" @click="researchOpen = !showResearchNav">
          <span>策略研究（研究人员）</span>
          <a-icon :type="showResearchNav ? 'up' : 'right'" />
        </button>
        <div v-show="showResearchNav" class="research-subnav">
          <router-link v-for="item in secondaryGroup.items" :key="item.path" :to="item.path" @click.native="mobileOpen = false">
            <a-icon :type="item.icon" /><span>{{ item.label }}</span>
          </router-link>
          <a href="/operator"><a-icon type="setting" /><span>系统管理</span></a>
        </div>
        <div class="powered">Powered by QuantDinger</div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <button class="mobile-menu" aria-label="打开导航" @click="mobileOpen = !mobileOpen"><a-icon type="menu" /></button>
        <div class="page-identity"><span>{{ isAccountArea ? '我的模拟盘' : '策略研究（内部参考）' }}</span><b>{{ $route.meta.title }}</b></div>
        <div class="top-actions">
          <span v-if="isAccountArea" class="boundary account" data-testid="p12-global-sim-badge"><i></i>模拟账户运行 · 虚拟资金，不发实盘指令</span>
          <span v-else class="boundary"><i></i>内部研究环境 · 非个人操作建议</span>
          <a-dropdown placement="bottomRight">
            <button class="user-button"><span>{{ initials }}</span><b>{{ user.username }}</b><a-icon type="down" /></button>
            <a-menu slot="overlay"><a-menu-item @click="logout"><a-icon type="logout" />退出登录</a-menu-item></a-menu>
          </a-dropdown>
        </div>
      </header>
      <main><router-view :catalog="catalog" :catalog-loading="loading" :user="user" @catalog-changed="refreshCatalog" @session-expired="logout" /></main>
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
    researchOpen: false,
    primaryGroup: {
      label: '我的账户 · 模拟盘',
      items: [
        { path: '/p12', label: '工作台', icon: 'dashboard', exact: true },
        { path: '/p12/records', label: '委托与成交记录', icon: 'file-text' },
        { path: '/p12/funding', label: '资金与规则', icon: 'wallet' }
      ]
    },
    secondaryGroup: {
      label: '策略研究',
      items: [
        { path: '/research', label: 'A 股研究', icon: 'fund' },
        { path: '/candidates', label: '候选验证', icon: 'experiment' },
        { path: '/attribution', label: '归因分析', icon: 'deployment-unit' }
      ]
    }
  }),
  computed: {
    initials () { return (this.user.username || 'Q').slice(0, 2).toUpperCase() },
    isAccountArea () { return this.$route.meta.area === 'p12' },
    showResearchNav () { return this.researchOpen || !this.isAccountArea }
  },
  watch: {
    '$route.path' () {
      if (!this.isAccountArea && !this.catalog && this.authenticated && !this.loading) {
        this.ensureCatalog()
      }
    }
  },
  created () { if (this.authenticated) this.bootstrap() },
  methods: {
    async bootstrap () {
      this.loading = true
      try {
        const user = await loadMe()
        this.user = user
        if (!this.isAccountArea) await this.ensureCatalog()
      } catch (error) {
        if (error.status === 401) this.logout()
        else this.$message.error('账户登录状态暂时无法核验')
      } finally { this.loading = false }
    },
    async ensureCatalog () {
      try {
        this.catalog = await loadCatalog()
      } catch (error) {
        if (error.status === 401) this.logout()
        else if (!this.isAccountArea) this.$message.error('研究目录暂时无法读取')
      }
    },
    async refreshCatalog () {
      try { this.catalog = await loadCatalog() } catch (_) { this.$message.error('候选队列暂时无法刷新') }
    },
    onAuthenticated (user) {
      this.authenticated = true
      this.user = user
      if (this.$route.path === '/' || this.$route.path === '/research') {
        this.$router.replace('/p12').catch(() => {})
      }
      this.bootstrap()
    },
    logout () { clearSession(); this.authenticated = false; this.catalog = null }
  }
}
</script>
