# 个股阶段：专家约束下的历史口径学习

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | blocked |
| Version | 0.1 |
| Parent | `stock-stage-qualification.md`; `lld/03-label-feature-system.md` |
| Dependencies | `stock-stage-data-dependencies.md` |
| Compute plan | `stock-stage-training-compute.md` |
| Review | `../reviews/stock-stage-learning-review-20260908.md` |
| Work items | `../work-items/stock-stage-historical-learning.md`; `../../config/stock-stage-learning-delivery.v1.json` |
| Authorization | P3-STAGE-001 A-F与I已关闭；F唯一2022-2024 arbitration预算已消耗并INCONCLUSIVE；J真实holdout继续blocked。第二组已mixed closeout：Stage 10/11随后由`P3-STAGE-002`正式化为可重叠的development Projection Contract，Stage 5/6/7为INCONCLUSIVE_PARKED。第三组P003-P026与P027 portfolio synthesis均已关闭：历史方向码1..12加neutral 0全部保留追溯，但flat single-valued FSM不再是实现架构；当前没有任何official peer primary stage。`P3-STAGE-028`完成Layered Stage Proposition Bundle第一阶段MVP；`P3-STAGE-029`进一步用全部10条已接受的真实历史Stage10-active retrospective观察完成Facts-only / comparison-single-code / layered replay：layered保留16/16显式parent+nested命题，single-code仅保留10/16且在6/6 nested rows省略Stage10父背景。`P3-STAGE-030`随后按四方推荐冻结strict `PUBLIC_RECONSTRUCTION`，但在任何PIT Stage predicate运行前被`BLOCKED_SOURCE_VERSION_OR_AVAILABILITY_LINEAGE`关闭：现有ODS/DWD没有证明完整的2020日线source-version/public-availability family，session-close仅是经济完成下界。四方blocker复审4/4只允许另开独立、受限的历史source-version evidence feasibility，不允许降级成PIT-lite。正式LabelCard、外样本、日更/生产消费者/策略/交易仍关闭；2022-2024不重读，2025继续未触碰 |

## 1. 决策摘要与继承关系

采用“专家语义约束 + 历史参数学习 + 独立验证”，不是让无监督算法随意聚出十二组再贴名字。十二状态是待验证的业务词汇，不是必须凑齐的模型输出。先做3/4突破尝试—接受及其相邻反例，随后按依赖扩展，允许合并、改为不同维度或仅保留连续事实。

本文件拥有历史学习、选参和定稿协议；`stock-stage-qualification.md` 继续拥有真值、歧义、验证分层和数据/消费边界；`stock-stage-data-dependencies.md` 继续拥有源字段及F1-F9缺口。不得用本方案改写已经接受的Facts或指标公式。

对外标签运行资格只用 SHADOW / QUALIFIED。DRAFT/RETIRED 是定义文档/版本管理状态，不增加四级产品审批。数据、语义、覆盖、稳定性和用途证据是同一个资格包的维度。qualification_score可以做摘要，不是权威加权通行证；消费者和交易权限另行验收。

首组冻结契约为 `config/stage-learning/experiment.v1.json`。H 的两个profile、首轮 development selection 与一次受限 reference supplement 均已完成。Supplement 只修复 Stage-6/11 reference sufficiency，hard gates和网格保持不变：Stage 11最终冻结5%未收复反抽，Stage-5/6联合网格仍无eligible candidate，Stage 7保持原真实INCONCLUSIVE，Stage 10维持5-session+2% candidate。`second-group-development-closeout.v1.json` 因此以mixed disposition关闭当前版本追参：10/11保留development candidate；5/6/7保留编号但INCONCLUSIVE_PARKED。量化、短线操作、基金三视角评审给出`PASS_WITH_SCOPE_REDUCTION`，要求连续Facts继续作为权威基线，且不再为凑齐标签扩参或降gate。随后 Route A `P3-STRUCT-005` 已将 support object/binding、same-support break/reclaim、unreclaimed process state、signed distance 与 post-break rebound 正式化为 bounded retrospective **演化支撑事实族（Support Evolution Fact Family，历史编号 F9）**；两次独立 cold real-DWD Job 语义完全复现。`P3-STAGE-002` 进一步把10/11从研究 runner 内部布尔量拆成版本化 Projection Contract：10是持续 structural-failure context，11是该context内的 current rebound condition；这两个命题可以共同成立而不等于发布两个 primary stages。其唯一 qualification Job 只消费既有 immutable F9 artifacts，得到 241 observations / 2 confirmations / 10 Stage-10 contexts / 6 Stage-11 conditions，legacy predicate mismatch=0 且 primary_stage 非空数=0；没有重读DWD。该里程碑已经关闭，primary-stage压缩和正式LabelCard继续分离。

