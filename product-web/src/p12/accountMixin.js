// Shared account selection for the three P12 desktop pages.
import { listAccounts } from './client.js'
import { reasonText } from './format.js'

const STORAGE_KEY = 'quanthus.p12.account'
const EXPLICIT_KEY = 'quanthus.p12.account:explicit'

export default {
  data: () => ({ accounts: [], accountId: null, loadError: null, loading: false }),
  async created () {
    try {
      const result = await listAccounts()
      this.accounts = result.accounts
      const remembered = sessionStorage.getItem(STORAGE_KEY)
      const explicitChoice = sessionStorage.getItem(EXPLICIT_KEY) === '1'
      const requested = this.$route.query.account
      const ids = this.accounts.map(item => item.account_id)
      // Prefer a non-synthetic personal paper account by default when available.
      const simDefault = this.accounts.find(item => !item.synthetic || item.mode === 'simulation')?.account_id
      const rememberedEntry = this.accounts.find(item => item.account_id === remembered)
      const validRemembered = (rememberedEntry && (!rememberedEntry.synthetic || explicitChoice || !simDefault))
        ? remembered
        : null
      this.accountId = [requested, validRemembered, simDefault, ids[0]].find(id => id && ids.includes(id)) || null
      if (this.accountId) sessionStorage.setItem(STORAGE_KEY, this.accountId)
      if (this.accountId) await this.refresh()
      else this.loadError = { reasonCode: 'ACCOUNT_NOT_ADMITTED', text: '当前用户没有可用的账户' }
    } catch (error) {
      this.fail(error)
    }
  },
  computed: {
    currentAccount () { return this.accounts.find(item => item.account_id === this.accountId) || null },
    personalPaperAccount () { return this.accounts.find(item => !item.synthetic || item.mode === 'simulation') || null }
  },
  methods: {
    fail (error) {
      if (error.status === 401) this.$emit('session-expired')
      this.loadError = { reasonCode: error.reasonCode, text: reasonText(error.reasonCode) || '账户数据暂时无法读取', detail: error.detail }
    },
    async selectAccount (accountId) {
      this.accountId = accountId
      sessionStorage.setItem(STORAGE_KEY, accountId)
      sessionStorage.setItem(EXPLICIT_KEY, '1')
      if (this.$route && this.$router && this.$route.query.account !== accountId) {
        this.$router.replace({ path: this.$route.path, query: { ...this.$route.query, account: accountId } }).catch(() => {})
      }
      await this.refresh()
    },
    async switchToPersonalAccount () {
      if (this.personalPaperAccount && this.personalPaperAccount.account_id !== this.accountId) {
        await this.selectAccount(this.personalPaperAccount.account_id)
      }
    },
    async refresh () {
      this.loading = true
      this.loadError = null
      try { await this.load(this.accountId) } catch (error) { this.fail(error) } finally { this.loading = false }
    }
  }
}
