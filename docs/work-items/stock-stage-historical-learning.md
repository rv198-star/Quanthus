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
| C | P3-STAGE-001-C | closed / #240 | 最小Facts扩展、历史逐日harness、候选/decision、独立oracle | real-DWD governed harness PASS；见 `../acceptance/stock-stage-learning-c-harness-20260908.md`；不训练分类器、不读future target |
| D | P3-STAGE-001-D | ready / #241 | T-only参考、development-only future settlement、Synthetic Expert Panel开发参考 | v1完整workflow已跑通但审计结论INCONCLUSIVE：Stage 3 definite仅3例且歧义控制不足；见 `../acceptance/stock-stage-learning-de-v1-development-audit-20260908.md`。v2采用不重叠120例分层补样：40 started、30 early-held、20近锚失效、15 reclaim、15 mature-held；Panel v2把活动强度纳入Stage 3语义。真人专家仍属更高证据层级 |
| E | P3-STAGE-001-E | blocked pending D v2 supplement | R0/R1/必要CPU R2、有限试验台账、相邻组校准、锁定候选 | v1的18-trial学习已执行但原diagnostic rank #1不接受；v2加入hard-gated development selection、正确的positive-reference coverage和negative-control false-positive审计，并直接消费D封存的`learning-inputs`避免同Job重复重建StructureV2。R2仍关闭 |
| F | P3-STAGE-001-F | blocked by E+保留集授权 | 一次外样本、分状态/profile/用途资格包 | 数据权限与冻结门槛全部满足；测试失败保留，不原地调参后再称盲测 |
| G | P3-STAGE-001-G | blocked by F+日更授权 | 新as-known profile、DWD READY后固定模型推断、ADM发布 | 延迟/缺失/修正/漂移受控；不自动开启P6或下单 |
| H | P3-STAGE-001-H | blocked by F+新增范围授权 | 第二/三组状态及特定消费者对照 | 不凑12个状态；按同协议新增版本；策略用途另做完整P2 |

每项精确inputs/deliverables/acceptance/blocked_on由机器计划登记并同步到GitHub。A/B/C已关闭；D现已获准执行development-only reference/settlement pilot，但这仍不是市场分类器训练授权。D允许在仅用于开发的有限试标后形成最终阈值/样本补充；它必须在E结果访问前密封，不可事后补门槛。

## 2. 谁负责什么

算法：规则/轻量模型实现、fold内训练与校准、可重复性、复杂度预算。量化：问题/对照、时序切分、有效样本、区间与试验台账。短线业务：可观察定义、参照角色、相邻反例、时效性、允许灰度。数仓作为跨项基础责任：源字段、单位、身份、知识视图、修正影响与发布。

本次早期三视角评审由单一助手分别执行，不是真人签字。D现采用版本化 `SYNTHETIC_EXPERT_CONSENSUS`：不同风格原型互不可见地评审同一T-only证据，且明确不等同真人独立金标准；未来如获得真实A股专业交易者/基金经理参考，可升级或对照为 `HUMAN_EXPERT_REFERENCE`。不能让用户因不懂算法而被迫审批任意阈值。

## 3. 建议的第一步

先完成A，补全用户Win11的CPU/RAM/可用SSD，将数据范围、有限预算及最小实现路径写成执行包。技术侧推荐CPU-first，沿用当前分区；无需先租GPU或重装Windows。

获准后B可先运行合成数据基准，C构建首组最小输入与契约，D再进入获准市场样本。大规模训练、外样本和日更是后续有证据的选择，不是本次工单创建的副作用。

## 4. 原提案迁移

原`stock-stage-qualification-proposal.md`的STAGE-A/B对应新A/C；STAGE-C对应C；STAGE-D/E拆为D/E/F；STAGE-F对应G，新增B解决设备容量、H记录扩展/消费者边界。原提案记录作为历史保留，不再是并行执行清单。

## 5. 本轮验证

计划校验包括：稳定ID集合、依赖无环、先决项顺序、文档路径、P0分区一致以及每项显式权限。GitHub只同步P3-STAGE-001和A-H，不能改动旧P3 fixture、Facts验收、Catalog或P6工单。原始规划收尾见 `../acceptance/stock-stage-learning-planning-closeout-20260908.md`；当前执行状态以上表及各 acceptance 为准。A/B/C 已关闭，D 工程pilot已通过并进入合成专家评审；完整 `SYNTHETIC_EXPERT_CONSENSUS` 可解锁development-only R0/R1。正式 `training_authorized`、2022+结果/密封分区、资格发布、日更与租机权限仍保持关闭。
