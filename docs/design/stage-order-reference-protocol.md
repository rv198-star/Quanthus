# Stage 0/2：分尺度业务定义与可追溯参考协议

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Work item | P3-STAGE-005 |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Owner decision | 2026-09-10：按第二轮四角色模拟评审结论推进 |
| Review | [业务复审第9节](../reviews/stage-0-2-business-boundary-reaudit-20260910.md) |
| Machine contract | `../../config/stage-learning/order-reference-protocol.v1.json` |
| Delivery plan | `../../config/stage-order-reference-delivery.v1.json` |
| Implementation | `../../src/quanthus/stage_learning/order_reference_protocol.py` |
| Scope | 有限的参考证据/意见/解析/封存机制及合成工程验收；不是新分类器或旧实验重跑 |
| Acceptance | [工程验收](../acceptance/stage-order-reference-engineering-20260910.md)；不是市场语义资格 |

## 1. 决定与不变项

本里程碑落实已批准路线，不再等待独立模型401恢复，也不再要求用户选择技术阈值。
旧 P3-STAGE-004、其阈值、参考规则和 INCONCLUSIVE_REFERENCE_SUFFICIENCY 保持不变。
新协议不修改旧代码、不复用旧候选名称冒充已获资格，不注册 LabelCard 或 Worker kind。

主命题为指定20会话尺度的结构描述；10会话宽度变化/中心移动是独立近期条件。
20是首个研究profile，不代表最佳周期或持仓期限。20-vs-prior20需要40会话，完整证据
继续继承现有Trend/Order builder的61会话历史要求，而不是仅提供20根数据。

| 观察 | 业务边界 |
| --- | --- |
| Stage 2 | 收敛型有序整理：低方向效率、有界区间、收缩、中心稳定的正面证据；具体判定仍需参考与候选验证 |
| Stage 0 | 同一声明尺度上的结构不稳定正面证据；不是其他标签失败、歧义或缺数据的剩余类 |
| 稳定但不收缩 | 保留连续描述；不为补正例扩大Stage 2，也不自动归0 |
| 20日整理+10日扩张 | 可以同时记录；10日信息不能隐含改换主命题，若构成主命题反证须明确理由和对应证据 |
| 两命题都不成立 | 合法；不同于输入不可用和认识不确定 |
| 两命题同时被确认 | 记录 RULE_CONFLICT，不多数投票消除，也不发布正式阶段 |
| 缺失/历史不足 | 单独记录availability；不能推出卖出或Stage 0 |

整理不表示吸筹、未来突破、向上预期或买点；不将支持分数作为校准概率。
本协议不引入新的市场阈值，亦不降低旧门槛。它负责把外部提供的业务判断与证据绑定，
不自动生成所谓专家真值。

## 2. 数据依赖与最小复用

```text
已提供的DailyInput历史（不打开DWD/provider）
  -> 一次准入 + 每个Fact family一次批量计算（job-local TMP）
  -> T-only案例包：20主尺度 / 10近期条件 / 对应价格路径与来源引用
  -> 外部逐例意见（短线、基金两个语义角色；真实来源单独记录）
  -> 无强制裁决的解析 + 分歧/缺失/数量账本
  -> 不可变证据包 + 独立读回重建校验
```

只复用现有正式Fact计算和canonical/digest，不复用带3/4突破anchor/episode假设的旧参考对象。
批量构建内所有案例共享一次准入及两族Fact计算；不同评审者不重建同一案例证据。
原始输入按内容身份存一次，案例仅引用自己截至T的最近61个会话。供评审的视图只展开
该案例的历史，不包含同批其他案例的未来记录或任何候选/阈值/评分/未来结算。

不将这些TMP/研究证据提升为永久ADM，不往DWD写标签。源快照记录及逐例意见须随包保留，
不能仅留无法解引用的哈希。source侧的dwd_known_from保留原义；输入view固定retrospective，
不推断历史实际到达时间。生成时间单列物理manifest，不进入案例或语义输出身份。

## 3. 输入与证据边界

