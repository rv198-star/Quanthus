# 三类实际使用案例

这三例分别帮助研究负责人判断：更新是否传到真实决定、原专家门槛是否仍生效、状态不可用时应该做什么。逐项原始字段在私有发布的`study/feedback.safe.json`，CN02保留`/srv/agentdock/quanthus-ops/research-r10-feedback-20261114/feedback.safe.json`；使用共同event_id即可定位三臂decisions与分配审计。

## 1. 更新改变了决定

- 2021-06-24，`event:1c0e723649d90bb70d4f812047798506ac0ff68dccfab281861365f115931105`
- case：`r10-3ed4bafe8bdaa5ba1266b754`。
- v0分数0.4970186086，v1分数0.6955016781，跨越固定0.5门槛。
- C1：no_action、R10_CONFIRM_BLOCK、未分配/未成交。
- C2：trial_entry、R10_CONFIRM_PASS，capital-unit-02分配且成交。
- C0：trial_entry并分配，但未成交。各路径的资金和持仓状态不同，决定与成交需分别查看。
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
