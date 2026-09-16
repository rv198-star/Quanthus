# 个股阶段标签：可辨识性、不确定性与验证设计

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | blocked |
| Authority | 验证/不确定性设计权威；F唯一2022-2024 arbitration预算已消耗并INCONCLUSIVE，remaining budget=0；I冻结 `../../config/stage-learning/qualification-protocol.v2.json`，J真实holdout继续blocked。第二组mixed closeout后，10/11由`P3-STAGE-002`正式化为可重叠context+nested development Projection Contract，5/6/7保持INCONCLUSIVE_PARKED。第三组P003-P026与P027 synthesis均已关闭；`P3-STAGE-028`已接受Layered Stage Proposition Bundle第一阶段MVP；`P3-STAGE-029`又在全部10条已接受真实历史Stage10-active retrospective观察上验证 layered development-shadow 对 parent+nested 语义的显式保留（16/16），comparison-only单码只保留10/16并在6/6 nested rows省略Stage10父背景。`P3-STAGE-030`随后冻结strict `PUBLIC_RECONSTRUCTION`/T=15:30/10-of-10 gate，但在任何PIT Stage predicate运行前因现有ODS/DWD缺完整2020日线source-version/public-availability family而关闭为`BLOCKED_SOURCE_VERSION_OR_AVAILABILITY_LINEAGE`。P030不改变10/11资格等级；四方4/4仅批准另开隔离的历史source-version evidence feasibility。`P3-STAGE-031`已对SSE/SZSE官方historical EOD archive family关闭`NO_PASS_OFFICIAL_EXCHANGE_EVIDENCE_INSUFFICIENT`：source authority/coverage不足以替代exact version/correction lineage和T=15:30 public-availability proof。四方4/4仅允许一个更小的官方Level-1 intraday/realtime evidence feasibility。`P3-STAGE-032`已关闭`NO_PASS_LEVEL1_HISTORICAL_DISSEMINATION_EVIDENCE_INSUFFICIENT`：SSE历史Level-1 snapshot的采样/去重/cleaned归档和SZSE snapshot的生成时间/不可重传语义均不足以证明exact retained historical version在T=15:30的contemporaneous dissemination。阶段级四方评审4/4决定停止当前Stage historical PIT source-evidence hunt；不打开商业source或PIT-lite。`P3-STAGE-033`随后只验证consumer/explanation utility：机械预登记门槛强通过（Facts-only关键错误12/20，Facts+Layered 0/20），但4/4结果审计认定information equivalence未建立，因而研究结论关闭`INCONCLUSIVE_CONSUMER_UTILITY_ATTRIBUTION_MECHANICAL_GATE_PASS`。这只支持当前Layered包更可靠地传达已推导10/11语义，不授权把差异归因于纯Layered表达。Stage10/11继续保持accepted development projection + P029 retrospective development-shadow，strict PIT未检验；Continuous Facts继续为权威底座。当前无active逐code补样/搜索权限；2022-2024不重读，2025、QUALIFIED、LabelCard、日更、生产消费者或交易仍未开放 |
| Version | 0.1 |
| Parent | `stock-stage-vocabulary-portfolio.md`; `lld/03-label-feature-system.md`（`stock-stage-state-machine.md`仅保留历史词汇/转移图） |
| Facts boundary | `structure-facts-v2.md`; `../../config/structure-facts-v2-blueprint.v1.json` |
| Dependency contract | `stock-stage-data-dependencies.md` |
| Proposed delivery | `../work-items/stock-stage-historical-learning.md`; 原STAGE-A/F提案保留为历史 |
| Review provenance | 单一助手按量化验证、短线交易、数仓三个视角交叉审查；不是三位独立专家签字或实证验收 |

## 1. 本次要解决的问题

历史口径学习、有限选参与定稿现在由子设计 `stock-stage-historical-learning.md` 具体化；设备规划见 `stock-stage-training-compute.md`。本文件继续拥有验证与不确定性边界，对外标签资格仅用 SHADOW / QUALIFIED，多维证据不是新增生命周期。D/E development已关闭；F的单次2022-2024 arbitration访问已经消费且不能重跑。它在panel/预测/评分前因冻结mature-held 80例要求只有58个额外unique episodes而INCONCLUSIVE，因此没有形成SHADOW或QUALIFIED证据。