构建API仅接受显式DailyInput、InputPolicy、案例键和精确代码版本。它不具备数据获取、
远端读取、抽样、选参或提交Job能力。继承开发时间域：历史2019—2021，案例2020—2021；
拒绝2022以后数据，即使调用者把它标为合成。批次不接收晚于最后案例的行情，早期案例
只呈现自己的T前后缀闭包。拒绝重复证券/日期、无目标行、非时区cutoff与来源身份不匹配。

source_kind仅说明调用方申报的synthetic_fixture或retrospective_development，不能单独证明
源在外部已经认证。案例保存上市/坐标身份、结果时点、profile和规则版本、Fact修订引用，
availability与UNKNOWN分开；历史断裂、坐标变更继承现有builder的重置/重新预热。

新API的可用性不授权真实数据读取。当前交付只进行合成夹具验收；后继真实参考可行性
批次必须先具备可读取的既有源材料和事前限定的样本/成本/停止契约。旧160例缺明细时
不得隐式重建后称为读回。2022—2024不重读，2025不动。

## 4. 参考意见与解析

每个案例恰好两份完整主评：short_term、fund。同一reviewer不可扮演两个不同主评身份；
同一origin可承担两角色但必须原样记录为相关来源。量化负责协议和统计解释，数仓负责
输入与证据符合性；它们不是额外两票市场标签，因此不为每个案例强加四次模型调用。

意见记录精确case_id/profile，实际来源kind、origin_id、session_id，两个主命题的
TRUE/FALSE/UNKNOWN，近期width/center描述，支持/反证引用、理由与未知项。严格字段
白名单拒绝偷偷夹带候选答案、未来收益或概率。正面断言至少有支持证据；输入不可用时
不得断言命题成立。记录schema验证不等于验证理由语义或证明没有外部信息污染。

来源明确区分deterministic_policy、self_simulated、external_ai、human。不同角色名、
origin/session数量或调用者填写human不能证明独立性；报告始终不自动授予
human_expert_validated或independent_validation。真实来源认证是外部证据，而非一个布尔值。

两意见相同可形成暂定参考一致；不同则保留AMBIGUOUS。任一意见同尺度同时TRUE为
RULE_CONFLICT；资料不足为INPUT_UNAVAILABLE。近期描述分歧单独保留，不改变已一致的
主尺度意见。原始意见始终封存；本版不提供强制仲裁、自动补样或候选选参入口。

数量按主尺度参考、近期分歧、输入状态、证券/日期、抽样类型、source_kind和origin分别
记录。代表性与边界/富集案例不能混成总体precision；本版不生成准确率或通过20例就
启动搜索的开关。合成/模拟参考只提供工程证据，不产生正式语义资格。

## 5. 发布、验证与有限工程验收

包保存protocol快照、共享source_rows、case packets、原始reviews、resolved和summary，
以manifest/READY绑定。拒绝覆盖已有路径、路径穿越、symlink、遗漏/多余文件、哈希损坏
及未完成包；所有读回有固定大小上限。独立verify从保留输入重建Fact/案例与解析结果，
比较语义内容而非仅检查摘要哈希。这个重建属于显式独立验收模式，不是日常消费者重复
生成前提。报告不宣称验证源外部真实性或人类语义判断。

工程样例至少覆盖：双尺度收缩；主尺度相同但近期扩张；稳定无收缩；输入缺失与历史
不足；不同日期案例不泄漏未来；同尺度冲突；同源多角色不伪装独立；残留分歧；源/配置/
意见被篡改；反复读回。测试夹具不是市场样本，不纳入真实参考数。

本里程碑A冻结业务与协议、B实现最小机制、C完成合成闭环及工程验收。工程验收成功
只关闭这个机制里程碑，不表示0/2已识别成功或整套阶段标签可上线。

## 6. 审查结论与追溯

实施前再次按四职责检查：量化要求不把参考多信息本身判为错误；短线要求主尺度和
近期反证均可表达；基金要求保留连续Facts且不追加选参；数仓要求共享前提一次构建、
完整闭包与可解析的明细。该有界方案满足上述设计约束；实证资格仍未产生。

本设计是用户“按评审结论推进”后的治理性落实，前序审计继续保留为历史建议，不回写
成当时已授权。工程验收由后继 acceptance 记录；关联测试、工单在机器交付计划登记。
