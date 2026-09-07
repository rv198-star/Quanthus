# 个股阶段标签：字段级依赖与状态证据矩阵

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | blocked |
| Authority | 设计依赖清单；不创建新的运行字段、指标、LabelCard 或 ADM 资产 |
| Parent | `stock-stage-qualification.md` |
| Vocabulary | `stock-stage-state-machine.md` v0.3；本文件标明其待修订处，不直接激活 |
| Observed baseline | Git `08a07c677f3dc13fe409caa05a6cf57269072109`；只读 schema / 实现核查，未读取新市场结果 |
| Machine source | `../../config/pipelines/dwd-domain-model.v1.json`; `../../config/pipelines/dwd-domain-model.v1.1-extension.json` |

## 1. 依赖契约的含义

本文件区分三种状态：**源字段在 schema 中存在**、**算法已有实现**、**指定 knowledge-view/样本/用途已验证**。三者不能合并成“已有”。所有下列现存 DWD 字段名已按当前 Catalog/机器契约核对；存在不等于每日可得、完整单位或历史 PIT 已证明。

一个待上线 LabelCard 必须能追溯：

```text
正式 DWD 字段及其版本
   -> 坐标/身份/时间准入
   -> Facts 或 Feature 的精确规格与版本
   -> TRUE / FALSE / UNKNOWN 谓词
   -> 全部候选 + 正反证
   -> 消歧后的主阶段或弃权
```

下面 D0-D9 是文档分组，F1-F9 是拟议共享能力分组，都不是新增 Catalog 节点/正式 feature id。所有参数、窗口、阈值和 profile 必须在实施前进入受治理配置；不得把分组名当成已实现算法。

## 2. 基础源字段清单

### D0 — 身份、上市与交易日坐标（所有阶段必需）

| 正式表 | 精确字段 | 用途及准入 |
| --- | --- | --- |
| `dwd_listing` | `listing_id`, `security_id`, `listing_code`, `venue_id`, `board_code`, `effective_from`, `effective_to`, `listing_currency` | 把代码映射到当时有效上市关系；板块用于规则适用，不用当前名单代替历史总体 |
| `dwd_security` | `security_id`, `company_id`, `instrument_type`, `instrument_currency`, `effective_from`, `effective_to` | 证券与公司身份、币种；标签主键不只用证券代码字符串 |
| `dwd_security_identifier` | `security_id`, `identifier_namespace`, `identifier_value`, `venue_id`, `effective_from`, `effective_to` | 仅当接入链需要代码归一时使用；不得跳过有效期 |
| `dwd_trading_calendar` | `calendar_session_id`, `venue_id`, `trade_date`, `is_open`, `session_type`, `open_at`, `close_at` | 交易日序号、窗口和确认时点；停牌日与市场休市分开 |

连接主干为 `dwd_security_bar.listing_id -> dwd_listing.listing_id -> dwd_listing.security_id -> dwd_security.security_id`，并以 `calendar_session_id` 连接日历、校验 venue。不要虚构 `dwd_security_bar.security_id` 这个源字段。

### D1 — 价格观察（所有价格阶段的核心）

正式表 `dwd_security_bar`：`listing_id`, `calendar_session_id`, `observation_start`, `observation_end`, `interval_size`, `price_basis`, `currency`, `open`, `high`, `low`, `close`。

必须筛选冻结的日线 interval 与 UNADJUSTED 坐标，验证价格有效、时段完整、重复行和来源身份。V2 当前 DailyInput 只接入 high/low/close，**open 虽在 DWD 已存在，但要使用跳空、实体/影线等能力仍须增加并验收输入映射**。

价格源目前采用 `raw-cny-action-epoch-reset-v1`。不同 epoch 不拼滚动窗口，旧高点也不能直接拿到新坐标比较。采用连续调整后的新规格需要单独验证，不能在原字段名下切换口径。

### D2 — 成交活动（只有卡片声明时才必需）

正式表 `dwd_security_bar`：`volume`, `turnover`，连同 D0/D1 的键、时段、币种及来源元数据。

