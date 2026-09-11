# P3-STAGE-007-B：prepare 技术重试成功与来源隔离验收

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-007-B / #289 |
| Governing design | [固定规则筛查](../design/stage-order-rule-screen.md) |
| First prepare | `stage_7ef751fa63664175b49eaa3459c83ca3` / failed / retained |
| Technical retry | `stage_95f9b48bd96049b184e0cbf856b9a5e8` |
| Retry revision | `ae99d4973f605c3a6e7044cb6c5f88fd91f98874` |
| Retry input digest | `sha256:4dbe116bc5221eb178414f66ada5f589e8f08849a9d440569cd468a3ae20e4cd` |
| Retry state | succeeded / exit 0 |
| Runtime | 2026-09-11T07:59:45.393801+00:00 → 2026-09-11T08:05:35.136275+00:00 |
| Run | `stage-order-rule-prepare-20260911T080532Z` |
| Manifest | `sha256:0331ba6a82db7b4fb60cfbc03f328d899b7939c43936b414d9a4ffbce30b4db7` |
| Evidence digest | `sha256:6a9df1869ff4f532715b212e41b9b6ada34d72835152d7318f4ac662acbf8dcf` |
| Selection digest | `sha256:75c5d7c1a0711482abf503d9c17b71f0470ba256d6b66fd15ce9ff4f645d7373` |

## 1. 结论

P3-STAGE-007-B 的来源隔离与固定选例已经完成，可以关闭 B 并进入 C 的实际参考判断准备。该结论只覆盖开发期来源隔离、经济身份排除、固定抽样和 T-only 证据封装；不覆盖参考语义正确性、候选准确率、市场代表性、收益或交易。

失败的首个 prepare Job 保留为历史证据，没有改写、删除或冒充成功。唯一技术重试绑定该失败 Job，并在 canonicalization 修复后成功；没有第三个 prepare。

## 2. 实际来源隔离与选例结果

成功 retry 的 Worker 冷验证结果：

- 旧 P3-STAGE-004 队列重建并核验：512 个证券；
- 被排除的经济身份：515 个；
- 新开发候选人口：3917；
- 隔离后固定池：512；
- 固定案例：160；
- coverage / order-like / instability-like / boundary 四桶缺额均为 0；
- 封存 T-only 证据源行：9760；
- `source_isolation_verified=true`；
- `review_ready=true`。

固定选择和证据内容由 selection/evidence digest 锁定。后续参考判断不得重抽样、补样或按候选结果替换案例。

## 3. 首次失败与重试边界

首次 prepare 已经跑通旧512重建、旧 source binding、3917候选人口、515经济身份排除、512隔离池、160选例和9760行证据构建，失败点位于选例摘要与005证据包之间的后置一致性校验。根因是抽样坐标使用绝对方向效率/绝对中心迁移，而 `SamplingObservation.from_case` 保留带符号值，导致负号案例被误判为摘要不一致。

修复只统一该一致性校验的 canonical sampling 坐标；没有改变：

- 007 固定规则及任何阈值；
- validation digest、sample seed、面板配额；
- 旧512排除逻辑和经济身份映射规则；
- 2021开发范围；
- 005参考协议；
- 参数搜索、2022—2024 arbitration、2025、正式标签或交易权限。

技术重试是第二次 prepare 执行，因此开发源实际被同一冻结合同读取了两次。第一遍没有产生可用参考意见、候选评分或语义结果；该事实仍计入执行历史，不以“技术重试”隐藏成本。

## 4. Claim ceiling

本验收支持：来源隔离成立、固定160例可供后续参考判断、prepare 工程闭环成功。

本验收不支持：160例已经有正确标签、Stage 0/2 规则有效、独立专家验证、市场代表性、正式 LabelCard、日更、策略或交易。

C 开始前，参考评审必须读取这批已经封存的 T-only 视图，隐藏候选输出/阈值/评分；原始意见先封存，随后才能执行唯一冻结候选评分。
