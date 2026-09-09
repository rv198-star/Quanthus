# P3-STAGE-001-H 第二组 development selection：Release Ready

| 字段 | 值 |
| --- | --- |
| Evidence status | pre-run freeze / release ready |
| Work item | `P3-STAGE-001-H` / #245 |
| Runtime config | `config/stage-learning/second-group-development.v1.json` |
| Runtime config digest | `sha256:26c6b672ce45551e87dbba3dcc5ae187ba73067545a94888d74ac423c8f4b170` |
| Synthetic panel | `config/stage-learning/second-group-synthetic-panel.v1.json` |
| Synthetic panel digest | `sha256:c1a2e022499dff4b463d7ab7514e7d68600affc6ec25607ed017d1c97707ee13` |
| Stage 5/6 design digest | `sha256:cc0d08def7b7dc88c082e07abdf080160840476f0bb911413cdfaa35ab389399` |
| Stage 7/10/11 design digest | `sha256:80f0dff2f0a68a1eeb0146886aceecb3c94a698790121cda3a626b4de6de4d61` |
| Data authority | 2020-2021 development only |
| Population | deterministic 512-security cohort |
| Holdout | none |

## 1. 本次授权

本轮只把已经完成的两个 development profile 转成一个可执行的第二组 development
selection。Runner 必须共享一次 512 只股票的 Structure/Activity 前置构建，随后构造 T-only
observation panel 和 synthetic-consensus references，并严格按下列顺序执行：

1. `Stage 5/6`：27 组联合评分，但 5 与 6 保持独立 stage code，允许共存；
2. `Stage 10`：3/5/8-session unreclaimed persistence × 2/4/6% support depth，共 9 组；
3. `Stage 7`：只有 Stage 10 candidate 已冻结后才运行；12/16/20% damage × 25/40/50%
   repair fraction，共 9 组；
4. `Stage 11`：只消费同一 Job 内冻结的 Stage-10 candidate；3/5/8% unreclaimed rebound，
   共 3 组。

禁止把 5/6、10/11 合并成一个标签，也禁止把四段搜索展开成跨阶段笛卡尔积。

## 2. Reference 与 hard gates

每个 stage 的参考样本采用 4 个 T-only feature bucket，每 bucket target cap=30；精确配额不
是 process-failure 条件，但每个 stage 至少需要 80 个 realized contexts，并且一个 episode
在同一 stage reference 中只可出现一次。

Reference evidence tier 固定为 `SYNTHETIC_EXPERT_CONSENSUS`。两个主评策略互不可见，分歧由
第三个冻结 adjudicator 处理。Reviewer 不得看到候选参数、算法 prediction、future outcome、
strategy result 或 sealed-partition 信息。该证据不是 `HUMAN_EXPERT_REFERENCE`。

候选先过 hard gates 再排名：

- definite positive references >= 20；
- definite negative references >= 20；
- ambiguous references >= 10；
- unjudgeable rate <= 15%；
- positive-reference coverage >= 55%；
- definite-negative false-positive rate <= 10%；
- ambiguous unique assertion rate <= 20%；
- one-sided 95% precision Wilson lower >= 65%；
- F1 不得比同 stage 的 R0 低超过 5 个百分点；
- T-only evidence audit veto 必须为 0。

若任何阶段没有候选通过 hard gates，Runner 必须保留完整 trial ledger 并返回
`INCONCLUSIVE`；不得在运行中修改阈值、参考策略或采样规则。

## 3. 权限边界

允许：

- 读取 2020-2021 development DWD；
- development-only synthetic reference；
- 上述冻结搜索空间的 runtime evaluation；
- 产出 development candidate artifact。

明确禁止：

- 读取或重新利用 2022-2024 arbitration；
- 读取 2025 permanent blind；
- future settlement / strategy result；
- 搜索冻结网格之外的参数；
- 结果后热改候选再继续同一 Run；
- 发布正式 Stage 5/6/7/10/11 LabelCard；
- 日更、consumer、SHADOW、paper/live 或交易权限。

## 4. Batch / 资源要求

本 Job 必须只构建一次共享 Structure/Activity prerequisite。5/6、10、7、11 的参考和搜索
都消费该 Job-local immutable panel，不允许为了每个 stage 重建相同 512-security 两年
Structure。Worker wall budget 为 2700 秒；若资源失败，终止并保留本 Job 证据，新版本再跑，
不得运行中热改。

## 5. Claim ceiling

即使五个 stage 都成功冻结 development candidate，本里程碑也只证明：

> 在已消费的 development evidence 与 synthetic-consensus reference 下，第二组存在一个通过
> 预登记 hard gates 的有界候选口径。

它不证明真人专家共识、外样本稳定性、经济价值、正式资格或交易有效性。后续评审将由量化、
短线操作和基金专家独立审查该 milestone，再决定是否进入下一证据层级。
