# R10 后继提案：窄幅子句的视图不变性资格

Design status: draft。Delivery gate: blocked（独立设计核准和新的付费参考预算待授予）。工作项 #686；父设计为[原 R10 反馈](research-r10-next-feedback-v1.md)，结果依据见[整体收敛交审](../acceptance/research-r10-overall-convergence-20261115.md)。这是一份参考资格提案，未激活模型候选或新训练。

## 问题、消费者和新信息

P5 需要先判断“materially_narrow 能否成为独立必要条件监督”。现有 31 例窄幅票与 whole 票完全重合，且 600328／600718 呈相反的宽度／标签排序。假说为：仅当前 20 行的固有包络证据可被稳定判断；显示或隐藏 prior20 不应单独改变该子句。

本实验只改变参考输入的 prior20 可见性。current20 始终是原观察包中的同 20 行，无合成价格。它提供现有历史票缺少的同路径成对不变性证据，决定是否值得单独准备必要条件训练。当前 R10 v1 的拒绝决定不依赖本实验完成。

## 唯一固定设计

| 样本 | anchor | 资格角色 |
|---|---|---|
| 600328 | 2020-12-31 | 已曝光训练窄 TRUE 上界见证 |
| 600718 | 2021-08-16 | 已曝光开发 FALSE 反例 |
| 603023 | 2021-08-19 | 已纠正正例中靠近见证的控制 |
| 600538 | 2020-12-31 | 原训练窄 FALSE 最小宽度控制 |

每例两视图：current20_only 与 full40_current_last20；每视图两个 reader，共 16 条意见。使用原 provider `magpie`、原固定模型别名 `auto/gpt-6.1-sol` 和 `auto/gpt-6-astra`，独立 fresh session；启动前固定实际运行身份。每个任务只见盲 packet_id、HLC、当前区间和相同单子句提示，不见原角色、旧票、分数、收益或其他案例。顺序按 packet_id+reader 摘要固定。

机器合同、8 个确切 packet、16 个任务顺序、盲提示及私有来源映射已放入本次交付 `successor/`，全部带 SHA。源来自已接受的原包，不采集市场数据，不重投或修改旧票。全部样本角色为已曝光开发资格材料。

## 执行、比较与验收

1. 固定 packet／提示／job／运行身份与新预算；身份缺失或漂移停止。
2. 最多并发 2、每任务 120 秒，fresh session，重试 0、健康调用 0；先逐意见封存后比较。
3. 只判断 materially_narrow，返回 TRUE／FALSE／UNKNOWN、原当前行引用、支持／反证、理由，以及 Hmax／Lmin／midpoint／close_range／19 次 close travel。引用值逐字一致，算术误差不超过 0.0002 个标准化价格单位。
4. 比较原封存子句（只作诊断）、新 current20-only 判断、同 current20 的 full40 判断。没有 R0／v0／候选模型预测或资金比较；若将来另启模型合同，须单独冻结 R0／v0／候选识别比较与 FP/FN 门。

资格通过须全部成立：16 条意见有效且全部明确；每 reader 的四例两视图标签 8/8 一致；两个 reader 对四例 current20-only 标签 4/4 一致；对子理由不把 prior 收缩或内部 churn 单独当作窄幅条件。任何失败、算术／引用无效、UNKNOWN、视图或 reader 分歧即 `STOP_REFERENCE_NOT_QUALIFIED`；保留全部尝试，不补例、修票、改门或重试。

通过只允许提出下一份训练预登记：独立子句含义、窄 TRUE／whole FALSE 对照、用途与曝光必须齐备，自动训练次数仍为 0。失败则停放当前参考家族上的独立窄幅学习。无论何种结果，原 width-only 停放和原 R10 结果均保持。

## 最小资源与停止分支

最多 30 分钟墙钟、16 条意见、每条意见 JSON64KiB／事件流256KiB、脚本 CPU300 秒、RSS2GiB、输出64MiB。当前新增付费调用 0；启动需独立核准及一次新的付费参考预算，实际 provider token／费用身份另记。市场采集、训练、预测搜索和资金回放上限均为 0。

交付包冻结了逐任务完整提示及现有 `r9_calibration.py` 的 provider CLI argv：两个原模型、medium reasoning、magpie、两类 retry0、ephemeral、read-only sandbox。按 jobs 组装 subprocess argv，无 shell 代换。新的单子句20/40视图协议不直接调用旧四子句40行校验器；付费前按本合同完成合成 schema／引用／算术干测，再按明确 schema 执行16个任务。尚未执行这些新参考任务。
