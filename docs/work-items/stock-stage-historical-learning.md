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
| Current scope | A-F已关闭，F为sample-feasibility INCONCLUSIVE；I已关闭；J仅冻结未来cohort size=512且真实holdout继续blocked。H现唯一ready：首轮 `second-group-development.v1.json` 已成功执行但整体INCONCLUSIVE；Stage 10冻结5-session+2% candidate，Stage 6/11因ambiguous reference 9/7低于hard gate=10无法通过，Stage 7在参考充分下真实INCONCLUSIVE。当前只授权 `second-group-development-supplement.v1.json` 增补6/11 references并重跑原网格；10不得重搜、7不得扩网格。5/6与10/11保持独立编号；2025、正式QUALIFIED、日更、消费者和策略权限仍关闭 |

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
| H | P3-STAGE-001-H | ready / #245 / second-group development supplement | 首轮共享512只 development Job 已成功执行：10冻结5-session+2%，6/11被reference-sufficiency gate阻塞，7为真实INCONCLUSIVE。当前 supplement 保留原120 contexts并只为6/11每bucket追加target cap=20、排除原episode；hard gates与网格不变，只重跑必要5/6和11。10直接复用，7只做immutable trial audit不重跑 | supplement后无论冻结成功还是继续INCONCLUSIVE都停止追参并形成milestone disposition；随后按用户要求进入量化/短线/基金专家评审；无正式stage label/消费者/holdout |
| I | P3-STAGE-001-I | closed / #247 / protocol repair | 后继外样本采样控制协议 | `qualification-protocol.v2.json`保留F原target caps（mature-held仍80），但exact quota不足不再process crash；实际样本量进入冻结semantic hard gates，无跨层结果依赖回填；不读任何新holdout |
| J | P3-STAGE-001-J | blocked / #248 / successor holdout | development容量普查已反推并冻结未来cohort size=512；后续在未消费holdout上执行一次评价 | 2020-2021 census：64不够、128首次满足、256/512均满足；选择512是基于实测算力余量增加稀有样本储备，640个semantic contexts上限不变。结果见 `../acceptance/stock-stage-learning-j-development-capacity-census-result-20260909.md`。真实评价仍需预先冻结具体证券/seed/partition并独立授权；2022-2024禁止fresh reuse，2025仍`unopened_unselected` |

每项精确inputs/deliverables/acceptance/blocked_on由机器计划登记并同步到GitHub。A-F已关闭，其中F是**sample-feasibility INCONCLUSIVE**而不是模型REJECT：2022-2024 arbitration的一次预算已经消耗，不能修sample后重跑并称fresh holdout。I只修复未来协议；J保持holdout blocked。H当前已从连续剖面推进到一次development-only有界搜索runtime；它不读取future outcome、不打开holdout，也不能在运行中扩大参数空间。2025 permanent-blind保持未触碰。

## 2. 谁负责什么

算法：规则/轻量模型实现、fold内训练与校准、可重复性、复杂度预算。量化：问题/对照、时序切分、有效样本、区间与试验台账。短线业务：可观察定义、参照角色、相邻反例、时效性、允许灰度。数仓作为跨项基础责任：源字段、单位、身份、知识视图、修正影响与发布。

本次早期三视角评审由单一助手分别执行，不是真人签字。D/E采用版本化 `SYNTHETIC_EXPERT_CONSENSUS`：不同风格原型互不可见地评审同一T-only证据，且明确不等同真人独立金标准；F继续沿用同一证据层级，因此即使外样本硬门槛通过，也只形成bounded SHADOW external-semantic evidence。未来如获得真实A股专业交易者/基金经理参考，可升级或对照为 `HUMAN_EXPERT_REFERENCE`。不能让用户因不懂算法而被迫审批任意阈值。

## 3. 当前下一步

当前不消耗新的holdout。H的两个development profile均已完成。repair profile进一步证明：acceptance anchor在20/60尺度约77%/74%的路径会被跌破，更像局部突破接受边界；更深的initial-low support仅约29%/13%被跌破，且首次跌破中位在26/28个交易日，更适合作为Stage-10主支撑候选。即便如此其跌破仍约80%/77%最终收回，因此10必须同时要求持续未收复和足够跌破深度。7则用damage与T-only repair fraction分离“深跌”与“正在修复”；11只能在冻结10之后、同一失败支撑仍未收复时评估反抽。

首轮 `second-group-development.v1.json` 已经执行完毕：Job成功但 disposition=INCONCLUSIVE。10在原网格内冻结为5-session+2% depth；6与11分别只有9/7个ambiguous references，低于预登记minimum=10，因此是corpus-sufficiency blocker；7有42 positive / 58 negative / 20 ambiguous却仍无候选过gate，定性为真实INCONCLUSIVE。当前唯一执行项改为 `second-group-development-supplement.v1.json`：只补6/11 reference，不改hard gates，不重搜10，不扩或重跑7。supplement后即停止development追参并进入量化、短线操作、基金专家评审。J真实holdout、G日更、第三组和消费者继续blocked；2025不动。

## 4. 原提案迁移

原`stock-stage-qualification-proposal.md`的STAGE-A/B对应新A/C；STAGE-C对应C；STAGE-D/E拆为D/E/F；STAGE-F对应G，新增B解决设备容量、H记录扩展/消费者边界。原提案记录作为历史保留，不再是并行执行清单。

## 5. 本轮验证

计划校验包括：稳定ID集合、依赖无环、先决项顺序、文档路径、P0分区一致以及每项显式权限。GitHub同步P3-STAGE-001和A-J，不能改动旧P3 fixture、Facts验收、Catalog或P6工单。原始规划收尾见 `../acceptance/stock-stage-learning-planning-closeout-20260908.md`；当前执行状态以上表及各 acceptance 为准。A-F与I关闭，H为唯一ready development任务，G/J blocked。首组开发候选继续冻结，第二组仅开放当前一次有界development search；2022-2024 fresh holdout已消耗，2025/其他新holdout、正式QUALIFIED、日更与策略消费权限仍保持关闭。