- `volume` 为成交量原字段，跨来源/跨除权比较前验证单位一致；同一单位在比例中可抵消，不意味着任意供应商数字都可混用。
- `turnover` 在本仓库是成交金额，不是换手率，不是净流入；不能推断买方主动性、机构持仓或“主力吸筹/派发”。
- 缺失不填零；真实零活动与供应商缺失分别保留。以零均量为分母时量比不可用，不得输出无穷大。
- V2 的 missing_volume 是输入可用性记录，不是 B7 活动算法已完成的证明。

### D3 — 可交易性与市场机制（分类适用与交易效果分开）

正式表 `dwd_tradability_state`：`listing_id`, `calendar_session_id`, `is_tradable`, `can_buy`, `can_sell`, `upper_price`, `lower_price`, `reason_code`, `input_revision_digest`。

对于某个阶段能否解释，一字板、停牌、特殊时段须按标签 profile 决定是否暂停；**不能把 can_buy=false 自动解释成看空或状态10**。有些价格事实仍可观察但不能交易；阶段观察状态和交易执行状态分开。

Q4/P2 的未成交判断必须使用真实执行上下文。D0/D1 是可观察性依赖，D3 不是万能方向性特征。

### D4 — 公司行动与价格连续性（跨日/跨窗口准入必需）

| 正式表 | 精确字段 | 用途 |
| --- | --- | --- |
| `dwd_corporate_action` | `corporate_action_id`, `security_id`, `action_type`, `action_status`, `announced_at`, `effective_at`, `ex_date`, `record_date`, `payment_date` | 识别源事件及何时适用，不把未来生效事件当成已经执行 |
| `dwd_corporate_action_term` | `corporate_action_id`, `term_type`, `amount_per_share`, `ratio_numerator`, `ratio_denominator`, `subscription_price`, `currency`, `announced_at`, `effective_at` | 只在获准连续性/收益算法需要时解析条款；不由 Catalog 自行猜复权公式 |
| `dwd_adjustment_factor` | `security_id`, `adjustment_factor_id`, `factor_value`, `adjustment_method`, `adjustment_direction`, `anchor_date`, `effective_from`, `effective_to`, `evidence_set_digest` | 因子口径与版本边界；不能将不同锚定方式的 factor_value 混用 |
| `dwd_adjustment_factor_input` | `adjustment_factor_id`, `corporate_action_id`, `input_type`, `input_revision`, `input_evidence_digest`, `effective_from`, `effective_to` | 解释因子的实际证据闭包 |

首个试点优先沿用已获准的 action-epoch reset；它会牺牲某些样本连续性，但不通过未来复权或跨 epoch 插值制造形态。若消费者确需跨行动连续参照，再单独立项验证。

### D5 — 质量和版本证据（所有阶段准入必需）

DWD 表按其 time profile 保留 `knowledge_from`, `knowledge_to`, `source_record_id`, `source_revision`, `source_artifact_sha256`, `record_source` 等。不同表没有的字段不得虚构；外部源发布时间、平台接收/发布记录需要独立凭据。

质量辅助表：

- `dwd_observation_gap`：`listing_id`, `calendar_session_id`, `interval_size`, `is_expected`, `gap_reason`, `affected_effective_at`, `detected_at`, `evidence_digest`。
- `dwd_quality_exception`：`affected_table`, `affected_business_key_json`, `affected_effective_at`, `classification`, `severity`, `disposition`, `resolution_state`, `rule_id`, `rule_version`, `detected_at`, `evidence_digest`。

质量问题在未来才发现时，不能反向把历史当天从样本总体中删掉，再声称历史判断正确。retrospective 修正研究与 as-known 判断分别绑定知识视图。missing/invalid 数据可以冻结新判断，不能变成结构损坏证据或强制卖出指令。

### D6 — 指数与市场背景（首个价格结构分类的可选上下文）

- `dwd_market_index`：`index_id`, `index_code`, `methodology_version`, `effective_from`, `effective_to`。
- `dwd_index_bar`：`index_id`, `calendar_session_id`, `observation_start`, `observation_end`, `interval_size`, `currency`, `open`, `high`, `low`, `close`, `volume`, `turnover`。