Shadow 是运行方式，Qualified 是一个需要证据支撑的资格结论；两者之间必须有可执行的判定机制，不能因为定时任务稳定、图表看起来合理或运行了一段时间就升级。

本提案把“12 状态”视为待检验的市场描述体系，而不是已经存在标准答案、只差编程的十三分类任务。既要验证算法是否遵守规则，也要验证规则是否能区分用户关心的情形，进一步验证其市场假设与策略用途。允许结果是保留、合并、改为修饰维度、降为展示或拒绝某个状态，而不是为了凑足十二个编号提高规则复杂度。

当前仍不注册新的QUALIFIED LabelCard、不启用日更，也不改变正式P6权限。第二组和10/11 Projection Contract均已关闭，primary-stage仲裁继续延后。第三组P003-P026也已完整关闭：Stage0/2的有限路线最终由P019按portfolio原则收口；P020保留Stage12 context + Stage1 nested condition但reference gate no-pass；P026保留Stage8 acceleration/extension modifier并在唯一资源路线上no-pass，Stage9继续等待activity relation。P027进一步确认这些结果不是要重新压回一个互斥槽，而应分层表示。正式Projection/LabelCard仍须新scope；P028只允许schema/governance，不授权任何research-only/parked proposition升级。阈值外样本、HUMAN_EXPERT_REFERENCE、primary-stage仲裁和产品资格仍未验证；2022-2024不重读，2025 permanent-blind保持未触碰。

## 2. 仓库核查与需要纠正的前提

1. `stock-stage-state-machine.md` v0.3 现已被P3-STAGE-027标记为**superseded flat architecture**：1-12的历史方向码、neutral 0、X与ABSTAIN的词义/转移图仍保留追溯，但实现权威转到`stock-stage-vocabulary-portfolio.md`。这不是十二状态验收；恰恰说明编号不再代表同层互斥输出。
2. Facts V2 B1-B5 只在固定 development/retrospective 切片接受了 20/60 参照、距离、同参照交互和最小过程。`structure_v2_inputs.py` 的 `DailyInput` 有 volume，并单独记录 missing_volume；但 `structure_v2.py` 的结果对象没有已验收的量比/活动 Fact 家族。旧版 `structure_facts.py`、`baseline_research.py` 存在相关计算，不等于已经完成 V2 B7 集成或坐标对齐。
3. 当前 V2 过程是每证券/窗口一个高点突破—失守—收复过程，不是完整支撑破坏、低点抬升、趋势结束识别器。跌回突破位以下不自动等于状态10的关键支撑失效。
4. 已完成的 `P3-STRUCT-002` 受限实验选择 `prefer_continuous_structure_facts`。其 C2 粗状态相对 C1 连续事实收益更低、回撤更大，区间证据没有证明前向优势。证据在 `../acceptance/structure-intelligence-economic-final-acceptance-20260906.md` §3、§6。它不是对所有十二状态的否定，但要求后续始终保留同信息集连续事实基线。
5. 已有 `label_registry.py`、`label_audit.py`，以及专家评估的 target card / independent resolver / scoring / OutcomeSettlement 边界，可以复用。不能再造一套仅由“每日成功运行”决定标签正确性的机制。

## 3. 三视角评审：问题、分歧与处理