## 2. 学习对象：不变的语义骨架与可学习参数

### 2.1 不可由优化器改变

参照的业务角色与因果选择法、代码/上市身份、观察尺度、知识截止、价格坐标、缺失处理、语义非承诺和输出维度须先冻结。反抽须有此前失效证据；突破接受须有同参照的接受证据；中性不是弃权；缺数据不能成为结构失效。

不使用当前前复权序列冒充当时价格，不把日K双触价位擅自排序，不把涨停不可买直接变成看空。8/9只描述可观察的加速/高位量价分歧，不声称已知顶部、主力意图或净资金流。

### 2.2 可以学习但必须有限、版本化

回撤/距离阈值、波动标准化尺度、活动阈值及权重、确认等待、滞回幅度、候选保留及弃权阈值。每项登记单位、范围、方向约束、是否允许交互和最大候选数。只有业务上有意义的量才进入搜索空间，不以全历史收益反向搜索数十种形态名。

阈值优化、特征选择、校准、状态合并/重命名和试验次数都属于学习；它们看到的结果不得再用于声称独立盲测。目标是“指定范围内的稳健口径”，不是全局最优。

## 3. 监督数据及候选语义

参考对象至少区分：

| reference_kind | 语义 | 学习/评价处理 |
| --- | --- | --- |
| definite | 同尺度同槽明确正例/反例 | 可拟合语义分类；保留独立参考集 |
| coexistence | 不同维度的命题同时成立 | 分维度或真正多标签；不可硬变单分类 |
| ambiguous_set | 同槽候选之间尚不能区分 | 第一版不把所有候选标为阳性；保留作候选/过度确定审计 |
| unjudgeable | 来源/历史/定义不足以裁判 | 不冒充阴性或正确弃权；单独记账 |

如果以后引入部分标签学习，必须另证“候选集合中恰有一个正确答案”的假设；真实共存不满足该假设。[R3] 人工票数或模型softmax都不自动代表客观状态概率。

原始规则弱标签只能用于工程测试、教学或明确标注的弱监督训练；不能成为独立资格测试答案。参考证据分层为 `HUMAN_EXPERT_REFERENCE > SYNTHETIC_EXPERT_CONSENSUS > WEAK_RULE_SELF_LABEL`。当前没有合适的真人A股专家资源，因此D允许使用不同交易/机构风格原型的隔离盲审作为development reference；公开人物姓名只作为便于理解的风格锚点，不冒充本人、不声称本人观点，也不把同一模型体系下的角色隔离夸大成真人统计独立性。正式资格报告必须保留该证据等级，不能写成“真人专家验证”。同一学习算法或弱规则不能生成答案再给自己评分。

### 3.1 开发参考预算与补样纪律

首轮120个episode-context可行性参考按80个预登记随机样本 + 40个边界/稀有反例执行。v1运行后仅得到3个definite Stage-3参考，且原diagnostic rank #1以更高歧义过度断言换取Stage-4 recall，因此该轮只保留为INCONCLUSIVE开发证据，不接受参数赢家。该结果可以指导新的development补样，但不能把首轮重新命名为独立测试。

v2补样仍在同一冻结32-security/2020开发池内，显式排除首轮120个context，并固定为40个started活动阈值识别、30个hold=1/2/3接受边界、20个近锚失效负例、15个reclaim歧义控制和15个mature-held共存控制。它是**result-aware development supplement**，不是confirmatory sample。Panel v2同时修正首轮“只要activity可用就把started判为Stage 3”的过宽语义，把活动强弱纳入两位主评和第三裁决的Stage-3判断。

v2已完成并关闭D/E：120个context解析为87 definite、21 ambiguous_set、12 coexistence，14个primary disagreement全部由冻结第三原型裁决，量化审计veto为0。R0在同一参考上的macro F1为0.8520、positive-reference coverage为0.7424；最终R1 development candidate为probe=1.0、hold=2、signed distance=0、activity=1.0，macro F1 0.9255、positive-reference coverage 0.8939，Stage 3从29/39提升到39/39且没有新增明确负例误报或歧义强判。原raw-score更高的hold=1组合因ambiguity over-assertion被hard gate拒绝，hold=3组合因Stage-4精度下界/不退化失败被拒绝；signed distance 0与0.0025当前不可辨识，选择0只是较低复杂度/不改R0的tie break，不声称市场真值。R2因此不触发。权威证据为 `../acceptance/stock-stage-learning-de-v2-development-closeout-20260909.md` 和 `../../config/stage-learning/development-candidate.v1.json`。

