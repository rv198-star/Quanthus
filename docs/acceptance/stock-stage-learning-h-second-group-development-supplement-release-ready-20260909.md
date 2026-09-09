# P3-STAGE-001-H 第二组 development reference supplement：Release Ready

| 字段 | 值 |
| --- | --- |
| Evidence status | pre-run freeze / release ready |
| Work item | `P3-STAGE-001-H` / #245 |
| Prior Job | `stage_45493c2eae724023a1a1a626dd515a95` |
| Prior Run | `stage-second-group-development-20260909T155024Z` |
| Prior manifest | `sha256:16056a637d4456ed3d9dcab4e4a1f9feb9b7f5e361e76bcc66c16a12acd14cb9` |
| Prior candidate | `sha256:8e63c41f42a21396b7fc5eb785e37838ea2a2cb3fd7716f4b1c87b0f7a418d83` |
| Supplement config | `config/stage-learning/second-group-development-supplement.v1.json` |
| Supplement config digest | `sha256:161e07f2ab5cdfd8f0b06f89c29136a04ff87ea6d2fc2a5f2b73fa7d85ed5955` |
| Base development config digest | `sha256:26c6b672ce45551e87dbba3dcc5ae187ba73067545a94888d74ac423c8f4b170` |
| Synthetic panel digest | `sha256:c1a2e022499dff4b463d7ab7514e7d68600affc6ec25607ed017d1c97707ee13` |
| Data authority | 2020-2021 development only |
| Holdout | none |

## 1. 上一轮真实结论

上一轮执行成功，但整体 disposition 为 `INCONCLUSIVE`。它不是基础设施失败：

- Stage 10 通过 hard gates 并冻结为 `5 sessions + 2% depth`；
- Stage 6 的参考账本只有 9 个 `ambiguous_set`，低于预登记 minimum=10；
- Stage 11 的参考账本只有 7 个 `ambiguous_set`，低于同一 minimum=10；
- 因为 ambiguous reference count 是 corpus-sufficiency gate，Stage 6/11 在该参考集下无论参数如何都不可能通过；
- Stage 7 已有 42 个 definite positive、58 个 definite negative、20 个 ambiguous references，样本量条件充分但没有候选通过，因此它属于真正的 semantic/parameter `INCONCLUSIVE`，不是参考样本不足。

因此不得通过降低 `minimum_ambiguous_references`、放宽 precision/FP/F1 gate、扩大 Stage-7 网格或重新搜索 Stage 10 来追求 PASS。

## 2. 唯一允许的修复

本 supplement 只修复 Stage 6 和 Stage 11 的 development reference sufficiency：

- 完整保留并重新生成原冻结 base sample；
- 每个原 T-only feature bucket 追加 target cap=20 的新 context；
- supplement 必须排除 base sample 已使用的 episode；
- exact supplement quota 不是 process-failure 条件，但合并账本仍须满足原 `ambiguous >= 10` gate；
- reviewer policies、hard gates、Stage 5/6 grid、Stage 11 grid全部保持原值；
- Stage 10 直接绑定上一轮冻结候选，不得重新搜索；
- Stage 7 保留上一轮完整 trial ledger 与 `INCONCLUSIVE`，不得重跑或扩网格。

如果补样后 Stage 6/11 仍无法满足 reference sufficiency 或没有候选通过原 hard gates，则本 development milestone 以 `INCONCLUSIVE_AFTER_REFERENCE_SUPPLEMENT` 收口，不再继续“修到通过”。

## 3. Milestone 完成语义

只有以下条件同时成立，才允许 disposition 为：

`DEVELOPMENT_MILESTONE_COMPLETE_WITH_STAGE_7_INCONCLUSIVE`

1. Stage 5/6 在合并后的参考账本上冻结一个原网格内候选；
2. Stage 10 保持上一轮已冻结 `5 sessions + 2% depth`；
3. Stage 11 在合并后的参考账本上冻结一个原三值网格候选；
4. Stage 7 明确保留为 attributable `INCONCLUSIVE`，其失败 gate 统计和最佳 diagnostic trials 进入最终 evidence；
5. 5/6/7/10/11 五个编号继续独立，不发生 merge；
6. 所有输出仍为 development-only candidate，不是正式 Stage LabelCard。

Stage 7 的 `INCONCLUSIVE` 在这里是一个允许的里程碑结论，而不是要求继续改规则直至 PASS。完成该 milestone 后，才按用户要求发起量化、短线操作、基金三视角评审，判断 Stage 7 应保留、改语义、改维度或暂停。

## 4. 数据与权限边界

明确允许：

- 重新读取 2020-2021 development DWD；
- 重建一次共享 512-security Structure/Activity panel；
- 对 Stage 6/11 追加 development-only T-only synthetic references；
- 只在原冻结 5/6 与 11 网格内重评分；
- 读取并验证上一轮 immutable development artifact。

明确禁止：

- 读取或复用 2022-2024 arbitration；
- 读取 2025 permanent blind；
- future settlement / market outcome / strategy result；
- 重新搜索 Stage 10；
- 扩大或重跑 Stage 7 参数网格；
- 降低任何 hard gate；
- 发布正式标签、日更、SHADOW、paper/live 或交易权限。

## 5. Claim ceiling

本 supplement 是**结果已知后的 development sample-sufficiency repair**，不是独立验证。即使最终 milestone complete，也只证明：

> 在固定 development 数据、冻结 synthetic-consensus reference policy、冻结 hard gates 和有限参数网格下，Stage 5/6、10、11 得到可复现 development candidates，而 Stage 7 得到可归因 INCONCLUSIVE。

后续量化、短线操作和基金专家评审仍是更高一层的设计/业务复审，不得把本 development milestone 描述为真人专家共识、外样本稳定性、经济价值或正式交易资格。
