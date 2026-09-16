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
| Current scope | A-F/I已关闭，F为sample-feasibility INCONCLUSIVE；J真实holdout继续blocked。后续分组研究现已由P027统一收口：10/11为accepted development context+nested projection，3/4为frozen development event candidates且外样本qualification inconclusive，5/6/7 parked，0 primary资格park、2 strong exploratory、12/1 layered research no-pass、8 research modifier资源no-pass、9 activity-blocked modifier。flat single-valued FSM已被supersede为分层vocabulary portfolio；当前唯一ready child为P3-STAGE-028 schema/governance contract，primary_stage继续null/absent；2025、正式QUALIFIED、日更、消费者和策略权限仍关闭 |

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
| H | P3-STAGE-001-H | blocked / #245 / second-group mixed closeout | 首轮与supplement均已完成。10冻结initial-low + 5-session + 2%；11冻结prior-10 + same-support unreclaimed + 5% rebound；5/6补样后仍无联合eligible candidate，7保持真实INCONCLUSIVE。`second-group-development-closeout.v1.json` 将5/6/7标记INCONCLUSIVE_PARKED并关闭追参 | 三视角review=`PASS_WITH_SCOPE_REDUCTION`；10/11可进入未来新scope，5/6/7只保留连续Facts与编号；当前无active search、无正式label/consumer/holdout |
| I | P3-STAGE-001-I | closed / #247 / protocol repair | 后继外样本采样控制协议 | `qualification-protocol.v2.json`保留F原target caps（mature-held仍80），但exact quota不足不再process crash；实际样本量进入冻结semantic hard gates，无跨层结果依赖回填；不读任何新holdout |
| J | P3-STAGE-001-J | blocked / #248 / successor holdout | development容量普查已反推并冻结未来cohort size=512；后续在未消费holdout上执行一次评价 | 2020-2021 census：64不够、128首次满足、256/512均满足；选择512是基于实测算力余量增加稀有样本储备，640个semantic contexts上限不变。结果见 `../acceptance/stock-stage-learning-j-development-capacity-census-result-20260909.md`。真实评价仍需预先冻结具体证券/seed/partition并独立授权；2022-2024禁止fresh reuse，2025仍`unopened_unselected` |

每项精确inputs/deliverables/acceptance/blocked_on由机器计划登记并同步到GitHub。A-F已关闭，其中F是**sample-feasibility INCONCLUSIVE**而不是模型REJECT：2022-2024 arbitration的一次预算已经消耗，不能修sample后重跑并称fresh holdout。I只修复未来协议；J保持holdout blocked。后续分组实验已按各自冻结gate完成或park，并由P027统一为分层portfolio；当前无active逐code搜索，唯一ready scope是P028 representation schema/governance，不需要新市场读取。2025 permanent-blind保持未触碰。

## 2. 谁负责什么

算法：规则/轻量模型实现、fold内训练与校准、可重复性、复杂度预算。量化：问题/对照、时序切分、有效样本、区间与试验台账。短线业务：可观察定义、参照角色、相邻反例、时效性、允许灰度。数仓作为跨项基础责任：源字段、单位、身份、知识视图、修正影响与发布。

本次早期三视角评审由单一助手分别执行，不是真人签字。D/E采用版本化 `SYNTHETIC_EXPERT_CONSENSUS`：不同风格原型互不可见地评审同一T-only证据，且明确不等同真人独立金标准；F继续沿用同一证据层级，因此即使外样本硬门槛通过，也只形成bounded SHADOW external-semantic evidence。未来如获得真实A股专业交易者/基金经理参考，可升级或对照为 `HUMAN_EXPERT_REFERENCE`。不能让用户因不懂算法而被迫审批任意阈值。

## 3. 当前下一步

当前不消耗新的holdout，也不再逐code追参。P027已经把历史码1..12与neutral 0的证据等级和语义角色统一到`stock-stage-vocabulary-portfolio.md`：连续Facts是权威底座，context/structural state、event/nested condition、modifier分层表示；`X`是control，`ABSTAIN`是epistemic outcome。没有任何历史码当前被授权为official peer primary stage。

下一步P3-STAGE-028只冻结Layered Stage Proposition Bundle的schema与治理：stable proposition id、historical code traceability、evidence grade、coexistence/ambiguity、Fact references、ABSTAIN/X边界以及哪些accepted development projection可进入governed shadow surface。v1必须保持`primary_stage` absent/null，representation不能自动晋级research/parked proposition。J真实holdout、G日更、正式LabelCard/消费者/策略继续blocked；2025不动。

## 4. 原提案迁移

原`stock-stage-qualification-proposal.md`的STAGE-A/B对应新A/C；STAGE-C对应C；STAGE-D/E拆为D/E/F；STAGE-F对应G，新增B解决设备容量、H记录扩展/消费者边界。原提案记录作为历史保留，不再是并行执行清单。

## 5. 本轮验证

计划校验包括：稳定ID集合、依赖无环、先决项顺序、文档路径、P0分区一致以及每项显式权限。GitHub同步P3-STAGE-001和A-J，不能改动旧P3 fixture、Facts验收、Catalog或P6工单。原始规划收尾见 `../acceptance/stock-stage-learning-planning-closeout-20260908.md`；当前执行状态以上表及各 acceptance 为准。A-F/I关闭，G/J及父工单仍受原外样本/日更gate约束；H后继的分组研究已由P027完成portfolio synthesis。当前唯一ready任务是P028 schema/governance；2022-2024 fresh holdout已消耗，2025/其他新holdout、正式QUALIFIED、日更与策略消费权限仍保持关闭。
