# 个股阶段第二组 development milestone：量化 / 短线操作 / 基金视角评审

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | not-applicable |
| Work item | `P3-STAGE-001-H` / #245 |
| Reviewed closeout | `../../config/stage-learning/second-group-development-closeout.v1.json` |
| Base Job | `stage_45493c2eae724023a1a1a626dd515a95` |
| Supplement Job | `stage_5dad4fb7923d44e7b135461e2f60ec75` |
| Evidence tier | development-only `SYNTHETIC_EXPERT_CONSENSUS`; not `HUMAN_EXPERT_REFERENCE` |
| Provenance | 同一助手按量化、短线操作、基金三个独立评审视角分别审查；当前 workspace 无独立 agent provider，不能声称三位真人或三个独立 Agent 签字 |
| Overall decision | `PASS_WITH_SCOPE_REDUCTION`：接受 mixed development closeout；只保留 Stage 10/11 离散 development candidate，Stage 5/6/7 保留编号但暂停离散化，继续以连续 Facts 为权威研究基线 |

## 1. 事实底座

本评审不重新搜索参数，也不读取新的市场分区。第二组的最终 development 证据是：

- Stage 10：原冻结 9 组网格中形成 development candidate：更深 `initial_low_support_candidate`、连续 5 个交易日未收复、当前低于该支撑至少 2%。
- Stage 11：在冻结 Stage 10 前提下，补样后形成 development candidate：同一失败支撑仍未收复，且从 post-break running low 反弹至少 5%。
- Stage 6：ambiguous reference 从首轮 9 个补到 19 个，sample-sufficiency blocker 已消失，但原 Stage-5/6 联合网格仍无任何候选通过 unchanged hard gates。
- Stage 5：与 Stage 6 保持独立编号，但当前联合搜索仍未形成可冻结离散 candidate。
- Stage 7：首轮已有 42 positive / 58 negative / 20 ambiguous references；无 sample blocker，但 9 个冻结组合仍出现 positive coverage 不足、ambiguous over-assertion 与相对 R0 F1 退化，因此保留真实 INCONCLUSIVE。
- 所有 Run 只读 2020-2021 development；2022-2024 未 reread，2025 permanent blind 未触碰。

评审的第一原则是接受上述混合结果，而不是为了凑齐五个标签继续追参。

## 2. 量化视角

### 2.1 结论

**通过 mixed closeout；Stage 10/11 可进入下一层设计，Stage 5/6/7 不应继续在当前版本调参。**

Stage 10/11 至少满足“有限网格 + T-only reference + hard gates + 不看 future outcome”的可辨识性开发要求。尤其 Stage 11 在补样后拥有 200 个 contexts，其中 52 definite positive、137 definite negative、11 ambiguous，reference sufficiency 已过原门槛，因此 5% rebound 不再是因为样本不足而偶然产生的唯一可行点。

Stage 5/6 的结论相反：Stage 6 补样已经排除了 ambiguous reference 数量不足这一工程原因，联合网格仍无合格候选，应解释为**当前特征/语义组合不可稳定辨识**，而不是继续扩大搜索空间。Stage 7 同理，已经有足够 reference，却同时暴露 coverage、ambiguity 与 non-inferiority 问题，继续向结果方向增加特征或阈值会显著提高过拟合风险。

### 2.2 要求

- Stage 10/11 后续任何正式化都必须保留连续 Facts：support role、break time、unreclaimed age、distance below support、reclaim、post-break rebound；离散 stage 只是 projection。
- 5/6/7 当前版本不得因为“业务上想要完整十二状态”而进入 R2/更复杂模型。
- 不允许用策略收益为 5/6/7 反向选择标签定义；若以后重新开版本，必须先有新的语义/事实假设，再冻结试验。
- Stage 10 的 `5 sessions + 2%` 与 Stage 11 的 `5% rebound` 只作为 development candidate，不声称跨市场时期稳定。

量化 disposition：`PASS_WITH_SCOPE_REDUCTION`。

## 3. 短线操作视角

### 3.1 Stage 10

当前定义比“一跌破就算结构失效”明显更符合短线结构判断。一次刺穿经常是假动作，而“更深的事前支撑 + 5 日未收复 + 2% 有效破位”提供了时间和空间双确认。

需要强调：**Stage 10 是结构失效确认，不是卖出指令。** 对极短周期交易者，5 日确认可能偏慢，但这正说明它描述的是结构状态，而不是瞬时止损触发器。实际操作层可以有更早的风险动作，但不能回过头把更早动作冒充 Stage 10 已确认。

