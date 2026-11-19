// Shared account selection for the three P12 desktop pages.
import { listAccounts } from './client.js'
import { reasonText } from './format.js'

const STORAGE_KEY = 'quanthus.p12.account'

export default {
  data: () => ({ accounts: [], accountId: null, loadError: null, loading: false }),
  async created () {
    try {
      const result = await listAccounts()
      this.accounts = result.accounts
      const remembered = sessionStorage.getItem(STORAGE_KEY)
      const requested = this.$route.query.account
      const ids = this.accounts.map(item => item.account_id)
      this.accountId = [requested, remembered, ids[0]].find(id => id && ids.includes(id)) || null
      if (this.accountId) await this.refresh()
      else this.loadError = { reasonCode: 'ACCOUNT_NOT_ADMITTED', text: '当前用户没有可用的账户' }
    } catch (error) {
      this.fail(error)
    }
  },
  computed: {
    currentAccount () { return this.accounts.find(item => item.account_id === this.accountId) || null }
  },
  methods: {
    fail (error) {
      if (error.status === 401) this.$emit('session-expired')
      this.loadError = { reasonCode: error.reasonCode, text: reasonText(error.reasonCode) || '账户数据暂时无法读取', detail: error.detail }
    },
    async selectAccount (accountId) {
      this.accountId = accountId
      sessionStorage.setItem(STORAGE_KEY, accountId)
      await this.refresh()
    },
    async refresh () {
      this.loading = true
      this.loadError = null
      try { await this.load(this.accountId) } catch (error) { this.fail(error) } finally { this.loading = false }
    }
  }
}