| 视角 | 核心质疑 | 本提案处理 |
| --- | --- | --- |
| 量化验证 | 规则输出不能再由同一套规则充当真值；未来涨跌也不能直接裁定当前形态名称对错 | 分开规则符合性、盲审语义参考、独立未来目标和完整策略效果 |
| 量化验证 | 始终输出多个状态或始终弃权都能制造漂亮的“准确率” | 同时报告单值覆盖、错误风险、候选集大小、歧义与数据不可用分母 |
| 短线交易 | 5 上升推进可同时包含 6 回踩；3 突破是事件，5 更像持续背景 | 先记录趋势背景/局部行为/关键位关系，再按批准的粒度压缩主阶段；没有明确压缩规则不强制 top-1 |
| 短线交易 | “赶顶”“派发”“企稳”容易使用事后信息或猜测资金意图 | 当时可观察描述与未来假设分开；8/9 不声称已知顶部或主力行为；1 不声称未来不会新低 |
| 短线交易 | 高命中率可能靠等太久才确认，失去时效性 | 按状态报告确认延迟、错过的价格移动和翻转率，不能只评估最终图形 |
| 数仓 | 字段存在不等于单位、PIT、历史长度、坐标已经可用 | 依赖精确到源字段、转换和版本；区分 REQUIRED / OPTIONAL / OUTCOME_ONLY；逐族准入 |
| 数仓 | “昨日状态”与滚动窗口重算可能让历史变化；修正包又可能影响长期 episode | 不可变判断记录 + 独立结算；状态检查点绑定完整输入闭包，修正从受影响节点重放至依赖收敛 |
| 三视角共同约束 | 全市场每天打十二状态不应成为下一个默认动作 | 先做小范围可辨识性与离线验证，再审批观察性日更；上线运行不等于获准策略使用 |

量化方要求互斥单值以便检验，短线视角要求表达多个同时成立的行为。折中不是多标签乱发，而是：不同维度/不同尺度允许并存；同一主阶段槽最多一个已确认值，无法消歧时保留候选与原因。

## 4. “对错”必须拆成四个问题

| 验证层 | 要验证什么 | 参考或裁判 | 不能据此声称什么 |
| --- | --- | --- | --- |
| Q1 计算与证据符合性 | 给定冻结规则、输入和截止时间，计算/转移是否正确 | 独立最小参考实现、人工可算反例、源行与版本审计 | 规则有市场价值 |
| Q2 同时点语义可辨识性 | 截至 T 的证据是否足以把这个状态和相邻状态区分 | 预测盲、未来盲的独立评审和保留歧义的参考集 | 评审者命名是客观市场真值 |
| Q3 市场假设有效性 | 某状态/连续量对应的预登记未来事件或分布是否有用 | 独立的 future DWD resolver + 对照 + 不确定性 | 状态日后的每次失败都是当时误判 |
| Q4 使用价值 | 引入标签是否改善特定研究任务或策略 | 同信息集基线、固定政策/成本的完整 P2、展示任务实验 | 描述合格就能交易，或收益好就证明语义正确 |

运行成功率、时延、增量一致性单列为工程运行指标，不替代 Q1-Q4。

例：T 收盘时突破保持条件成立，T+2 又跌破，可以是“当时描述正确、之后延续失败”；不得回写 T 为从未突破。若声称的是“未来 N 个交易时段不会跌破”，则必须绑定独立 ForecastSpec，并在到期后裁定该预测，而不是偷换当前状态含义。

## 5. 状态的对象与时间粒度

候选槽至少绑定：证券和上市身份、观察截止时点、周期/尺度 profile、价格坐标、参照角色和目标、分类体系版本、规则版本、知识视图。

建议先冻结一个日线短线观察 profile。当前 V2 的 20/60 是既有参照窗口，不等于“短线只能持有20/60天”，也不能被默认为未来目标的 horizon。观察窗口、最低历史、确认等待、未来验证窗口是四个不同参数。

主阶段只是对结构化观察的可选压缩：

- 趋势背景：上行 / 区间 / 下行 / 无法确认；
- 局部行为：压缩、突破尝试、保持、回测、修复、反抽等；
- 参照关系：相对哪个冻结高点/支撑位，是否穿越、失守或收复；
- 活动描述：量扩张/收缩、相对成交额等，缺失单独保留；
- 市场可交易性、输入质量和推断状态不属于方向性阶段。

这些维度是拟议的逻辑中间表示，不在 Data Lineage 里各建一个工程节点。正式物化到 ADM 后才按资产和字段登记。

### 5.1 不能直接沿用的状态命名承诺

- 1：只能描述截至 T 的下跌减速与稳定迹象，不表示底部已经最终形成。
- 2：整理不等于吸筹，更不天然蕴含向上预期。方向性背景必须来自独立可观测证据。
- 4：明确“持续保持”与“经历回踩后保持”是否是两个子型；没有发生回踩时，不得伪造“回踩成功”。
- 8：拟改阅读语义为“上行加速 / 过度延伸”；真正的顶部是另一个未来目标，不把情绪耗尽作为无来源的事实。
- 9：拟采用“高位量价分歧 / 滞涨”；不能仅凭日线推断派发、主力持仓或净资金流出。
- 10：必须有已确定角色的关键支撑及失效确认；不把任何高点突破失败统称结构失效。
- 11：必须绑定此前已记录的支撑失效 episode；普通上涨日不等于反抽。

