# R1 HF-DWD 修复合同 v2

| 项 | 值 |
|---|---|
| Design status | approved — Owner 2026-11-11要求Sol按#609→#610→#611修复，每步自审 |
| Delivery gate | accepted — 固定v2单节点Worker及描述性结果经[#612独立差集验收](../acceptance/research-r1-f1f3-independent-audit-612-20261111.md)；新任务按新请求与环境准入 |
| Parent | [探索推进准则](exploration-governance.md) |
| Work items | META-RESEARCH-SELECT-001 / #609；P3-RESEARCH-HF-001 / #610；P3-RESEARCH-PILOT-001 / #611 |
| Frozen request | `config/research/research-r1/request.v2.json`；精确闭包`closure.v2.json`；环境`runtime.v2.lock` |
| Code / tests | `ops/bin/hf-dwd-research`；`src/quanthus/hf_dwd_research.py`；`tests/unit/test_hf_dwd_research.py` |
| Prior audit | [#612原批次审计](../acceptance/research-r1-independent-audit-612-20261111.md) |

## 1. 问题与决定

主问题：固定的流动性/未复权价格筛选条件下，T+1开盘的价格守卫阻断比例、缺失原因和单节点资源成本如何？消费者是R2任务下达者。基准为同日期、相同证券身份和当前有效收盘/可交易状态下的全市场机会。

候补仍为固定资金路径的成本压力表，需要合格公司行动/重放接缝，停放。本批优先修复已确认的消费者和交付缺陷，并取得可复用的轻量执行资产。预计56成员187,921,175字节；单次计算最多180秒。原v1请求、HF A1、审计及样本曝光均保留。

总体阻断率>=1%，或任一年>=2%：下一笔优先调查执行守卫。都低于门槛：保留现有P2执行约束，下一笔选择一个具名信号问题。该门槛用于一笔可逆资源分配：至少每100个机会1个被拦时值得优先调查；不作为资金损失或交易资格门槛。未满足样本/集中度要求则记inconclusive并停放。

事前完整性要求：候选>=5,000；每年>=500；每年决策日>=200；候选listing>=100；最大单listing占比<=5%、前10<=20%。采用完整可准入集合，不按证券选出优胜者，不计算独立同分布的显著性结论。费用、收益、持仓和容量本批不评价。

## 2. 输入与时间

固定Base 2.5.1 HF revision及manifest沿用v1。仅2019交易日历用于上市年龄；行情/可交易状态只读2020-01-02至2021-12-31。决策2020-03-02至2021-12-30，下一交易日执行上下文到2021-12-31。2019元数据新增1个32,637字节文件，不读取2019行情。56成员逐个绑定源manifest中的size/SHA256；计算只读这个白名单。

用途为封存基座的latest-sealed回顾性描述。2022以后行情、仲裁和盲测保持关闭。DWD业务有效区间按上海日期[from,to)，当前知识版本保留`knowledge_to IS NULL`。security和listing都必须在T和T+1各自有效。连接后listing/date与listing/calendar-session必须唯一；冲突失败，不用DISTINCT消歧。

上市年龄与warmup分开：从listing的effective_from开始按开放日历累计；早于2019的listing计数可截断至2019-01-01，只用于>=120资格判断。每交易所到第一决策日必须已有>=120个日历会话，保证这一截断不改变门槛真假。结果称`listing_age_floor_sessions`，不报告被截断的计数为真实完整年龄。

## 3. 公式与缺失

- 当日未复权收益：`close_t / close_previous_session - 1`。
- `unadjusted_close_return_vol20`：含T的20个收益的样本标准差乘sqrt(252)，需要21个连续、有限且为正的收盘价。它与现有总回报波动率是两个明确区分的指标，未做公司行动调整。
- 流动性：T之前20个完整交易日成交额均值>=1亿元。
- T日收益绝对值<=4%；20期未复权动量`close_t/close_t-20-1`在[2%,12%]；上述波动率<=0.5；上市年龄>=120会话。
- 所有required输入须有限且有效，当前可交易状态为true。缺失/非法输入关闭该机会并记账，不推断数值。

T+1通过全交易所日历的明确下一开放日连接，禁止在过滤或重复的行情行上直接lead。需要T+1有效listing/security、正且有限的open、明确true的is_tradable/can_buy、正且有限upper_price，并要求`open < upper_price - 0.005`。该项命名为`next_open_price_guard_pass`。成交量、排队、滑点、卖出和资金路径属于后续真实回放。

按互斥优先级记录阻断原因：缺T+1身份、缺/非法开盘价、缺可交易状态、停牌、不可买、缺/非法涨停价、触及涨停价格；通过。计数总和须等于分母。控制组与候选使用同一T+1标签。

## 4. 执行与恢复

单写者、Linux/Python3.10.12、2线程；DuckDB3GiB、进程RSS6GiB、scratch2GiB、下载512MiB、上传4MiB；每阶段墙钟和CPU、整个Job墙钟均按请求限制并监测。Guardian进程负责超限终止；退出与峰值回执保留。CPU/RAM/空间现场配额满足后运行。

三阶段fetch/screen/publish。fetch用固定HF提交及源READY、manifest、成员白名单；可以从已下载缓存逐字节验证复制。文件只有size/SHA验证后才原子替换。screen进入前重验请求、代码、环境、完整闭包；计算子进程剥离凭据环境且不导入HF客户端。所有结果绑定Git完整SHA、代码文件摘要、请求摘要、环境和闭包摘要。

每次运行有`proposal/task/attempt`身份。阶段日志、fetch/run回执和固定包相互绑定。stage失败记日志，已完成的合法阶段可恢复；输入/配置改变必须新Job身份。计算最多一次已定位重试，上传最多3次暂态重试，均不重算已完成输出。

输出位于`runs/<proposal_id>/<task_id>/<attempt_id>/`；每个文件名含task和attempt。封装只做一次，包内字节及时间不因上传重试改变；READY绑定manifest真实摘要。上传使用原子commit返回的OID，并从该OID回读所有产物与READY逐字节验证。远端同身份不同内容拒绝，同内容幂等。上传失败保留本地包。

临时产物2026-12-11起复核，由CN02研究维护者负责；相关独立审阅/整改未结时延期。最小长期证据回CN02并回读，本批不删除旧证据。

## 5. 阶段自审

#609：输入只读元数据、公式/门槛/样本/分支及真实权限已冻结，preflight通过，记录准确Git/request/closure身份后才能实现#610。

#610：语义边界样例和身份/损坏/超额/并发/重试/上传负例通过；真实单节点canary、环境及配额回执齐全；记录本地完整检查后进入#611。canary是正式任务计算阶段，验收后直接复用已有输出发布，不重建同一派生数据。

#611：同一代码/请求/闭包的正式包上传及完整回读，分组/互斥原因对账、资源/失败/等待和下一动作交付。结论与蓝图追加，作者自审后交独立采用审阅。原#612审计保持历史封存。

原执行主动工时约1.7h/Owner约2h及审计成本持续累计；此次明确授权的修复段上限2h主动工时，计入总线8h，不重置旧成本。超出时交精确差集并停止对应阶段。

## F1–F3执行合同修订

Owner在v2独立审计后要求Sol修复并自审。研究公式、输入、样本阈值和已接受A2结果保持原版本；
本段明确原有阶段成功与恢复合同的实现。`request.v2.a3.json`绑定修后代码和新Job，
至多一次固定参数canary，已有A1/A2原包和资源记录保留。

- Guardian在启动子进程前记录当前attempt为`running`，使之前的成功回执失效；在子进程退出、
  资源检查及产物摘要核验完成后写`research-stage-completion.v1`终态回执。
- 回执绑定完整identity、phase、attempt、status、exit_code/failure、产物digest及资源峰值，
  自带规范化摘要，并与attempt日志的当前记录完全相同。fetch绑定fetch回执；screen绑定result/run。
- screen需要fetch最终成功。首次封包、复用完整包及发布重试均核验fetch/screen最终成功，
  当前attempt和产物摘要一致；失败、运行中、缺失、旧attempt或身份错配在接触HF API前拒绝。
  子进程写出结果只是产物准备完成，阶段成功由Guardian终态控制。
- 最小年度日期门槛明确统计候选实际出现的决策日，对照组日期另行报告。
- 在`package.staging/`写完成员/manifest，并原子写入READY，完整校验和上传额度通过后再原子
  改名为`package/`。已有完整staging可按原字节继续提升；无READY的中断目录移至
  `package-quarantine/`并记录恢复路径，使用现有成功输出重新封包。已有READY的损坏或冲突包停止，
  保留原字节。完成包在上传重试中不重新封装。

本次定点修复估算45分钟主动工作，完整检查/网络等待另计；旧执行、两轮审计和修复费用继续累计。
长期证据写入`cn02:/srv/agentdock/quanthus-ops/research-r1-f1f3-20261111/retained/`；
作者自审交#612差集复验。

发布前C1自审进一步复现：失败screen若已写出result/run，原恢复分支会直接返回，从而在下一次
启动中把旧失败产物记为成功。缓存恢复也必须核验原screen的Guardian终态；失败或新attempt的
running状态拒绝复用。成功计算直接进入publish；失败残留保留隔离后才能在已定位原因下重试。
A3保留为未发布的中间canary，初步完整检查主动中止并留存；
`research-r1-f1f3-correction.v1.json`记录一次已定位C1修正，`request.v2.a4.json`绑定最终代码。
A3+A4累计两次计算，公式、阈值和56成员闭包相同；不再追加计算尝试。

## A2代码绑定修订

A1 canary完成并保留在fb21637f；随后自审发现上传额度在重试路径遗漏、回执摘要可缺失两项守卫缺口。新增`request.v2.a2.json`只改变attempt/path及曝光记录，指标、阈值与输入56成员保持v2冻结值。A2复用已校验输入缓存并绑定修正代码，计算作为一次原因已定位重试计入累计成本；A1不发布为最终包。中止旧代码尚未完成的make check，最终代码重新完整检查。
