<template>
  <div class="login-page">
    <div class="login-brand">
      <div class="login-mark"><img :src="`${assetRoot}slogo.png`" alt="" /></div>
      <div class="login-copy">
        <span class="login-kicker">QUANTHUS RESEARCH</span>
        <h1>A 股策略研究工作台</h1>
        <p>从研究假设、PIT 数据到可审计回测，集中查看策略证据与边界。</p>
      </div>
      <div class="login-facts">
        <span><a-icon type="safety-certificate" />PIT 语义</span>
        <span><a-icon type="audit" />证据可追溯</span>
        <span><a-icon type="stop" />不连接实盘</span>
      </div>
    </div>
    <section class="login-panel">
      <div class="login-panel-head"><img :src="`${assetRoot}quantdinger-logo.png`" alt="QuantDinger" /><span>Product layer adapted for Quanthus</span></div>
      <div><h2>登录研究平台</h2><p>使用平台账户继续</p></div>
      <a-form-model layout="vertical" @submit.native.prevent="submit">
        <a-form-model-item label="用户名"><a-input v-model="username" size="large" autocomplete="username" data-testid="username"><a-icon slot="prefix" type="user" /></a-input></a-form-model-item>
        <a-form-model-item label="密码"><a-input-password v-model="password" size="large" autocomplete="current-password" data-testid="password"><a-icon slot="prefix" type="lock" /></a-input-password></a-form-model-item>
        <a-form-model-item v-if="mfaRequired" label="MFA 验证码"><a-input v-model="mfaCode" size="large" inputmode="numeric" maxlength="6" data-testid="mfa-code"><a-icon slot="prefix" type="safety" /></a-input></a-form-model-item>
        <a-alert v-if="error" :message="error" type="error" show-icon />
        <a-button type="primary" html-type="submit" size="large" block :loading="loading" data-testid="login-submit">进入工作台</a-button>
      </a-form-model>
      <p class="login-boundary"><a-icon type="info-circle" />当前开放研究证据读取与受控复核，不连接实盘。</p>
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