本节是对 v0.3 的修订建议，不在没有审批时偷偷改变原代码含义。原草案保留，未来由批准的 taxonomy version 一次性生效。

## 6. 多候选不是只有一种情况

| 情况 | 例子 | 正确处理 |
| --- | --- | --- |
| 不同尺度 | 60期背景上行，较短周期局部回踩 | 分槽展示，各有 scale/profile，不算同槽冲突 |
| 不同维度或父子含义 | 上升趋势内出现回踩；上升趋势内出现突破事件 | 分维度记录；主阶段压缩规则须显式审批，不能把背景与事件当作独立互斥真值 |
| 同一槽真实难以区分 | 6 浅回踩与 7 深修复都部分得到支持 | primary=null，AMBIGUOUS，候选及反证保留 |
| 必需输入缺失 | 价格有效，量数据缺失，规则要求缩量确认 | INPUT_UNAVAILABLE，不把“未知缩量”算成缩量成立；其他价格 Facts 可保留 |
| 定义逻辑冲突 | 同一参照被两条互斥规则同时认定“完整保持”和“已失效” | RULE_CONFLICT；阻止该槽的正式主阶段发布并归因规则，不靠多数票压掉 |
| 实现不足 | 所需几何/活动算法未实现 | NOT_IMPLEMENTED，不冒充一般 ABSTAIN |

歧义不会因为进入 Qualified 阶段自动消失。Qualified 也允许多候选/弃权，只是其条件、覆盖率和可用范围经过验证。

### 6.1 拟议输出契约

以下为设计字段，不是已部署 schema；需以加法方式对接现有 LabelResult，而不是修改既有枚举后让历史结果变义。

| 字段 | 含义 |
| --- | --- |
| observation_key / revision_id | 业务观察槽与精确证据修订分开；不含 consumed_at |
| security_id / listing_id / result_as_of | 经济身份、上市身份和被描述时点 |
| profile_id / scale / coordinate_ref / knowledge_view | 分类口径，不同口径不能互相冒充 |
| primary_stage | 0-12 或 null；只有 availability=READY 且 inference=CONFIRMED 可以有值 |
| inference_status | CONFIRMED / AMBIGUOUS / ABSTAIN / RULE_CONFLICT / NOT_EVALUATED |
| availability_status | READY / INPUT_UNAVAILABLE / INSUFFICIENT_HISTORY / FROZEN / NOT_IMPLEMENTED / NOT_APPLICABLE；具体枚举待 schema 冻结 |
| candidates[] | 候选代码、各条谓词的 TRUE/FALSE/UNKNOWN、正反证、缺失项、support score（如有） |
| conflict_kind | 无 / 维度未分离 / 同槽歧义 / 规则冲突 / 不允许转移等 |
| context_facets | 趋势背景、局部行为、参照关系、独立活动维度 |
| previous_observation_ref | 同口径前一判断/过程依据；不得只信任上一日标签数值 |
| last_confirmed_stage / last_confirmed_as_of | 仅作历史上下文，不能填进今天的 primary_stage 冒充新确认 |
| candidate_rule_set_digest / resolver_version | 原始候选生成和主阶段消歧分别可追溯 |
| facts_snapshot_digest / input_manifest_refs | 精确输入与派生证据身份 |
| reason_codes / support_refs / contradiction_refs | 人和审计程序都能追查 |

每个 attempted 槽仅取一个 availability_status；非 READY 时 inference=NOT_EVALUATED，READY 时取其余四个推断结果之一。规则级缺失清单可以有多项，但不能在观察账本重复计数。RULE_CONFLICT 必须 primary=null，不能同时记成普通歧义。

`support_score=0.7` 不等于 70% 准确率，不对多个不互斥命题做 softmax 后冒称概率。只有明确“预测什么事件”、有合格校准样本并绑定校准版本时，才允许 probability；评审者投票占比也不自动等于真实后验概率。[R2]