指数与证券日期/价格口径要可比，用于相对强弱或评价切片，不应偷偷成为每个阶段的必需条件。宽度指标需要完整且当时有效的横截面，不能用“有数据的赢家子集”代替完整总体。

### D7 — 换手率的股本分母（仅选用该指标时必需）

`dwd_share_capital_observation`：`security_id`, `observation_start`, `observation_end`, `capital_type`, `share_quantity`, `unit_code`, `raw_value`, `raw_unit`, `parser_version` 及其知识/来源版本。

必须证明某个 capital_type 是卡片所需的流通/自由流通口径、单位为相容股数且当时已知；不允许使用当前流通盘回填历史换手率。源契约有 share_quantity 不等于自由流通分母已完整获准。首个试点不以此作为隐含必需依赖；可先用经验证的相对成交量，明确不称换手率。

### D8 — 行业、财务、公告及订单流（首版不作隐藏阶段输入）

行业分类若用于历史判断，需要 `dwd_company_industry_membership` 的 `company_id`, `industry_node_id`, `effective_from`, `effective_to`, `source_declared_as_of`, `membership_type` 及知识证据；当前静态浓度映射不能当历史行业时序。

财务/公告可由独立专家提供背景，但不为了区分2与9就把它们混进价格结构定义。订单流、席位、持仓等没有经准入的来源时，不推断“机构行为”。引入任何新域必须增加本依赖表、LabelCard 输入、时间契约与验证，不允许在提示词里隐式加料。

### D9 — 仅用于后验结算/策略评价的资料

未来 DWD 行情、未来可交易状态和公司行动、完整 P2 交易/资金账本，是 Q3/Q4 的 OUTCOME_ONLY 输入。分类器及日更 observation builder 不能读取它们。评审参考集也不能把未来结果带给 Q2 的当时点评审者。

## 3. Facts/Feature 共享能力矩阵

所有公式是待冻结规格的说明，不自动创建运行版本。以下 `H_n^-` / `L_n^-` 表示此前 n 个合格交易时段、不含 T 的高低点；`C_t` 为相同坐标的当前收盘价。

| 能力组 | 要表达什么 | 原始依赖 | 现有实现 / 本轮缺口 |
| --- | --- | --- | --- |
| F1 参照与距离 | 20/60 期高低参照、`C_t / anchor_level - 1` | D0/D1/D4/D5 | V2 Anchor 与 Measurement 已有受限验收；选择哪个参照是主支撑/趋势边界仍需标签卡 |
| F2 同参照交互 | 对同一 anchor 的上穿、下穿、收复及相等规则 | F1 与前一有效收盘；D4/D5 | V2 breakout/loss/reclaim 已有受限验收；通用低点支撑破坏与双向状态链不能冒称已有 |
| F3 持续与过程记忆 | started_at、hold_count、age、prior loss、固定参照、失效/中断 | F1/F2、D0日历和真实先前观察 | 最小高点突破过程已有；每日持久检查点、general retest/failure episode 未验收 |
| F4 区间几何 | 区间宽度、中心、位置、压缩/扩张、重叠 | D1或F1、D4；统一窗口 | B6 未开始；拟 `W_n=(H_n^- - L_n^-)/((H_n^-+L_n^-)/2)`，压缩可比较预定义短/长窗口 W；0宽度单独处理 |
| F5 趋势/速度 | 有方向的变化、趋势持续、加速、下跌减速、相对强弱（可选） | D1/D4；D6仅可选 | 旧 baseline 有总回报指数 momentum/SMA/vol；V2 raw epoch 与总回报指数不可混名复用。新方向/斜率规格须冻结 |
| F6 回撤与修复 | 从因果峰值的回撤、接近支撑程度、恢复幅度、低点抬升 | D1、F1/F3/F4/F5 | B6/过程扩展；若用局部 pivot，必须等右侧确认结束才可用，并记录确认时间，不回填到极值日 |
| F7 活动与阶段关系 | 当前/突破/回测相对量、价涨量扩/价跌量缩、金额的独立描述 | D2、F2/F3（条件窗口） | `liquidity.volume_ratio20@1.0.0` 已有旧 baseline 公式 `V_t / mean(V_{t-20:t-1})`；V2 B7未集成。改median或换窗口需新规格，不能沿旧ID |
| F8 日K可观察细节 | 实体、影线、收盘在日内区间位置、跳空 | D1的open/high/low/close及前一close，D4 | DWD有字段；V2 open未映射。`(C-L)/(H-L)`在H=L时不可定义；日线不能还原盘中先后 |
| F9 关键支撑失效/反抽链 | 主/次支撑角色、实际破坏、破坏后反抽、恢复或继续下行 | F1/F2/F3/F5/F6 | 消费者驱动的过程扩展；不把跌回阻力下方等同整段趋势失效；10/11/12的关键缺口 |

