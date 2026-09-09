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
| GitHub | 父工单 #246；A #238、B #239、C #240、D #241、E #242、F #243、G #244、H #245、I #247、J #248；A-F/I closed，G/H/J及父工单open/blocked |
| Current scope | A-F已关闭，F为sample-feasibility INCONCLUSIVE；I已关闭；J仅冻结未来cohort size=512且真实holdout继续blocked。H现唯一ready：v1 profile已完成并冻结5/6有限搜索；下一步 `second-group-repair-profile.v1.json` 只读2020-2021 development补7/10/11连续证据。5/6与10/11保持独立编号，不做合并；2025 permanent-blind、正式QUALIFIED、日更、消费者和策略权限仍关闭 |

## 1. 逐步执行路线

| 步骤 | 稳定ID | 状态 | 主要产物 | 通过条件与失败动作 |
| --- | --- | --- | --- | --- |
| A | P3-STAGE-001-A | closed / #238 | `config/stage-learning/experiment.v1.json`、3/4语义与反例、有限参数空间、120例试标预算、物理镜像claim ceiling | `docs/acceptance/stock-stage-learning-a-freeze-20260908.md`；不打开分类器训练或结果分区 |
| B | P3-STAGE-001-B | closed / #239 | RX6800独立CPU环境和100k/500k/1m容量基准、实际2020-2021 DWD I/O、设备选择 | 8线程/32GiB预算通过；CPU-first accepted；见 `../acceptance/stock-stage-learning-b-capacity-20260908.md` |
| C | P3-STAGE-001-C | closed / #240 | 最小Facts扩展、历史逐日harness、候选/decision、独立oracle | real-DWD governed harness PASS；见 `../acceptance/stock-stage-learning-c-harness-20260908.md`；不训练分类器、不读future target |
| D | P3-STAGE-001-D | closed / #241 | T-only参考、development-only future settlement、Synthetic Expert Panel开发参考 | v2非重叠120例分层补样完成：87 definite / 21 ambiguous / 12 coexistence，14个分歧按冻结第三裁决，audit veto=0；见 `../acceptance/stock-stage-learning-de-v2-development-closeout-20260909.md`；证据仍为SYNTHETIC_EXPERT_CONSENSUS |
| E | P3-STAGE-001-E | closed / #242 | R0/R1有限试验台账、hard-gated开发选择、冻结候选 | 18个R1全部留痕，4个过硬门槛；冻结 `development-candidate.v1.json`：probe 1.0 / hold 2 / distance 0 / activity 1.0。hold=1因歧义过断言被拒，hold=3不退化门槛失败；0与0.0025 distance不可辨识，按最小变更选0；R2不触发 |
| F | P3-STAGE-001-F | closed / INCONCLUSIVE / #243 | 一次外样本、分状态/profile/用途资格包 | Job `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7`进入真实执行后在panel/预测/评分前因`qualification_mature_held`只有58个额外unique episodes、低于冻结80例要求而fail-closed；无qualification artifact/SHADOW/QUALIFIED结果；见 `../acceptance/stock-stage-learning-f-arbitration-inconclusive-20260909.md` |
| G | P3-STAGE-001-G | blocked after F inconclusive + 日更授权 | 新as-known profile、DWD READY后固定模型推断、ADM发布 | F未产生可消费标签版本；延迟/缺失/修正/漂移受控；不自动开启P6或下单 |
| H | P3-STAGE-001-H | ready / #245 / second-group development | v1 512只profile已成功：3,425个Stage-4起点、186,221条路径观察；`second-group-experiment.v1.json` 冻结5/6有限27组搜索且明确不合并任何第二组编号；下一步repair profile测7的damage→repair、10的双支撑角色+3/5/8持续未收复/reclaim、11的confirmed-failure后未收复反抽 | v1结果见 `../acceptance/stock-stage-learning-h-second-group-profile-result-20260909.md`；repair profile仍不输出正式stage label/primary、不选support role或阈值；第三组/消费者/外样本仍blocked |
| I | P3-STAGE-001-I | closed / #247 / protocol repair | 后继外样本采样控制协议 | `qualification-protocol.v2.json`保留F原target caps（mature-held仍80），但exact quota不足不再process crash；实际样本量进入冻结semantic hard gates，无跨层结果依赖回填；不读任何新holdout |
| J | P3-STAGE-001-J | blocked / #248 / successor holdout | development容量普查已反推并冻结未来cohort size=512；后续在未消费holdout上执行一次评价 | 2020-2021 census：64不够、128首次满足、256/512均满足；选择512是基于实测算力余量增加稀有样本储备，640个semantic contexts上限不变。结果见 `../acceptance/stock-stage-learning-j-development-capacity-census-result-20260909.md`。真实评价仍需预先冻结具体证券/seed/partition并独立授权；2022-2024禁止fresh reuse，2025仍`unopened_unselected` |

