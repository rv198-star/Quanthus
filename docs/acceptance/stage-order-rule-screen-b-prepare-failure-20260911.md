# P3-STAGE-007-B：首次 prepare 技术失败与唯一重试授权

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-007-B / #289 |
| Failed Job | `stage_7ef751fa63664175b49eaa3459c83ca3` |
| Revision | `b7042cf0683b817a2ed2b3a59a00d5d6159cf5b6` |
| Input digest | `sha256:d46f8d3f990890abdf49d0ae0fba12c0b9a5a2629e70faba56a15d15acf5dedb` |
| State | terminal / failed / exit 1 |
| Worker error | `stage_order_rule_prepare_process_failed` |
| Root cause | `post_selection_sampling_coordinate_canonicalization` |
| Failed check | `selected_observation_differs_from_retained_evidence` |
| Semantic result exposed | no |
| Opinions generated | 0 |
| Candidate scored | no |
| Parameter search | no |

## 1. 失败前已经实际完成的步骤

该 Job 不是启动失败，也不是 SSH 或 Tailscale 失败。RX6800 本地 Worker 已经完成主要
prepare 计算，日志依次记录：

- 旧 P3-STAGE-004 512 证券队列精确重建：`cohort_size=512`；
- 旧 source binding 重建：`attempts=313344`；
- 新开发候选证券：3917；身份成员3920；排除经济身份515；隔离后新池512；
- 512证券正式 Facts 全部完成，2021 可用观察121329；
- 固定选择160例，coverage/order-like/instability-like/boundary 四桶缺额均为0；
- 保留61会话闭包9760行；reference evidence 已构建到160例，约10.9MiB；
- 未进入意见生成、候选评分、参数搜索或任何未来结果读取。

这些日志证明计算路径走到封装后的一致性检查，但因为 governed artifact 尚未发布，不能把
上述日志计数升级为 B 验收或 source-isolation accepted receipt。旧512名单明细、新160案例
明细和 evidence 仍以成功 retry 后的 sealed artifact 为准。

## 2. 根因

prepare 的抽样坐标使用正式开发观察中的绝对值字段：

- `efficiency20_abs`；
- `center_shift20_abs`。

005 reference packet 则忠实保留正式 Fact 的带符号原值。最后一致性校验通过
`SamplingObservation.from_case()` 从 reference packet 重建抽样坐标时，旧实现直接保留
这两个符号值，导致负方向效率或负中心迁移的案例与选择阶段的绝对值摘要 digest 不同。

修复仅在该重建入口将上述两字段 `copy_abs()`，使它与选择阶段使用的抽样坐标一致。范围宽度
和宽度变化保持原值；选择算法、seed、桶定义、阈值、规则、数据范围和证据协议均未改变。
新增回归明确覆盖负方向效率和负中心迁移。

## 3. 唯一技术重试边界

允许一次 technical retry，仅用于发布原 prepare 本应产生的 sealed artifact。它不算第二轮
研究，也不开放结果驱动调参。机器授权文件：
`config/stage-learning/order-rule-prepare-retry.v1.json`。

重试必须同时满足：

- 精确绑定上述失败 Job、revision 和 input digest；
- prepare config digest 仍为
  `sha256:b27bbc1d3d54f9e410a8ca7b931af63f62690be9b911d538aeb0cda5666e9bd9`；
- rule digest 与 validation digest 不变；
- 修复文件字节摘要与授权文件精确一致；
- 同一 source scope、同一 selection algorithm、同一 seed；
- 不生成意见、不评分、不搜索参数；
- technical retry 最多1次，成功或失败后不得再产生第三个 prepare 尝试。

Worker 保留首个失败 marker，并使用单独 retry marker 绑定第二个 Job；不会删除或覆盖失败
Job。若 retry 再失败，P3-STAGE-007-B 停止并按实际失败结案，不继续追加 prepare。

## 4. Claim ceiling

本记录只证明一个确定的实现缺陷及其有界技术修复。修复后聚焦Worker/validation/prepare/
计划/追溯回归139 passed；全仓`make check`为2075 passed、7 skipped，跳过项仍是两项未挂载
生产专家产物和五项未配置PostgreSQL测试DSN。修复文件字节摘要由机器retry授权绑定。

它不证明160例语义参考正确，不证明Stage 0/2分类有效，不证明市场代表性，也不授权
2022—2024、2025、正式LabelCard、日更、策略或交易。
