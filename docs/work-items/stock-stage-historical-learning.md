# P3-STAGE-001：历史学习与阶段标签资格

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | blocked |
| Parent | `index.md`; `stock-stage-qualification-proposal.md` |
| Governing design | `../design/stock-stage-historical-learning.md` |
| Dependencies | `../design/stock-stage-data-dependencies.md` |
| Compute | `../design/stock-stage-training-compute.md` |
| Review | `../reviews/stock-stage-learning-review-20260908.md` |
| Machine plan | `../../config/stock-stage-learning-delivery.v1.json` |
| GitHub | 父工单 #246；A #238、B #239、C #240、D #241、E #242、F #243、G #244、H #245；已创建并回读，均open |
| Current scope | 设计、角色评审、真实工单登记、只读资源核查；未开始实现/市场训练/数据分区开放 |

## 1. 逐步执行路线

| 步骤 | 稳定ID | 状态 | 主要产物 | 通过条件与失败动作 |
| --- | --- | --- | --- | --- |
| A | P3-STAGE-001-A | closed / #238 | `config/stage-learning/experiment.v1.json`、3/4语义与反例、有限参数空间、120例试标预算、物理镜像claim ceiling | `docs/acceptance/stock-stage-learning-a-freeze-20260908.md`；不打开分类器训练或结果分区 |
| B | P3-STAGE-001-B | closed / #239 | RX6800独立CPU环境和100k/500k/1m容量基准、实际2020-2021 DWD I/O、设备选择 | 8线程/32GiB预算通过；CPU-first accepted；见 `../acceptance/stock-stage-learning-b-capacity-20260908.md` |
| C | P3-STAGE-001-C | ready / #240 | 最小Facts扩展、历史逐日harness、候选/decision、独立oracle | Development-only实现已授权；PIT/身份/坐标/歧义/权限反例通过；批量与增量一致；不扫描未准分区；不训练分类器 |
| D | P3-STAGE-001-D | blocked by C | T-only独立参考、分歧保留、未来resolver、最终试验冻结补充 | 试标不复用为测试；参考和未来账本隔离；没有独立参考不授语义资格 |
| E | P3-STAGE-001-E | blocked by D | R0/R1/必要CPU R2、有限试验台账、相邻组校准、锁定候选 | 先硬门槛再稳健选择；保留失败配置；无证据可合并/保留R0/INCONCLUSIVE |
| F | P3-STAGE-001-F | blocked by E+保留集授权 | 一次外样本、分状态/profile/用途资格包 | 数据权限与冻结门槛全部满足；测试失败保留，不原地调参后再称盲测 |
| G | P3-STAGE-001-G | blocked by F+日更授权 | 新as-known profile、DWD READY后固定模型推断、ADM发布 | 延迟/缺失/修正/漂移受控；不自动开启P6或下单 |
| H | P3-STAGE-001-H | blocked by F+新增范围授权 | 第二/三组状态及特定消费者对照 | 不凑12个状态；按同协议新增版本；策略用途另做完整P2 |

每项精确inputs/deliverables/acceptance/blocked_on由机器计划登记并同步到GitHub。A已冻结并关闭；负责人已明确授权B的CPU-first容量benchmark，但这仍不是市场分类器训练授权。D允许在仅用于开发的有限试标后形成最终阈值/样本补充；它必须在E结果访问前密封，不可事后补门槛。

## 2. 谁负责什么

算法：规则/轻量模型实现、fold内训练与校准、可重复性、复杂度预算。量化：问题/对照、时序切分、有效样本、区间与试验台账。短线业务：可观察定义、参照角色、相邻反例、时效性、允许灰度。数仓作为跨项基础责任：源字段、单位、身份、知识视图、修正影响与发布。

本次三视角评审由单一助手分别执行，不是真人签字。后续D明确要求独立T-only参考或把缺口如实保留为INCONCLUSIVE；不能让用户因不懂算法而被迫审批任意阈值。

## 3. 建议的第一步

先完成A，补全用户Win11的CPU/RAM/可用SSD，将数据范围、有限预算及最小实现路径写成执行包。技术侧推荐CPU-first，沿用当前分区；无需先租GPU或重装Windows。

获准后B可先运行合成数据基准，C构建首组最小输入与契约，D再进入获准市场样本。大规模训练、外样本和日更是后续有证据的选择，不是本次工单创建的副作用。

## 4. 原提案迁移

原`stock-stage-qualification-proposal.md`的STAGE-A/B对应新A/C；STAGE-C对应C；STAGE-D/E拆为D/E/F；STAGE-F对应G，新增B解决设备容量、H记录扩展/消费者边界。原提案记录作为历史保留，不再是并行执行清单。

## 5. 本轮验证

计划校验包括：稳定ID集合、依赖无环、先决项顺序、文档路径、当前权限全false、P0分区一致、ready范围不包含训练。首轮文档/计划专项48 passed；这些只证明计划契约，不证明标签质量或训练速度。GitHub只同步P3-STAGE-001和A-H，不能改动旧P3 fixture、Facts验收、Catalog或P6工单。后续训练的成功与本轮规划验证分开。最终准备收尾见 `../acceptance/stock-stage-learning-planning-closeout-20260908.md`；完整工程门禁1617 passed / 7既有环境skip，Catalog摘要未变。A-H仍未执行，所有训练/结果/密封数据/日更/租机授权保持关闭。