### 6.2 单值消歧顺序

先做身份/时间/坐标准入，再独立产生全部规则结果。先分离尺度与维度；剔除已被反证的候选；只有事前定义、通过反例测试的互斥区间或子型优先规则可决定主阶段。满足唯一且充分证据时才 CONFIRMED；否则保留 AMBIGUOUS/ABSTAIN。

不能只挑最高分，不能因为昨日状态是5就屏蔽今天对结构失效的证据，不能用“最短驻留时间”把真实转折抹平。原始候选、消歧后主阶段及可选平滑展示分开存；任何平滑的延迟必须单独评估。

## 7. 验证机制：先离线，再观察性日更

### G0 — 冻结可辨识问题与验收契约

先决定试点的 profile、目标对象、状态/修饰维度、允许输出、状态边界、比较基线、采样方案、参数版本、验证分区和资源上限。每个将被验证的命题都须说明成功、失败、不可判定分别是什么。

数值门槛不能事后补。业务方须事先选择错误容忍、最低有效覆盖、关键状态漏判代价、延迟容忍、每状态有效 episode 样本量/区间精度、候选集大小与歧义上限。当前尚无依据替其冻结通用“准确率80%”或“观察30天”。未填写项使 G0 保持 blocked，而不是让执行者自行取值。

可先做不计入确认性样本的有限标注可行性试点，用于估算难度与标注成本。试点已看过的数据不能随后冒充新测试集；最终阈值在确认性结果访问前锁定。

### G1 — 独立计算验证与反例

复用现有契约框架，新建独立小型 oracle，不调用待测分类器来生成期望标签。工程性硬门槛：已登记测试集中未来输入、身份/坐标串线、同槽重复 CONFIRMED、不可重放修订、生产/消费时间污染 Fact ID 均为零容忍。

覆盖：同值不同参照、停牌缺口、除权切换、历史不足、价格不变而滚动高点下移、相等边界、量缺失、只改时间戳、后缀未来数据注入、同日 OHLC 路径未知、重新启动和重复执行。

全量逐日重放与增量检查点重放必须在相同知识视图、参数和输入版本下产生相同结果。对在 T 后追加、当时不可见的来源或行情，既有 T 判断应不变；对经批准的历史修正，只能形成新的 retrospective revision，不能改写原来的 as-known 记录。

### G2 — 截至 T 的独立盲审

准备同一知识截止、同一价格坐标、同一上下文窗口的证据包。日线/参照必须由该快照生成，不使用今天的前复权图冒充历史当时图。第一轮评审者看不到算法输出和 T 后行情；他们先写可观察现象、主阶段或允许的候选集、反证及缺失信息。

拟议人工流程：至少两位有短线判读经验且未实现待测规则的评审者独立标注，分歧由第三方按明文定义复核。数仓审查证据包口径，量化方冻结抽样与统计。没有真人到位时，可以做角色审查/合成测试，但不能将其记成独立人工语义验收。

参考集保留三类：明确单值、合理歧义、定义不可判定。禁止把所有分歧经讨论强行变成“金标准”，也不能用一个 LLM 的输出给同一个 LLM 当真值。

抽样包含代表性随机样本和预登记的边界/罕见事件样本，两部分分开报告。盲审单值样本报告按状态 precision/recall、宏平均指标和混淆；全样本报告一致性、未解决分歧、候选集大小、参考覆盖率。代表性表现按事前权重计算，不用人为平衡抽样后的总体正确率代表全市场。

### G3 — 市场目标独立结算

当前阶段是描述；需要评价方向性价值时，另绑 TargetCard / ForecastSpec。例如“未来 N 个交易时段，价格先上穿 T 时冻结的上边界还是先失守下边界”，或者“未来窗口内回踩后收复同一参照”。目标须可从未来 DWD 独立裁定，resolver 不导入分类器或消歧代码。

复用现有 `expert_evaluation/target_cards.py`、`resolver.py`、`scoring.py` 和 `lld/04-experts-aggregator/expert-strategy-layer/first-target-resolver-expert.md` 的边界。仅当对象、锚点、horizon、坐标、缺失规则完全一致才复用既有 P4 card；否则提出新的卡片而不是借用名字。

