<template>
  <div class="login-page">
    <div class="login-brand">
      <div class="login-mark"><img :src="`${assetRoot}slogo.png`" alt="" /></div>
      <div class="login-copy">
        <span class="login-kicker">QUANTHUS · 个人模拟盘</span>
        <h1>我的 A 股模拟盘工作台</h1>
        <p>每天打开先看今日待办、持仓与实时行情；在同一持久模拟账本里提交委托、撤单、登记成交并追加更正。</p>
      </div>
      <div class="login-facts">
        <span><a-icon type="stock" />真实证券身份与行情时间可核对</span>
        <span><a-icon type="audit" />委托、成交与更正逐笔留痕</span>
        <span><a-icon type="stop" />独立模拟资金，不连接实盘</span>
      </div>
    </div>
    <section class="login-panel">
      <div class="login-panel-head"><img :src="`${assetRoot}quantdinger-logo.png`" alt="QuantDinger" /><span>Product layer adapted for Quanthus</span></div>
      <div><h2>进入我的模拟盘</h2><p>登录后直达模拟账户工作台，接续昨日持仓与委托记录</p></div>
      <a-form-model layout="vertical" @submit.native.prevent="submit">
        <a-form-model-item label="用户名"><a-input v-model="username" size="large" autocomplete="username" data-testid="username"><a-icon slot="prefix" type="user" /></a-input></a-form-model-item>
        <a-form-model-item label="密码"><a-input-password v-model="password" size="large" autocomplete="current-password" data-testid="password"><a-icon slot="prefix" type="lock" /></a-input-password></a-form-model-item>
        <a-form-model-item v-if="mfaRequired" label="MFA 验证码"><a-input v-model="mfaCode" size="large" inputmode="numeric" maxlength="6" data-testid="mfa-code"><a-icon slot="prefix" type="safety" /></a-input></a-form-model-item>
        <a-alert v-if="error" :message="error" type="error" show-icon />
        <a-button type="primary" html-type="submit" size="large" block :loading="loading" data-testid="login-submit">进入我的模拟盘</a-button>
      </a-form-model>
      <p class="login-boundary"><a-icon type="info-circle" />模拟盘使用虚拟资金运行，所有委托与成交仅记入本平台模拟账本，不连接实盘。</p>
    </section>
  </div>
</template>

<script>
import { login } from '../api'
const errors = { invalid_credentials: '用户名或密码不正确', mfa_required: '请输入 MFA 验证码', invalid_mfa_code: 'MFA 验证码不正确', login_rate_limited: '尝试次数过多，请稍后再试' }
export default {
  data: () => ({ assetRoot: '/product-assets/', username: '', password: '', mfaCode: '', mfaRequired: false, loading: false, error: '' }),
  methods: {
    async submit () {
      this.error = ''
      if (!this.username || !this.password) { this.error = '请输入用户名和密码'; return }
      this.loading = true
      try {
        const result = await login(this.username, this.password, this.mfaCode)
        this.$emit('authenticated', result.user)
      } catch (error) {
        if (error.status === 428) this.mfaRequired = true
        this.error = errors[error.message] || '登录失败，请稍后重试'
      } finally { this.loading = false }
    }
  }
}
</script>