### 3.2 Stage 11

“确认失效后、同支撑仍未收复、从破位低点反弹至少 5%”在短线语义上可辨认，也比单纯“涨了 5%”合理。5% 位于前一 profile 的实际未收复反弹分布中部附近，既避免把小波动都叫反抽，也不会等到极端反弹才确认。

Stage 11 一旦 reclaim 原失败支撑就必须结束；后续若进入修复，应切换到新的修复语义，而不是继续挂着 11。

### 3.3 Stage 5/6/7

当前结果支持**保留名字、暂停硬标签**。短线语义上，5/6 很容易同时存在：上升推进背景中正在回踩；而现有联合规则只靠 extension / drawdown / support intact 仍不足以稳定区分。Stage 7 仅凭“跌深 + 修复比例”也还不够，真实交易判断通常还会看修复是否重新夺回中间结构、是否形成新的 higher low、量能是否配合等，但这些不能在本轮结果出来后临时加入。

短线 disposition：Stage 10/11 `PASS_FOR_DEVELOPMENT_USE`; Stage 5/6/7 `PARK_DISCRETE_LABEL_KEEP_CONTINUOUS_EVIDENCE`。

## 4. 基金 / 中期结构视角

### 4.1 Stage 10 的价值最高

对基金或中期仓位管理，Stage 10 的“关键支撑持续失效”比短线的单日破位更有意义，因为它可以作为风险背景或结构恶化证据。5 日确认虽然对短线略慢，对中期资金反而合理；2% depth 也应被理解为候选的结构确认幅度，而不是固定止损线。

基金侧建议后续优先把 Stage 10 依赖的 F9 连续链条产品化，而不是先推广一个裸的 `stage_code=10`。

### 4.2 Stage 11 只能是辅助状态

基金视角下，5% 未收复反弹有解释价值，但不应成为独立加仓/减仓信号。它更适合表示：此前结构已经失败，但价格正在进行技术性恢复，原失效支撑尚未重夺。它可帮助解释“跌势中的反弹”，但不能替代基本面、估值或更长期趋势判断。

### 4.3 Stage 5/6/7 暂时连续化更好

对中期资金，趋势推进、正常回撤、修复重建本身是有价值的概念，但当前离散阈值没有得到足够证据支持。相比强行输出 5/6/7，直接消费 maximum extension、current drawdown、damage depth、repair fraction、support relation 等连续 Facts 更符合组合管理需求，也与 `prefer_continuous_structure_facts` 的既有结论一致。

基金 disposition：`PASS_WITH_CONTINUOUS_FACTS_PRIORITY`。

## 5. 三方共同结论

三方一致同意：

1. **第二组 development tuning 在当前版本停止。** 不再补第三轮 reference、不降 hard gates、不扩网格。
2. **Stage 10 / Stage 11 保留为 development candidate。** 其参数分别冻结为 `5 sessions + 2% support depth` 与 `5% unreclaimed rebound`。
3. **Stage 5 / Stage 6 / Stage 7 不删除、不合并，但暂停离散 candidate。** 当前状态为 `INCONCLUSIVE_PARKED`，连续 Facts 继续保留。
4. 不把 mixed closeout 解释成“第二组 5 个标签通过了 2 个，所以准确率 40%”；每个状态是不同语义命题，结论必须逐项表达。
5. 下一阶段若继续，应优先正式化 Stage 10/11 所依赖的连续 F9 事实与可审计 projection；5/6/7 只有在新的事前语义假设出现时才可开新版本。
6. 本轮不打开 2022-2024 fresh reuse、2025、QUALIFIED、daily、consumer、strategy、paper/live 或交易权限。

## 6. 评审后的建议路线

建议将当前第二组 milestone 记为：

`MIXED DEVELOPMENT CLOSEOUT / PASS WITH SCOPE REDUCTION`

下一候选工作不是继续调 5/6/7，而是二选一，由后续产品路线决定：

- **A. 先正式化 10/11 的连续 F9 能力与 projection contract**，再考虑更高证据等级验证；
- **B. 暂停第二组产品化，转入第三组 0/1/2/8/9/12 的 development semantics。**

无论选择 A 或 B，都需要新的显式 scope；当前 H 的 development search authority 应关闭。

## 7. Claim ceiling

本评审证明的是当前 development evidence 下的工程/语义处置合理性，不证明真实基金经理、游资交易员或独立量化团队已经签字认可。若未来获得真人专业参考，应作为 `HUMAN_EXPERT_REFERENCE` 新证据层记录，不得改写本次 synthetic-development 历史。