每项精确inputs/deliverables/acceptance/blocked_on由机器计划登记并同步到GitHub。A-F已关闭，其中F是**sample-feasibility INCONCLUSIVE**而不是模型REJECT：2022-2024 arbitration的一次预算已经消耗，不能修sample后重跑并称fresh holdout。I只修复未来协议；J保持holdout blocked。H当前只开放development-only第二组连续剖面，profile结果之后才允许另冻有限参数空间。2025 permanent-blind保持未触碰。

## 2. 谁负责什么

算法：规则/轻量模型实现、fold内训练与校准、可重复性、复杂度预算。量化：问题/对照、时序切分、有效样本、区间与试验台账。短线业务：可观察定义、参照角色、相邻反例、时效性、允许灰度。数仓作为跨项基础责任：源字段、单位、身份、知识视图、修正影响与发布。

本次早期三视角评审由单一助手分别执行，不是真人签字。D/E采用版本化 `SYNTHETIC_EXPERT_CONSENSUS`：不同风格原型互不可见地评审同一T-only证据，且明确不等同真人独立金标准；F继续沿用同一证据层级，因此即使外样本硬门槛通过，也只形成bounded SHADOW external-semantic evidence。未来如获得真实A股专业交易者/基金经理参考，可升级或对照为 `HUMAN_EXPERT_REFERENCE`。不能让用户因不懂算法而被迫审批任意阈值。

## 3. 当前下一步

当前不消耗新的holdout。H的 `second-group-profile.v1.json` 已在2020-2021 development、512只总体上成功完成：从冻结Stage-4接受点追踪最多60个有效交易日，得到3,425个起点与186,221条路径观察。结果显示两个尺度的当前回撤分布高度一致，5趋势背景与6局部回踩可以进入下一轮有限development学习；但首次支撑候选跌破后20/60尺度分别有约79.6%/77.1%最终收回，且收回中位仅3个交易日，因此一次跌破明确不足以定义10。7需新增T-only修复/恢复量，10/11需新增持续失效、未收复与支持角色F9证据。

v1 profile审计后已经决定：5/6以及10/11暂时都保持独立编号，不做合并。接下来先运行repair profile，再决定7是否能用“深损伤+修复比例”形成稳定定义、10应采用哪一类支撑角色及何种持续未收复条件、11在已确认失效且同支撑未收复前提下需要怎样的反抽证据。J真实holdout、G日更、第三组和消费者继续blocked；2025不动。

## 4. 原提案迁移

原`stock-stage-qualification-proposal.md`的STAGE-A/B对应新A/C；STAGE-C对应C；STAGE-D/E拆为D/E/F；STAGE-F对应G，新增B解决设备容量、H记录扩展/消费者边界。原提案记录作为历史保留，不再是并行执行清单。

## 5. 本轮验证

计划校验包括：稳定ID集合、依赖无环、先决项顺序、文档路径、P0分区一致以及每项显式权限。GitHub同步P3-STAGE-001和A-J，不能改动旧P3 fixture、Facts验收、Catalog或P6工单。原始规划收尾见 `../acceptance/stock-stage-learning-planning-closeout-20260908.md`；当前执行状态以上表及各 acceptance 为准。A-F与I关闭，G/H/J blocked。开发候选继续冻结，2022-2024 fresh holdout已消耗，2025/其他新holdout、正式QUALIFIED、日更与策略消费权限仍保持关闭。