输出 immutable observation 和独立 settlement sidecar；结算区分 settled / pending / censored / unavailable。不能把 pending 或缺行情算对，不能把未触及边界一律当失败。同根日 K 同时触及两个价位而没有获准的细粒度数据时，顺序未定，按目标卡记不可判定，不猜先后。

概率评价结合 proper score 与校准曲线；Brier/log loss 不单独证明校准。没有经校准概率时只报告分组结果分布、相对基线差异及区间，不制造概率指标。[R2]

### G4 — 时间外验证、覆盖与增量价值

按市场日切分整个股票面板，不把不同股票的同一交易日随机分到训练和测试。前向窗口交叠需按真实目标起止时间排除跨边界训练样本；同一个 episode 和共同市场冲击要用事件/时间块或聚类不确定性处理。单纯设置 gap 不能替代面板和目标重叠审计。[R3]

既有已查看的2020-2021实验区间不能叫新的盲测；2022-2024也已被F消费并暴露sample-population信息，不能再作为fresh confirmatory holdout。新的时间外评价只能使用尚未消费且另行授权的分区，并在访问前冻结切分、sampling caps、最低有效样本/episode门槛、目标和评分。参数、状态数量、采样、目标和评分变更都记入试验账本，多次试验风险不能靠最后保留一次漂亮回测掩盖。[R4]

至少比较：无阶段的基线、简单趋势/区间分类、同输入闭包的连续 Facts、十二状态候选/消歧结果。标签是同一 Facts 的确定性变换时，不会凭空增加原始信息；它可能改善有限能力消费者的决策组织或解释，也可能损失信息，因此要做实际比较，而不是假设更多状态更好。

正式策略价值另经已冻结映射和完整 P2，保持候选总体、资金、风控、成本等可比；不能只拿若干持有期收益替代不可成交、持仓冲突和资金路径。研究分层/展示价值可独立成立，不要求每个描述标签都是买卖信号；但不得因此升级交易使用权限。

### G5 — 观察性日更与资格升级

G0-G2 及因果/增量契约满足后，才能审批有限范围、只读、无交易消费者的每日观察任务。它记录当时发布的 Facts、候选、主阶段/弃权和后续结算，用于验证运维、漂移与前向表现。

“观察性日更”不是现有 P6 策略 Shadow 的隐式启动；独立审批其范围与权限，不触发订单、通知执行或收益承诺。扩到全市场前先证实所需数据覆盖和资源预算。

资格按状态、profile、知识视图、适用总体和用途分别批准；可以只有3/4/6通过而8/9未通过，也可以只准展示和研究分层，不准策略决策。Qualified 继续允许有规则的弃权与多候选。

## 8. 评分账本和不能被掩盖的分母

每日/每批至少满足：attempted = ready + input_unavailable + insufficient_history + frozen + not_implemented + not_applicable（具体互斥状态在 schema 冻结时统一）。ready 再分 confirmed / ambiguous / abstained / rule_conflict；最终账本必须一一对账。

主报告同时列：

- 语义参考集的一致性与覆盖；单值输出在可裁判样本的准确/混淆；
- 确认覆盖率（全 attempted 和 ready 两个分母）、候选集平均大小/分布、歧义率和弃权率；
- 每状态的样本数、独立 episode 数、持续时间、非法转移、来回翻转、首次确认延迟；
- 每只股票、时期、波动/流动性切片的缺失和选择偏差；切片信息本身必须可在 T 得到；
- 有明确未来目标时的结算成熟率、不可判定率、相对朴素基线和连续 Facts 的分布/评分差异及区间；
- 全量与增量一致性、资源消耗、任务失败与补跑影响范围。

选择性分类的风险—覆盖权衡必须一起看：仅在极少数简单样本输出可能提高准确率但没有足够用途，输出所有候选则让候选命中率失去意义。[R5] 规则冲突需要修复，合理歧义需要表达，两者不合并成一个“错误数”。

## 9. 每日派生架构与数仓边界

```text
CN01/Hermes ODS -> DWD 发布
                      |
               OCI 验证镜像 READY
                      |
             同一精确源快照 / 准入
                 /             \
           Facts V2           必需共享指标
                 \             /
               候选阶段 + 消歧
                      |
             受治理观察 ADM 发布
                 /          \
             只读 UI      独立成熟目标结算
```

