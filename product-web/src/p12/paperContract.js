// Paper-trading contract extension (service/CONTRACT.md + PAPER-PRODUCT.md intraday contract).
// Kept separate from contract.generated.js so the generated desktop v1 schema remains verbatim.

export const PAPER_QUOTE_CONTRACT_VERSION = 'p12-paper-quote-v1'
export const PAPER_EXEC_RULE_VERSION = 'p12-paper-exec-v1'

export const PAPER_OPERATIONS = Object.freeze({
  'p12.market.session': {
    method: 'GET',
    path: '/api/p12/v1/market/session'
  },
  'p12.securities.quote': {
    method: 'GET',
    path: '/api/p12/v1/securities/{code}/quote'
  },
  'p12.securities.intraday': {
    method: 'GET',
    path: '/api/p12/v1/securities/{code}/intraday'
  },
  'p12.securities.identity': {
    method: 'GET',
    path: '/api/p12/v1/securities/{code}/identity'
  },
  'p12.sim_orders.list': {
    method: 'GET',
    path: '/api/p12/v1/accounts/{account_id}/sim-orders'
  },
  'p12.sim_orders.submit': {
    method: 'POST',
    path: '/api/p12/v1/accounts/{account_id}/sim-orders'
  },
  'p12.sim_orders.cancel': {
    method: 'POST',
    path: '/api/p12/v1/accounts/{account_id}/sim-orders/{order_id}/cancel'
  },
  'p12.funding.configure': {
    method: 'POST',
    path: '/api/p12/v1/accounts/{account_id}/funding-rules'
  }
})

export const PAPER_REASON_TEXTS = Object.freeze({
  INSUFFICIENT_CASH: '可用模拟资金不足，无法覆盖本次委托金额',
  NON_ROUND_LOT: '买入数量须为 100 股（1 手）的整数倍',
  SELL_NOT_ALLOWED_T1: 'A 股实行 T+1 交收，当日买入股数下一交易日才可卖出',
  MARKET_CLOSED: '当前处于休市或非连续竞价时段，暂不撮合成交',
  SECURITY_SUSPENDED: '该股票当前停牌，无法撮合成交',
  PRICE_OUT_OF_BOUNDS: '委托价格超出当日有效涨跌停或合理价格边界',
  QUOTE_UNAVAILABLE: '暂未获取到有效实时行情快照，无法校验撮合价格',
  BELOW_LIMIT: '当前最新价未达到你的限价条件，暂未成交',
  DUPLICATE_ORDER: '同一委托编号已提交过，未重复扣款或下单',
  SIM_ORDER_SERVICE_UNAVAILABLE: '当前服务尚未启用盘中模拟委托接口，未生成挂单或改动账户',
  QUOTE_SERVICE_UNAVAILABLE: '当前服务尚未接通实时报价接口，暂无法查询盘中行情快照',
  INTRADAY_SERVICE_UNAVAILABLE: '当日分时接口待当前服务接通，暂不绘制模拟走势',
  FUNDING_WRITE_NOT_AVAILABLE: '当前服务仅开放固定桶规则读取，个人资金参数保存接口待后端开放；本次仅完成本地测算预览，未改动已生效账户规则'
})

export const PAPER_LABELS = Object.freeze({
  market_state: {
    trading: '盘中连续竞价',
    lunch_break: '午间休市（11:30–13:00）',
    call_auction_close: '收盘集合竞价（14:57–15:00）',
    pre_open: '开盘前准备',
    closed: '已收盘',
    holiday: '节假日休市'
  },
  quote_status: {
    live: '实时有效',
    closed: '已收盘价',
    halt: '停牌 / 零成交',
    stale: '报价延迟',
    unavailable: '暂无可用报价'
  },
  quote_source: {
    tencent: '腾讯实时行情',
    tencent_m1: '腾讯当日分钟行情',
    'tencent-m1': '腾讯当日分钟行情',
    sina: '新浪实时行情',
    astock: 'astock 行情适配器',
    'astock-tencent': 'astock · 腾讯行情',
    'astock-sina': 'astock · 新浪行情',
    eastmoney: '东财延迟行情',
    synthetic: '演练合成序列',
    none: '无行情源'
  },
  sim_order_status: {
    open: '挂单待成交',
    partially_filled: '部分成交',
    filled: '全部成交',
    cancelled: '已撤单',
    rejected: '已拒绝'
  },
  record_source: {
    sim_matched: '模拟撮合成交',
    user_reported: '我自主登记的成交',
    self_directed: '自主模拟委托（非系统建议）',
    system_plan: '系统盘后计划'
  }
})

export const MISSING_FIELD_LABELS = Object.freeze({
  opportunity_policy_version: '候选选股策略正式版本号',
  cross_day_identity: '跨交易日机会追踪标识',
  composition_use_admission: '趋势与仓位专家组合的日常使用准入',
  daily_input_binding: '每日盘后合格日线数据源绑定',
  pit_availability: '收盘截止时点（PIT）数据可用性核验',
  account_limit_values: '个人账户单股/行业/总仓位风险阈值定版',
  tradeable_cash_basis: '可交易现金与费用预留计算口径',
  composition_digest: '策略与资金组合版本锁定摘要',
  manual_window: '人工执行有效时间窗口定义',
  fee_scope_rules: '佣金与税费分摊口径',
  daily_trigger: '每日收盘后自动生成计划任务',
  restart_drill: '服务重启与跨日恢复演练验收'
})

// Clickable A-share lookup shortcuts (codes & exchanges verified in evidence/live-quotes.md; zero hardcoded prices).
export const REAL_SECURITY_SHORTCUTS = Object.freeze([
  { code: '600000', hintName: '浦发银行', exchange: '上交所' },
  { code: '600519', hintName: '贵州茅台', exchange: '上交所' },
  { code: '000002', hintName: '万科A', exchange: '深交所' },
  { code: '300001', hintName: '特锐德', exchange: '深交所' }
])