每个context仍由两个互不可见的合成专家原型主评，分歧由第三原型裁决，机构挑战审只提供反证提醒，量化结构审计员只做PIT/证据闭包否决。未来切换真人专家时沿用同一T-only packet和resolution协议。原始/补样都只用于开发口径可辨识性和选参；最终外样本参考必须另行抽样并冻结。没有足够独立episode时，不把相邻股票日虚增成独立证据。

## 4. 双账本：描述学习不偷换成获利筛选

语义账本：截至T的事实、独立参考、候选、stage_confidence、主阶段/歧义、正反证。未来账本：独立TargetCard、冻结参照、观察horizon、成熟结算和用途评价。分类器无权读取未来账本。

用训练期未来结果训练“未来是否守住支撑”的预测器是允许讨论的方法，但它是明确的ForecastSpec，不得因此把原阶段定义改成“只有后来上涨才算回踩”。训练目标必须在该训练折截止前成熟；测试期目标只能用于最终结算。

未来结果不能以“未来分类器说进入5/10”作为唯一真值。应从未来价格、冻结边界及已批准公司行动/执行规则独立结算；输出 settled / pending / censored / unavailable，并明确日内顺序不可判定。结果分布相似可削弱交易区分主张，不自动否定两个标签的描述用途。

## 5. 三条算法路线及比较纪律

| 路线 | 定义 | 第一版职责 |
| --- | --- | --- |
| R0 | 专家固定规则 | 最小可解释基线；不可事后换成较弱基线 |
| R1 | 语义约束内有限阈值/权重搜索 | 主路线；CPU向量化评估，联合校准相邻标签 |
| R2 | 正则化逻辑回归；必要时一个受限浅树/直方图提升树对照 | 只有R1出现可归因的表达不足才启用，不默认增加深度模型 |

三条路线共享被批准的输入信息、数据可用性总体、源版本、参考和时间切分。连续Facts另作信息压缩对照。R2不能多拿未来、行业或额外长历史后再宣称比R1好。对仍未评价的类别不能编码成全负例。

建议初始计算预算：R1每个相邻标签组最多32个事前列举候选、3个时间折；R2总共最多12个预登记配置且与R1共享算力上限。重复种子也是试验，全部计账。执行前A/B根据实测冻结更小预算；扩大预算须新批准，不能拿超时当作无限重跑许可。

先排除硬门槛失败，再按冻结的语义质量/覆盖/确认时延多目标选择；在统计差异不能分辨的候选中优先低复杂度、低时延和参数邻域稳定者。不在一个缺乏根据的五项加权总分上选赢家。[R1]

### 5.1 可执行的学习/验证顺序

1. 冻结taxonomy/profile、非承诺、hard predicate、允许参数空间、实验预算。
2. 在获准开发区构建一次受治理的输入特征面板及episode索引；特征量依赖窗口被搜索时，只缓存预先列举的版本，不漏记预处理成本。
3. 生成T-only参考与独立future targets，物理/读取接口隔离；先审源再标注。
4. 时间折内拟合标准化/特征选择/R1或R2；验证折只能transform/predict，不能fit。
5. 对同槽候选进行联合消歧；不同维度不做无意义的top1竞争。
6. 用独立开发校准分片或前向out-of-fold预测校准分数；另留阈值选择分片，不拿最终测试调确认阈值。[R2]
7. 完成语义、灰度、覆盖、参数邻域、时间稳定和独立未来分布审查，锁定一个候选版本。
8. 在新授权的保留分区一次性评价；F首轮已按 `qualification.v1.json` 消耗2022-2024 arbitration预算，但在panel/预测/评分前因最终`qualification_mature_held`分层仅58个额外unique episodes、低于冻结80例而INCONCLUSIVE。该分区不能修采样后重跑并称fresh holdout。I随后冻结 `qualification-protocol.v2.json`：原五层target cap和hard gates数值不变，但稀有stratum不足时保留实际样本、发布shortfall并继续到semantic gates，不做结果依赖跨层补样。J只有在未来未消费holdout被独立选择/授权后才能实现/执行；2025 permanent-blind继续未触碰。
9. 以同一冻结版本做CPU/平台推断兼容、批量vs逐日一致性检查，再申请观察性日更与按用途资格。

每次资源失败先归因，不在仍运行的批次中热改规则；终止/保留本批次证据后新版本重启。跨ARM/x86或不同BLAS训练不要求模型字节相同：固定环境内可复现，跨环境要求事前容差及决策差异账本。冻结发布模型的消费语义仍须一致。

