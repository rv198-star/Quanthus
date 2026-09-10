# P3-STAGE-006：真实输入上的参考采集可行性

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Owner decision | 用户要求沿评审结论继续，直到当前里程碑完成；本站落实真实参考可行性，不重做已关闭工程里程碑 |
| Parent | [参考协议](stage-order-reference-protocol.md); [LLD03](lld/03-label-feature-system.md) |
| Work item | P3-STAGE-006 |
| Contract | `../../config/stage-learning/order-reference-feasibility.v1.json` |
| Delivery | `../../config/stage-order-reference-feasibility-delivery.v1.json` |
| Implementation | `../../src/quanthus/stage_learning/order_reference_feasibility.py` |
| Evidence | `../acceptance/stage-order-reference-feasibility-20260911.md` |

## 1. 范围与事前选择

只读取已接受的两份F9运行中357条相同的保留输入，来源身份完全继承
`config/structure-order-qualification.v1.json`。先用原F9深校验验证两份不可变产物，校验
输入字节一致及已接受哈希；不打开DWD/provider，不读取或重建旧160例，不运行分类器。
只有三只证券和2020年第一季度，不能代表市场，更不是新盲测。

每证券在2020-01-02至2020-03-31的有记录会话中排序，取索引
`floor(j*(n-1)/3)`，j=0,1,2,3。合计12例。选择不依赖指标、候选、参考标签或可用性；
缺失也保留，不换例、不补样。来源已用于前序研究，且当前会话读过前序摘要，明确
**不声称本助手在认知上对整个历史区间盲测**。机器提供的每例视图仍严格截至该例T，
不提供候选答案/阈值/评分，也不提供未来结算。

## 2. DAG、角色与预算

```text
两份已封存F9 -> 原验证/源输入身份 -> 固定日期选择
 -> 一次准入与每族Facts一次构建 -> prepare封存案例证据
 -> 短线/基金两角色逐例观察（self_simulated，关联同一来源）
 -> resolve封存原意见与解析 -> 独立读回重建 -> 量化/数仓符合性验收
```

复用P3-STAGE-005 API，不修改其历史合同或原0/2阈值。相同来源的A/B不算两倍参考；
12例也不算12个独立市场事件。量化和数仓不提供额外票数。沿用用户已允许的自身模拟，
不再等待401或伪造外部模型/真人身份；未来独立验证仍未建立。

固定Event Worker通道使用原`quanthus.structure-mvp.v1` envelope，仅新增一个严格配置，
其phase只准prepare与resolve。prepare不接收意见；resolve只接收所封存prepare的run id、
manifest SHA和24份已观察意见。动态意见是运行输入，不提交Git。两个Job分别绑定精确
代码/配置/输入哈希；不重复提交同阶段，不自动第三次尝试。技术恢复优先验证已封存产物。

每例意见按20日主命题、10日局部条件分别说明支持、反证与未知，不生成选参结果。
外部输入只能是同一助手实际完成的两角色意见，不能由runner阈值程序代写后改标AI。

## 3. 产物及验收

prepare保留原source-inputs、源manifest/readiness绑定、案例证据与运行计时；T-only
review视图通过受限Job结果返回。resolve保留完整P3-STAGE-005 bundle及prepare绑定。
代码只在所声明Event Run根内读写，不接收任意路径。存在目录不覆盖，必须验证后恢复。
生成/use时间进入物理manifest或运行日志，不进入事实语义身份。

验收分开报告：

- **采集闭环**：固定12例与24份完整意见均保留，源与案例可重建、早期视图无后续行，
  输入/未知/同尺度冲突/近期分歧分开记账。这证明真实数据能进入机制，不证明意见正确。
- **有限覆盖观察**：至少出现一个一致0和一个一致2且无RULE_CONFLICT时，报告
  `DESCRIPTIVE_REFERENCE_COVERAGE_OBSERVED`；否则为
  `INCONCLUSIVE_REFERENCE_COVERAGE_NO_SUPPLEMENT`。这不是降低原20正例统计门槛，
  也不是分类性能验收：原门槛完全不参与此试点，更不被替换。
- **独立语义资格**：始终未建立；没有准确率、市场频率、经济价值、正式标签或搜索授权。

即使采集闭环完成且有限覆盖不足，也完成本里程碑的明确结案；不在看到结果后增加案例。
若源不可读/身份不符则BLOCKED BEFORE REVIEW，不伪造完成。若工程发现缺陷，保持失败
证据，按技术归因处理，不以修改案例或标注改善结果。

## 4. 边界保持

原P3-STAGE-004仍INCONCLUSIVE_REFERENCE_SUFFICIENCY，P3-STAGE-005仍为机制工程验收。
本授权仅在此后继作用域启用真实保留输入与模拟意见，不改写之前的权限字段。
2022—2024 arbitration不重读，2025不动；不会启动RX6800批量重算、日更、策略或交易。

采用最小部署范围：只部署所需Event Worker，保留API、Web、replay-worker和数据卷。
该部署只为读取已有artifact并执行参考封存，不进行数据库迁移或数据清洗。