F4 的短长窗口共享输入，不是两个独立证人。F5 拟议的速度可以由固定窗口 log-return 或显式斜率表达，但选择哪种及阈值须在结果访问前冻结，不同时试几十种后只保存最优版本。

F6 的参照最大值必须从截至T的窗口产生，不能用事后已知完整波段最高点。回撤深度相对哪条波段、支撑采用哪个角色，是6/7互斥的必要前提，不是以后随便填的实现细节。

## 4. 0-12 状态 -> 必需证据 -> 反证矩阵

D0/D1/D4/D5 是以下所有行的共同准入闭包。D3 用于 profile 适用和独立可交易性；活动作为 MUST 还是 OPTIONAL 要在每行卡片内冻结。这里“需要 F7”表示要保留原草案量价含义就必须提供，不能没量时悄悄降成纯价格同名版本。

| 代码 / 草案名称 | 截至T应能证明的现象 | 最小候选能力依赖 | 主要混淆 / 必须给出的反证 | 当前是否可作为完整正式标签 |
| --- | --- | --- | --- | --- |
| 0 无序震荡 | 无稳定方向且无明确有序结构；不是分类器不会判断 | F4/F5，必要F8 | 与2的有序压缩区分；“其他规则都不满足”不等于无序震荡 | 否：中性判定及与2的边界未冻结 |
| 1 止跌企稳 | 已有下行背景，截至T速度减弱和稳定迹象；不声称未来不新低 | F5/F6/F7，F8可选 | 与12正常下跌中继区分；重新加速下跌是反证，不回写过去 | 否：减速/稳定/活动规格未冻结 |
| 2 蓄势整理 | 有序收敛、中心稳定，所需方向背景显式给出 | F4/F5/F7 | 与0、9区分；不能把横盘直接解释为吸筹 | 否：B6/B7及方向语义未冻结 |
| 3 试探突破 | 首次跨过特定参照；若叫放量突破必须量条件已可用 | F1/F2/F3/F7 | 与4“已保持”和5“上行背景”分维度；价格不动而高点滚落不得算突破 | 否：可复用穿越事实，但完整卡片未验收 |
| 4 突破接受 | 对同一参照已有规定时长/回测证据的保持与活动响应 | F1/F2/F3/F6/F7 | 与3按首次确认和保持条件区分；与5按趋势背景维度拆解 | 否：保持子型、时延和量确认未冻结 |
| 5 上升推进 | 当时可观测的上行几何、持续性及所声明的活动支持 | F1/F4/F5/F7 | 6可能是其局部行为，不应机械互斥；加速8可能是修饰维度 | 否：趋势/修饰到主阶段映射待验证 |
| 6 回踩测试 | 已存在上行背景，朝指定支撑回撤但该角色的结构未坏，活动符合卡片 | F1/F3/F5/F6/F7 | 与7需明确定义浅/深及主次支撑；支撑已失效不得还叫完整回踩 | 否：回测过程与6/7边界缺失 |
| 7 修复重建 | 更深损伤后的局部重建迹象，主结构条件和待确认项明确 | F1/F3/F4/F5/F6/F7/F9 | 与6、10区分；无法证明重建则只能候选，不因暂时反弹确认 | 否：多参照角色/修复证据未冻结 |
| 8 加速赶顶 | 当时上行加速、延伸及活动变化；“顶”不是已知事实 | F4/F5/F7，F8可选 | 与5的加速子型区分；未来回落不得作为T的输入 | 否：建议先作为“上行加速/延伸”修饰维度检验 |
| 9 高位分歧/派发 | 相对因果历史处于高位、活动增加而价格推进减弱 | F4/F5/F7，F8可选 | 与2整理/8加速区分；日线不证明资金意图，重新推进是后续反证 | 否：建议保留“高位量价分歧”可观察含义，派发仅待检验假设 |
| 10 结构失效 | 对事前指定角色的关键支撑出现充分失效证据 | F1/F2/F3/F6/F9；F7按卡片 | 与普通突破失败、7重建区分；缺数据/除权不是结构失效 | 否：现有高点loss不足以代表通用支撑破坏 |
| 11 反抽 | 已发生已记录失效后的反弹，尚未满足同一支撑收复规则 | F1/F2/F3/F5/F7/F9 | 与1、正常上涨日区分；必须给出 prior failure ref | 否：失效—反抽专属过程未实现 |
| 12 下行推进 | 截至T存在下行方向与持续扩展，活动含义显式给出 | F4/F5/F6/F7/F9 | 与1减速、11反抽区分；不要求必须事先看见10才能首次识别下行 | 否：初始化、下行几何及转移规则未冻结 |

