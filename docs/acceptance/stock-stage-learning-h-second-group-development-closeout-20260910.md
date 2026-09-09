# P3-STAGE-001-H 第二组 development milestone closeout

| 字段 | 值 |
| --- | --- |
| Evidence status | durable mixed closeout |
| Work item | `P3-STAGE-001-H` / #245 |
| Closeout contract | `config/stage-learning/second-group-development-closeout.v1.json` |
| Base Job | `stage_45493c2eae724023a1a1a626dd515a95` |
| Base manifest | `sha256:16056a637d4456ed3d9dcab4e4a1f9feb9b7f5e361e76bcc66c16a12acd14cb9` |
| Supplement Job | `stage_5dad4fb7923d44e7b135461e2f60ec75` |
| Supplement manifest | `sha256:c3eccde8d704652ab67f9016cfe346d9587ccb7a69d20fd8b8a129c35ad56155` |
| Development data | 2020-2021 only / 512-security cohort |
| Final disposition | `MIXED_DEVELOPMENT_CLOSEOUT_PASS_WITH_SCOPE_REDUCTION` |
| Review | `../reviews/stock-stage-second-group-development-review-20260910.md` |

## 1. 执行结果

首轮 `second-group-development.v1.json` 成功执行，但 disposition 为 `INCONCLUSIVE`。其中：

- Stage 10 在冻结 9 组网格内成功形成 candidate：`initial_low_support_candidate`、5 个交易日持续未收复、低于支撑至少 2%；
- Stage 6 / Stage 11 的 ambiguous reference 分别只有 9 / 7，低于不变 hard gate=10，因此首轮不能把它们的未通过解释为参数失败；
- Stage 7 已有 42 positive / 58 negative / 20 ambiguous references，仍无候选通过，属于真实 development INCONCLUSIVE。

补充 `second-group-development-supplement.v1.json` 只修复 6/11 reference sufficiency，完整保留首轮 artifact、hard gates 和参数网格；Stage 10 不重搜，Stage 7 不扩网格也不重跑。

Supplement 成功执行：

- Stage 6 merged reference：200 contexts，38 definite positive / 143 definite negative / 19 ambiguous；corpus blocker 已消失；
- Stage 11 merged reference：200 contexts，52 definite positive / 137 definite negative / 11 ambiguous；corpus blocker 已消失；
- Stage 11 成功冻结 `5%` post-break rebound candidate；
- Stage 5/6 联合原网格仍无 hard-gate-eligible candidate；
- Stage 7 保留原 INCONCLUSIVE；supplement artifact 记录其失败原因覆盖 positive coverage、ambiguous over-assertion 与 F1 noninferiority；
- 最终 supplement disposition 为 `INCONCLUSIVE_AFTER_REFERENCE_SUPPLEMENT`，这是 semantic/model selection 结果，不是执行失败。

两个 Job 均 `exit_code=0`、artifact verification PASS；未读取 future outcome、2022-2024 arbitration 或 2025 permanent blind。

## 2. 最终状态

| Stage | Development disposition | 参数/说明 |
| --- | --- | --- |
| 5 上升推进 | `INCONCLUSIVE_PARKED` | 保留编号和连续 extension/drawdown Facts；无离散 candidate |
| 6 回踩测试 | `INCONCLUSIVE_PARKED` | reference sufficiency 已修复仍无联合合格 candidate；不再补样/调参 |
| 7 修复重建 | `INCONCLUSIVE_PARKED` | 深损伤+repair fraction 当前不可稳定离散化；不再扩网格 |
| 10 结构失效 | `DEVELOPMENT_CANDIDATE_FROZEN` | initial-low support + 5 sessions unreclaimed + 2% depth |
| 11 反抽 | `DEVELOPMENT_CANDIDATE_FROZEN` | prior frozen 10 + same support unreclaimed + 5% rebound |

5/6/7/10/11 五个编号均保留，未授权任何合并。

## 3. 停止条件执行

本轮已经触发预登记停止条件：supplement 后不再继续 development chase。

因此：

- 不做第三轮 reference supplement；
- 不降低任何 hard gate；
- 不扩大 Stage 5/6/7/10/11 网格；
- 不切换更复杂模型来“救”5/6/7；
- 不用策略收益反推标签定义；
- 当前版本 development rerun authority 关闭。

未来若要重开 5/6/7，必须先有新的事前语义/事实假设与新版本合同，而不是继承本次 trial 后继续 refine。

## 4. 三专家视角评审

按用户要求，完成量化、短线操作、基金三个评审视角。当前环境无独立 Agent provider，因此 provenance 明确记录为同一助手的三次独立视角审查，不冒充真人专家签字。

综合决定：`PASS_WITH_SCOPE_REDUCTION`。

- 量化：接受 10/11 development candidate；5/6/7 应停止追参，连续事实优先；
- 短线：10/11 的失效—反抽链语义可用；5/6/7 当前离散边界不足，保留概念但暂停硬标签；
- 基金：Stage 10 的中期风险语义最有价值，Stage 11 仅适合作辅助状态；5/6/7 更适合继续消费连续 Facts。

详见 `../reviews/stock-stage-second-group-development-review-20260910.md`。

## 5. 权限与 claim ceiling

此 closeout **不**创建：

- `HUMAN_EXPERT_REFERENCE`；
- SHADOW / QUALIFIED LabelCard；
- 日更或 ADM stage 发布；
- strategy consumer；
- paper/live/交易权限；
- 新 holdout 读取权限。

2022-2024 不得 fresh reuse；2025 保持 `unopened_unselected`。

当前 milestone 证明的只有：在冻结 development 数据、T-only synthetic-consensus reference 和预登记 hard gates 下，Stage 10/11 形成有界 development candidate，而 5/6/7 没有足够证据支持离散化。接受这种 mixed result 本身就是本轮正确收口。