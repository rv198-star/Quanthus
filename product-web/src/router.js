import Vue from 'vue'
import VueRouter from 'vue-router'
import ResearchOverview from './views/ResearchOverview.vue'
import StrategyLibrary from './views/StrategyLibrary.vue'
import CandidateDetail from './views/CandidateDetail.vue'
import AttributionCenter from './views/AttributionCenter.vue'
import P12Workbench from './p12/views/P12Workbench.vue'
import P12Records from './p12/views/P12Records.vue'
import P12FundingRules from './p12/views/P12FundingRules.vue'

Vue.use(VueRouter)

export default new VueRouter({
  mode: 'history',
  routes: [
    { path: '/', redirect: '/p12' },
    { path: '/p12', component: P12Workbench, meta: { title: '工作台', area: 'p12' } },
    { path: '/p12/records', component: P12Records, meta: { title: '委托与成交记录', area: 'p12' } },
    { path: '/p12/funding', component: P12FundingRules, meta: { title: '资金与规则', area: 'p12' } },
    { path: '/research', component: ResearchOverview, meta: { title: 'A 股研究' } },
    { path: '/candidates', component: StrategyLibrary, meta: { title: '候选验证' } },
    { path: '/candidates/:strategyId', component: CandidateDetail, meta: { title: '候选详情' } },
    { path: '/attribution', component: AttributionCenter, meta: { title: '归因分析' } },
    { path: '*', redirect: '/p12' }
  ]
})