Facts 和指标不是强制串联的两层：都可消费同一 DWD，或指标消费既有连续事实，按真实依赖建 DAG，只计算一次共享前提。12状态不进入 DWD 清洗结果，也不成为专家必须经过的入口；专家仍可直接消费连续 Facts。

发布身份绑定数据版本、有效知识视图、完整历史/检查点闭包、尺度和参照、定义和代码版本。每次实际生成、发布与消费时间分别归 provenance/operation log；业务观察时点属于事实/标签。今天19时才发布的结果不得记成15时已可供历史策略使用。

当前 V2 adapter 明确只准 retrospective，不能换一个日期参数就当每日 as-known 合格。先实现并验收新日更输入 profile，校验每个必需来源在截止前确已到达、未来生效事件的适用条件以及 READY 水位。调度器可以定时唤醒，但任务启动/发布依赖来源就绪，不以钟点代替输入完成证据。

修正重算需要沿影响图传播；有记忆的 episode 不能一概只回算最近60天，应从可信的修正前检查点重放至依赖状态重新收敛或当前截止。保留旧知识视图与新修订，绝不覆盖原始观察。

Facts V2 的长期共享 ADM 尚需独立稳定 asset policy；新观察产品名在审批前只是候选名称，不能因为 Catalog 需要节点而反过来制造资产。诊断候选、确认结果、人工参考和未来结算物理/权限隔离，不使未来结果进入日更标签输入。

## 10. 每张 Label Card 必须补齐的字段

除了现有 registry 要求，本次设计冻结须明确：

1. 描述命题与非承诺、主维度/修饰维度、scale/profile、参照角色；
2. 完整 `源字段 -> Facts/Feature版本 -> 谓词 -> 候选 -> 消歧` 依赖闭包；
3. 必需、可选、仅结算使用三类依赖，所有时间/坐标/单位/历史要求；
4. 入态、维持、退态和反证，边界相等、首次可确认时点及历史修正规则；
5. 与其他状态的互斥/包含/可共存关系，所有未说明转移的处理；
6. 缺数据、缺算法、证据冲突、歧义、弃权、非法状态的不同出口；
7. 可手算正例、易混淆反例、盲审规则、独立未来目标（如声称预测）与评价分母；
8. 精度、覆盖、延迟和样本预算的事前门槛，以及失败后的合并/降级/停用方案。

有未填必需字段的卡片只能留在 draft，不能注册为 active，也不能因影子输出成功而关闭该工单。

## 11. 本轮设计结论与未决项

建议批准的原则：验证分层；多候选/弃权是正式能力；先校验分类体系再补 B6/B7；精确依赖登记；复用独立 resolver；连续 Facts 保留为主数据与对照；日更与交易授权分离。

必须再决定：试点的短线 profile、保留哪些状态作为主状态/哪些变修饰维度、数值阈值与验收容忍、独立评审人员、样本/计算预算、每日 as-known 输入资格。未决项不妨碍设计准备，但阻止结果性实施和发布。

本轮没有声称“十二状态准确”“三方专家已独立验收”或“每日标签已经可运行”。

## 12. 方法参考（不是 Quanthus 运行权威）

- [R1] Lo, Mamaysky & Wang, *Foundations of Technical Analysis* (2000), NBER w7613：形式化图形识别与条件分布检验相分离；不是对本项目状态的有效性证明。https://www.nber.org/papers/w7613
- [R2] scikit-learn, Probability calibration：proper score、校准曲线及 Brier 分解的区别。https://scikit-learn.org/stable/modules/calibration.html
- [R3] scikit-learn, TimeSeriesSplit：时间先后切分；其 gap 参数本身并不完成本项目的 episode/panel 重叠治理。https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- [R4] Bailey et al., *The Probability of Backtest Overfitting* (2017)，作者机构存档。https://scholarworks.wmich.edu/math_pubs/42/
- [R5] Franc, Prusa & Voracek, *Optimal Strategies for Reject Option Classifiers*, JMLR 24(11), 2023：风险与覆盖约束。https://www.jmlr.org/beta/papers/v24/21-0048.html