## 6. 时间分区与泄漏防护

当前权威 `config/p0-policy.v1.json`：2012-2021 development、2022-2024 arbitration、2025 permanent blind。`config/p3-gates.v1.json`继续把后两者列为一般 `unopened_partitions`；`bounded_partition_exceptions` 保留F的历史窄例外，但其状态现为 `consumed_inconclusive`、remaining budget为0，并绑定唯一Job。它不再提供任何可执行arbitration权限。

默认保留现有分区。本轮不把2019-2024变成训练区，也不读取2025。2020-2021已被development反复研究；2022-2024已被F访问并暴露至少sample-population信息，因此也不能再次称未使用的confirmatory holdout。2025 permanent-blind继续留给更高证据等级/后续真正确认性需求，只有新的预登记协议与独立授权才能打开。

2019-2024训练/2025测试是可供负责人以后审批的替代方案，必须先修改分区策略与下游验证用途；训练过的2022-2024不能继续当原项目仲裁证据。仅重命名实验不能清除已知信息。

按整个股票面板的日期分折，不随机拆股票日。对监督目标跨折的样本按真实target起止时间purge；依据最长目标成熟时间设置隔离，不机械把lookback天数当作唯一gap。同episode多行使用事件级抽样/权重和时间块不确定性，不能虚增有效样本。类别权重、标准化、特征选择、校准和置信阈值都必须只使用相应训练/校准分片。[R1,R2]

测试时允许使用T以前的历史窗口与因果状态检查点，但不能用测试未来完结的波段选择起点、支撑或训练标签。历史总体按当时上市身份定义，包括获准范围内后来退市对象；不得用今天存活名单或未来质量问题筛掉过去失败样本。

Facts V2 当前只保证bounded retrospective lane。规模化历史训练先申明retrospective限制，不将后来修订的DWD冒称platform-as-known。现有B1-B5 A/B并不证明全历史特征或训练面板已可用。

## 7. stage_confidence与多候选

业务名称允许“分类置信度”，schema使用stage_confidence并绑定score_semantics、score_version。未校准时是非概率证据支持；不要展示为胜率。分别任意归一化的不同标签分数不天然可比，只有相同参考目标/量纲和验证支持时才用confidence_margin。

若输出概率，必须说明概率是“独立参考认为该命题成立”还是“未来目标事件发生”，并绑定校准总体、分片、校准版本；两者不能共用概率名。校准曲线与proper score一起看，Brier改善不单独证明校准。[R2]

推断先分类可用性，再独立评价候选、区分共存/歧义/冲突。同槽只在证据充分且允许唯一时输出primary；否则保留candidate_set及原因。Qualified后仍可歧义，不能删去尚未合格的强竞争候选后制造唯一赢家。类别资格未通过不等于证据上的阴性。

## 8. SHADOW -> QUALIFIED：一个资格包，不是四级生命周期

资格单位为taxonomy/rule/model版本 × 状态或相邻状态组 × profile/尺度 × 知识视图 × 适用总体 × 允许用途。正式研究/展示资格不自动授予策略行动权。

建议以下作为A的起始验收默认值，全部是待试标校准后、确认性结果前冻结的工程/业务容忍，不是行业标准或市场规律；缺字段阻止训练结果被称为已资格化：

| 维度 | 拟议起始约束 / 判据 |
| --- | --- |
| 硬正确性 | 已登记测试中未来泄漏、身份/坐标串线、不可重放、缺数据当看空、同槽多个primary均零容忍 |
| 明确样本质量 | 各拟合格状态precision的单侧95%下界建议至少0.80；10失效等高误判代价类别另提高，先不进入首轮 |
| 不退化 | 固定覆盖比较下，宏F1相对R0的非劣容忍建议0.03；不能靠几乎不出标签抬高precision |
| 覆盖与歧义 | 同时计attempted和ready分母；明确参考样本中的confirmed覆盖建议不低于0.60；合理歧义中未经支持的唯一断言率单侧95%上界建议不高于0.10 |
| 有效样本 | 每拟合格状态建议至少100个可裁判独立episode-context，并满足冻结的区间精度；100不是自动充分条件，聚类后不足则INCONCLUSIVE |
| 候选与延迟 | 首个互斥对的候选最多2个；按事前可确认条件定义延迟基点，首轮3/4的额外确认延迟p90建议不高于2个交易时段；不是要求突破当天就被判为接受 |
| 稳健性 | 冻结跨折、流动性/波动切片与相邻参数检查；无证据的slice不批准，不以总体均值掩盖 |
| 用途 | 描述资格不强求未来收益显著；声称预测则另须冻结target与基线并通过区间/多重试验控制；具体策略消费另走P2 |
| 日更运行 | 新as-known输入profile、延迟/缺失/修正重放、冻结模型CPU消费和影子发布验证通过；不能由历史retrospective验证代替 |