“不完整”不是要求把 F1-F9 全建完才可推进。先选容易形成可证伪命题的突破/保持/回测片段验证3/4/6和其连续背景；若6/7不能稳定区分，应合并或分层，不为维持编号被动增加指标。

### 4.1 X 与推断状态不占市场阶段名额

- X/FROZEN：按 profile 暂停推断，不是第十四种市场趋势；真实异常原因保留在 availability。
- ABSTAIN：必需数据可用但不足以形成阶段判断。
- AMBIGUOUS：同一槽有多个尚不能排除的候选。
- INSUFFICIENT_HISTORY：历史不足，不填0，也不把上次状态冒充今天。
- NOT_IMPLEMENTED：能力未实现，不计为市场分类能力的普通失误或成功弃权。

先按可用性分组，再统计候选/确认/歧义，保持互斥账本。不能让一个样本同时在数据缺失和算法弃权分母里重复计数。

## 5. 共用参数与粒度登记要求

每一个拟议 Feature/Fact 家族必须明文填写：

| 类别 | 实施前必填 |
| --- | --- |
| 输入 | DWD完整字段ID、资产/视图版本、来源发布边界、join键、过滤条件与单位 |
| 时间 | 观察窗口、是否排除T、最小历史、交易时段还是有效bar计数、未来目标窗口另列 |
| 坐标 | 原始价格epoch或连续收益指数、币种、复权规则、量单位及行动边界 |
| 计算 | 精确公式、分母为0、相等边界、缺失处理、舍入/精度、实现版本 |
| 过程 | anchor角色/身份/修订、首次确认时间、前态证据、终止/中断/恢复、初始化 |
| 发布 | 事实键、修订键、准入总体、输入manifest、输出manifest、可用时间和生命周期 |
| 验证 | 每条依赖的正反例、独立复核路径、历史可知性等级、批量/增量一致性 |

指标定义留在 Registry；计算后的值若正式发布则成为 ADM 字段。与状态有关但未物化的实现中间量不为血缘图创建正式资产。

## 6. 度量覆盖与边界检查

设计到实施必须检查三类闭包：

1. **字段闭包**：LabelCard引用的每个谓词都能映射到 F 组、D 组、精确源字段/版本；不能写“行情数据”“相关指标”就算完成。
2. **时间闭包**：每个输入在 cutoff 可用且业务上适用；同一数值未来修订不能静默进入旧观察。
3. **验证闭包**：每个事实来源、消歧规则、冲突出口和状态边界都有独立正反例；没有证据的标签不允许 active。

当前字段目录可以检查名称/类型/归属，不能证明以上三个闭包全部成立。正式实施应把本提案逐项转成受版本管理的输入规格和机器检查，缺项使对应卡片 BLOCKED，不影响其他不依赖该项的连续 Facts。
