# P3-STAGE-006：真实参考采集可行性记录

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-006 |
| Governing design | [真实参考可行性](../design/stage-order-reference-feasibility.md) |
| Prior baseline | `6423d9625e00cc8691476b37fe61ffb1f44ca107` |
| Contract | `../../config/stage-learning/order-reference-feasibility.v1.json` |
| Config digest | `sha256:f161f1931c724df05b6dff0851185fe9e8571286841b3fd06e013a3821fdaf22` |
| Current checkpoint | release-ready; real prepare/review/resolve not yet executed |
| Claim ceiling | Real-input collection feasibility only, no independent semantic or market qualification |

## 1. 前置与范围

上一站P3-STAGE-005已接受。用户要求继续到里程碑完成，本次事前冻结12个日期选择案例、
同源self_simulated两角色24份意见、最多prepare/resolve两个Job和无补样停止条件。
两份来源是已接受F9冷运行的保留输入，不重新清洗或读取DWD，且不读取旧160例。
当前会话已看过前序运行摘要，所以不宣称完整认知盲测或独立专家验证。

源码读写只涉及已提供的不可变包和本次新产物。意见从外部传入，runner不生成意见；
意见与prepare身份都进入DurableJobQueue input digest，防止恢复时使用另一组意见。
原source bytes、两份manifest和readiness一并保留；冷验证检查其已接受SHA与case重建，
不会把只修文件SHA的篡改当成正确证据。目录恢复优先验证，不重算或覆盖。

## 2. 工程检查（执行记录随后补记）

聚焦协议/runner/Worker测试：67 passed。首次恢复测试误把内存测试队列的已终态Job直接
改成queued，未重入队；改为显式模拟已获得claim的恢复入口后通过。这是夹具修正，
不是更改生产队列语义。行长和import格式检查在发布前处理，不修改旧业务规则。

发布前 `make check` 完成：1932 passed、7 skipped，245.55秒；doctor/compile/planning均通过。
7项仍为2项生产专家产物未挂载、5项PostgreSQL测试DSN未设置。最终55项聚焦及文档检查
通过，新增代码/Worker/同步脚本/测试Ruff与git diff --check通过。工单LLD表头初次写成
Work item而非规划解析器要求的ID，导致两项traceability失败；只修正表头后重验通过。
这证明发布准备完成，不声称真实试点已经成功。A/B设计与工程入口已就绪，继续C运行。

GitHub工单：A #283、B #284、C #285、D #286、父 #287。

## 3. 待绑定的运行证据

prepare与resolve的Job、revision、input digest、manifest以及逐例原意见/结论将在实际
完成后登记。任何参考覆盖不足均按冻结规则保留，不通过补样来追求通过。

## 4. 不变项

旧0/2实验和统计门槛不变；2022—2024 arbitration不重读，2025不动；不发布正式标签、
primary_stage，不启动日更、策略消费或交易。临时证据不会晋升为永久ADM。
