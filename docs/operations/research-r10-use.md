# R10 当前统一使用入口

当前完整交付是本机私有包 `outputs/quanthus-r10-overall/README.md`：
`/Users/william/Documents/Codex/2026-11-14/jie-s/outputs/quanthus-r10-overall/README.md`。
它把完成情况、保留 B／v0 的理由、参考覆盖及下一动作放在同一页，并链接完整 19 事件与 13 成交链。
整体结论处于独立交审；已接受的诊断子任务不替代 R10 整体验收。

| 想做的判断 | 入口及明确语义 |
|---|---|
| 原 A/B/C 及 #544/#545/#546 的 R10 部分是否兑现 | `acceptance-entry.md`／`parent-acceptance-map.json`，14 项整体验收和 11 项父验收贡献 |
| 为何保留 B／v0，下一笔该做什么 | `feedback-decision.md`，G01/G02/G03/G05 实际处置与唯一非激活参考资格合同 |
| 哪些事件实际占用资金、成交和退出 | `decision-entry.md`，19 个完整卡片直接列出订单、入／退出成交、账本和结算，无需手工 join |
| 哪些判断有本锚点参考 | `recognition-entry.md` 和各事件卡；13 笔成交精确参考为 0/13，其他日期同公司标签单列而不迁移 |
| 原三类使用案例如何复核 | `usage-entry.md`，原事件键、三臂动作、实际容量及原因均展开 |

阅读时将 **entry_eligible（通过专家／确认门）**、原 **allocated**、**capacity_disposition**、
**reserved**、**order** 和 **fill** 分开。原 19 条 trial 的 allocated 都是 true，实际容量为
13 条 reserved、6 条 rejected/no_empty_unit；只有 13 条有订单及入场成交。封存原字段未改写。

版本决定仍是拒绝 v1、保留 B／研究 v0。实际经济门已经失败；即使参考相关两门另有资格解释，
本次固定候选的采用合取仍失败。因此对当前已拒绝候选停止追加 13 个成交锚点票，当前阅读视图
直接显示覆盖缺席，并保留完整资金决定。未来模型用途需先冻结原 B 合法入口的参考覆盖。

来源和细节：[整体交审](../acceptance/research-r10-overall-convergence-20261115.md) ·
[唯一后继资格提案](../design/research-r10-reference-boundary-proposal-v1.md)。

## 原封存三类实际案例与历史使用验收

这三例分别帮助研究负责人判断：更新是否传到真实决定、原专家门槛是否仍生效、状态不可用时应该做什么。逐项原始字段在私有发布的`study/feedback.safe.json`，CN02保留`/srv/agentdock/quanthus-ops/research-r10-feedback-20261114/feedback.safe.json`；使用共同event_id即可定位三臂decisions与分配审计。

## 1. 更新改变了决定

- 2021-06-24，`event:1c0e723649d90bb70d4f812047798506ac0ff68dccfab281861365f115931105`
- case：`r10-3ed4bafe8bdaa5ba1266b754`。
- v0分数0.4970186086，v1分数0.6955016781，跨越固定0.5门槛。
- C1：no_action、R10_CONFIRM_BLOCK、未分配/未成交。
- C2：trial_entry、R10_CONFIRM_PASS，capital-unit-02分配且成交。
- C0：trial_entry，原 allocated=true；实际容量处置为 rejected，未预留、未成交。各路径的资金和持仓状态不同，资格通过与实际预留需分别查看。
- 决定：保存此接缝作为实际更新进入专家与资金路径的证据。版本采用仍服从已封存三门，继续保留B/v0。

同类C1→C2动作变化19事件、19公司、15日期。

## 2. 原专家门槛继续阻断

- 2021-08-24，`event:002588086ad7fb6eec733a958d27faff821e4927e3bc7196c81c0fa48e135348`
- C0/C1/C2全为no_action，aggregate_score=-1，实际reason为`entry_score_below_threshold`。
- 决定：保留原专家门槛；状态输出沿用原入口求值顺序。该例归因于原门槛阻断。

原B在E中657条no_action；此数是原B全体阻断计数。

## 3. 状态不可用时拒绝新增确认入场

- 2021-08-11，`event:017c907448010fd54351762cd72c6ecb6e12ed55f8f89a1d78baf601246ba97a`
- C0为trial_entry；C1/C2为no_action，reason含`R10_CONFIRM_UNAVAILABLE`。
- source_reason：`nonadmitted:native_action_or_unknown_date`。本次具体来源是40日行动边界资格未过；本轮unknown ex_date计数0。
- 决定：保持新增确认入场阻断，保留可追踪原因；由业务线明确可用范围。共享源缺陷交P10具名工单。

同类53个事件单独统计，未用于凑足已知分数造成的futility差异。

## 使用验收

独立使用角色从交付包README开始计时，逐一确认三类证据能定位、决定明确、是否需要手工拼表，并给出一项下一步动作。实际验收GO：2026-11-14 10:56:40—10:58:28 UTC，用时108秒，手工join0次，9次动作键自动核对0.028041秒。研究负责人/P5下一步先解释guard新增FP1和eval FP5；原组装器未记录的manual_stitch_seconds保持null。
