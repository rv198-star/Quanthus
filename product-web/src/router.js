import Vue from 'vue'
import VueRouter from 'vue-router'
import ResearchOverview from './views/ResearchOverview.vue'
import StrategyLibrary from './views/StrategyLibrary.vue'
import CandidateDetail from './views/CandidateDetail.vue'
import AttributionCenter from './views/AttributionCenter.vue'

Vue.use(VueRouter)

export default new VueRouter({
  mode: 'history',
  routes: [
    { path: '/', redirect: '/research' },
    { path: '/research', component: ResearchOverview, meta: { title: 'A 股研究' } },
    { path: '/candidates', component: StrategyLibrary, meta: { title: '候选验证' } },
    { path: '/candidates/:strategyId', component: CandidateDetail, meta: { title: '候选详情' } },
    { path: '/attribution', component: AttributionCenter, meta: { title: '归因分析' } },
    { path: '*', redirect: '/research' }
  ]
})