盲审参考的可接受集合不等于其中任何单值断言都被支持：当评审明确认为不可消歧时，唯一断言需另有证据。无独立参考或有效样本不够只能SHADOW/INCONCLUSIVE，不降低门槛。

A须把数字、分母、区间算法、bootstrap块长/聚类、损失优先次序、延期/失败动作写入实验manifest。负责人不必凭经验替算法选32个阈值；由技术侧形成有依据的默认配置，再提交范围和预算审批。

## 9. 实施顺序与扩展

第一组3/4保留为有质量的breakout event-phase development candidates，但唯一2022-2024外样本运行在语义评分前因sample-feasibility关闭，因此没有official资格。第二组5/6/7/10/11已mixed closeout：5/6/7保持INCONCLUSIVE_PARKED并继续消费连续Facts；10/11由`P3-STAGE-002`正式化为persistent structural-failure context + nested rebound condition，可共同成立且`primary_stage=null`。第三组0/1/2/8/9/12已完成P003-P026：0 primary资格park、2/order strong exploratory、12/1形成downside context + nested stabilization但parent reference no-pass、8为research-only acceleration/extension modifier且资源路线no-pass、9为等待activity relation的modifier hypothesis。`P3-STAGE-027`将三组合并为分层vocabulary portfolio；`P3-STAGE-028`把该结构落实为可执行Bundle契约和non-production development-shadow view；`P3-STAGE-029`再用已接受真实历史F9产物完成受限replay，证明layered在6/6 nested观察中显式保留Stage10父背景，而comparison-only单码会省略该父背景。`P3-STAGE-030`已把下一关精确定义为strict historical public reconstruction，并在能力审计发现2020日线版本/公开时点证据族不完整后fail-close；没有任何Stage PIT结果。当前不重开逐code补样、不做primary-stage压缩。四方只授权一个与Stage执行隔离的历史source-version evidence feasibility successor；其成功最多产生source candidate。

每组都走相同的局部学习+全体系冲突审计；不得独立优化12个标签后直接拼接。每组输出保留/合并/改维度/拒绝决定。第三组尤其禁止把`0`做成“其他都不满足”的剩余类，禁止把`8/9`的“赶顶/派发”当可观测事实；若难以辨识则停在观察性描述/修饰命题，不能迫使B6/B7无限扩张。

复用生产模块：`structure_v2.py`及其输入/时间契约、`feature_registry.py`、`label_registry.py`、`label_audit.py`、`expert_evaluation/target_cards.py`、`resolver.py`、`scoring.py`与现有P2。未来新增代码计划归`src/quanthus/stage_learning/`，拟合依赖为独立可选环境，不引入lean API或运行中的Event Worker。

新增数据产品须有独立asset policy/manifest后才进Catalog。拟议reference、candidate、decision、settlement、qualification是不同权限角色，可作为一个受治理包的成员，不要求为了治理再建五套服务/数据库。

## 10. 工程与算力原则

详见compute plan：共享特征一次构建，搜索不重复扫描全DWD；CPU-first；训练和正式服务隔离；测量后才扩规格。所有stage记录耗时、峰值内存、输出行/字节、重用情况和完整输入闭包。源市场数据、模型、参考集和运行产物不入Git，只提交代码、规格与小型证据摘要。

当前只读核查不等于性能验收。A/B获准后才安装独立训练环境和运行限额基准；不修改系统驱动，不直接使用现有16GiB Worker配额承载新训练。

## 方法依据

- [R1] scikit-learn，Cross-validation（训练/选择/测试隔离、预处理只拟合训练集）：https://scikit-learn.org/stable/modules/cross_validation.html
- [R2] scikit-learn，Probability calibration：https://scikit-learn.org/stable/modules/calibration.html
- [R3] Cour, Sapp, Taskar (2011), Learning from Partial Labels：https://jmlr.org/papers/v12/cour11a.html
- [R4] Cawley, Talbot (2010), On Over-fitting in Model Selection：https://www.jmlr.org/beta/papers/v11/cawley10a.html

以上是方法参考，不构成Quanthus标签质量或硬件速度的实证。检索日期2026-09-08（Asia/Shanghai）。
