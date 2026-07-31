const componentLabels: Record<string, string> = {
  "allocator.capital": "资金分配器",
  "expert.trend": "趋势专家",
  "feature.breakout": "突破特征组",
  "feature.noise_filter": "噪声过滤特征",
  "risk.extension": "追高风险门",
  "router.market_state": "市场状态路由",
  "strategy.root": "合成策略根",
};

const componentDescriptions: Record<string, string> = {
  "allocator.capital": "拓扑中可见，但当前没有声明可执行的归因干预。",
  "expert.trend": "虚构的趋势专家，并声明了暂不发表意见时的处理方式。",
  "feature.breakout": "虚构的突破形态证据组。",
  "feature.noise_filter": "用于演示负面效果的虚构过滤特征。",
  "risk.extension": "用于演示避免损失与机会成本的虚构风险门。",
  "router.market_state": "虚构的市场状态路由，并声明了中性路由方式。",
  "strategy.root": "只用于验证归因链路的虚构策略根。",
};

const metricLabels: Record<string, string> = {
  capital_utilization: "资金利用率",
  loss_exposure: "损失暴露",
  max_drawdown: "最大回撤",
  opportunity_cost: "机会成本",
  total_return: "总收益率",
  win_rate: "胜率",
};

const conditionLabels: Record<string, string> = {
  "state.range_bound": "震荡状态",
  "state.trending": "趋势状态",
};

const interventionLabels: Record<string, string> = {
  abstain_trend_expert: "趋势专家暂不发表意见",
  bypass_extension_risk_gate: "绕过追高风险门",
  mask_breakout_feature_group: "屏蔽突破特征组（不重新训练）",
  mask_noise_filter: "屏蔽噪声过滤特征（不重新训练）",
  neutralize_market_state_route: "使用中性市场路由",
};

const operationLabels: Record<string, string> = {
  abstain: "暂不发表意见",
  bypass_policy_gate: "绕过策略风险门",
  mask_without_retrain: "屏蔽且不重新训练",
  neutralize_native_route: "使用原生中性路由",
};

const artifactTerms: Record<string, string> = {
  accounting: "账务归集",
  association: "相关关系",
  confirmatory: "验证性分析",
  counterfactual_replay: "冻结历史全量重放",
  declared_pipeline_component_reliance: "已声明流程对组件的依赖",
  development: "开发样本",
  no_strategy_action_synthetic_fixture: "不产生策略动作",
  "no_cross_module_ranking:synthetic_fixture_v1": "合成样例不进行跨模块排名",
  "not_required:synthetic_contract_fixture_v1": "合同样例不要求",
  primary_metric_dominance_with_materiality_v1: "主指标在重要性阈值下同向占优",
  sensitivity: "敏感性分析",
  synthetic_fixture_no_strategy_action_v1: "合成样例不产生策略动作",
  "synthetic-pit-a-share-six-month-v1": "合成 A 股六个月样本（非真实数据）",
  synthetic_fixture_not_strategy_evidence: "合成合同样例（非策略证据）",
  "All values are fabricated and prove contracts only.":
    "所有数值均为编造，仅用于验证归因合同。",
  "Both native interventions are compatible.": "两个原生干预可以共同执行。",
  "Fail if either intervention changes the other fallback.":
    "任一干预改变另一项的兜底语义时，联合实验立即失败。",
  "No live or historical strategy inference is permitted.":
    "不得使用该合成样例推断实盘或历史策略。",
  "Stop publication when any bound identity or required evidence differs.":
    "任何绑定身份或必要证据发生变化时停止发布。",
  "The frozen aggregator handles one native expert abstention.":
    "冻结聚合器按原规则处理一次专家暂不发表意见。",
  "The paired replay uses one frozen synthetic context.":
    "成对重放使用同一套冻结的合成上下文。",
  "Only this strategy policy gate is bypassed; invariants remain active.":
    "只绕过该策略风险门，其余系统约束保持有效。",
  "Regenerate native decisions with the declared missing-group mask.":
    "按已声明的缺失组掩码重新生成原生决策。",
  "Use only the exact strategy-native InterventionSpec bound by its digest.":
    "只使用摘要已绑定的策略原生干预定义。",
  "Use the frozen native neutral route; do not delete state labels.":
    "使用冻结的原生中性路由，不删除状态标签。",
};

const contextLabels: Record<string, string> = {
  capital: "初始资金",
  costs: "交易成本",
  data_snapshot: "数据快照",
  eligible_universe: "可选证券范围",
  execution_assumptions: "执行假设",
  matcher: "成交撮合",
  policy: "策略政策",
  seeds: "随机种子",
  settlement: "结算规则",
};

export function displayComponentLabel(componentId: string, fallback?: string) {
  return componentLabels[componentId] ?? fallback ?? componentId;
}

export function displayComponentDescription(componentId: string, fallback: string) {
  return componentDescriptions[componentId] ?? displayArtifactTerm(fallback);
}

export function displayMetricLabel(metricId: string, fallback?: string) {
  return metricLabels[metricId] ?? fallback ?? metricId;
}

export function displayConditionLabel(conditionId: string, fallback?: string) {
  return conditionLabels[conditionId] ?? fallback ?? conditionId;
}

export function displayInterventionLabel(interventionId: string) {
  return interventionLabels[interventionId] ?? interventionId;
}

export function displayOperationLabel(operation: string) {
  return operationLabels[operation] ?? operation;
}

export function displayArtifactTerm(value: string) {
  return artifactTerms[value] ?? value;
}

export function displayContextLabel(value: string) {
  return contextLabels[value] ?? value;
}

export function displayBoolean(value: boolean) {
  return value ? "是" : "否";
}

export function displayFormattedValue(value: string) {
  return value.replace(/^CNY\s+/, "人民币 ").replace(/\s+pp$/, " 个百分点");
}
