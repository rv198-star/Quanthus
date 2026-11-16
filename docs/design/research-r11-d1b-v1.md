> 最终状态（2026-11-16）：R11 整批共享合成工程 1.2.0 及受限停放决定已独立 ACCEPTED，FA-01 关闭；PR #691 普通合入 `c2b64d52361faa21d8175a3fd704c50d542d9153`。完整比较 parked、research_result=not_evaluated、daily_adopted=false；A01–A07 与原冻结合同保留。当前交付见 [R11 价值简报](../work-items/research-r11-value-brief.md)，以下保留各阶段设计及验收历史。

# R11 首包：单一机会与固定桶日常用途

研究负责人已接棒，Task #690。当前交付是 **C13+C15 的精确设计、共享政策执行包与采用缺项**。原R10已收敛；R11采用结论由完整比较与合并終审产生。

[研究合同](../../config/research/r11/research-contract.v1.0.1.json) · [采用覆盖](../../config/research/r11/adoption-coverage.json) · [数据消费者](../../config/research/r11/data-consumer-demand.json) · [DS完整工作包](/Users/william/Documents/Codex/2026-11-16/quanthus-r11-relay/design-v1/DS-WORK-PACKAGE.md)

现有P4/P2包含事件与T+1单次入场、原Trend/PositionRisk、持仓生命周期、有限池结算。P12 v8新共享储备、启用前补足、锁定周期预算与账户比例风险需要共同政策核心。DS将实现可供研究与P12 W3/W4直接复用的纯函数，不建设账户事实系统。

三项实质采用前置已确认：原专家接受包semantic_decision_authority=inconclusive；发展reader的per_decision_publication_proven=false；跨日等待新买点/重入尚无冻结语义。这些前置阻止日常采用，合成工程与元信息准备继续。实测历史读取/资金回放、新参考或训练需后段具体合同、完整估算和许可。

本包给出一个明确受限政策：原事件身份跨观察/入场/持仓/退出保留；原T+1窗口过期后只展示，后续可执行入场须新P4event并满足原22会话隔离/持仓清仓结算与5会话冷却。同事件重新择时不自动开放。该受限实现能验证接口与风险资金规则，完整C13等待采用项仍具名保留。

首DS包的交付单元为全部合成边界、源码/配置闭包、测试、成本和P12共享路径，回来由研究负责人内部验收和技术整合；最终只提交R11完整包。准备共用4主动小时是既有建议，首DS预计60–120主动分钟，操作保护150墙钟分钟/2400脚本CPU秒/2GiB/128MiB。新增付费参考/训练/市场读取/资金回放为0。

后段成本分开：P12 W1/W2工程52–84小时、W3/W4工程36–56小时为原v8估计依赖，P11不重复计作已投入研究。共享纯政策核心本棒完成；正式完整历史比较估算须DS核实接缝后给出，未具备报价时记录具体缺口，不编造总成本。已知开发曝光和未知历史成本继续保留。

预实现自审修订：CURRENT.json选择1.0.1，数量对保守成交后权益校验；旧1.0.0未执行草案和原检查点完整保留。

## 共享纯政策核心（DS S1）

实现位于 `src/quanthus/product_daily/`，不修改原 P4/P6、P2 policy、旧阈值或生产服务。模块边界如下：

| 模块 | 责任 |
| --- | --- |
| `contracts.py` | 严格 Decimal、时区、稳定 reason 顺序、CAS 与 idempotent command receipt |
| `candidate.py` | C13 原 event identity、T+1 有效期、观察/实际订单/实际持仓分离、22 会话与 5 会话边界 |
| `funding.py` | 固定桶 N/B、共享 reserve、欠资补足、迟到差额、同批排序和平衡分录 |
| `risk.py` | 保守成交后权益、费用与价格上界损耗、stock/industry/total 最大整手、候选到首桶的采用入口 |

所有公共命令先以 `CommandEnvelope` 校验 `command_id`、body digest 和 `expected_revision`。同键同 body 返回既有 receipt，同键不同 body 报 `idempotency_conflict`，revision 不符返回 `CAS_CONFLICT` 且状态对象保持不变。P12 W1/W2 负责把该语义放入数据库事务；本包不实现持久化、并发锁、HTTP 或账户事实系统。

C13 只保留原 T close 到 T+1 open 窗口。计划过期只更新候选解释，不终结实际订单、不释放 hold、不伪造成交；迟到 fill 可继续进入 P12 实际持仓链，但原推荐仍为 expired。新事件只有在原 22 会话分离、旧持仓已 flat/结算且 5 会话冷却满足时才能成为新机会。

C15 的 `cash_total` 始终由桶 cash、reserve 和非正 `unallocated_adjustment` 重新求和。结算只接受 flat、订单终结、fills 完整、费用已知且冲突已清的具名证据；同批按 bucket id 排序，后来的盈利不能重写已结算 cycle。启用前补足只使用当前 reserve，失败预约返回原 FundingState，不保留 transfer、hold 或 reservation。

数量求解对每个候选整手计算 `E_floor(q) = E - 其他未确认潜在买入费用上界 - 所有价格上界高于 mark 的损耗 - q 自身费用与 adverse loss`。已计入 E 的 payable 不再扣一次；pending sell 不释放 stock/industry/total 容量。任一依赖新风险的输入未知时返回 `INPUT_UNAVAILABLE`，已有持仓仍沿原专家/退出路径处理。

P2 接缝沿用原 `BuyCostPolicy` 与 `UnitState`。原 P2 平仓的 `UNIT_SETTLEMENT.profit_transfer` 可直接作为 C15 reserve 流入；不重写 P2 利润转移数学。原 `allocated`、候选可见性或 UI 状态均不产生 order/fill 授权。

## P12 W3/W4 接收接口

P12 将 `FundingState`、`CandidateState` 和命令 receipt 映射到其 revision/CAS 表。先冻结同一 as-of 的 `RiskSnapshot` 和候选 observation，再执行候选投影；新入场以 canonical `entry_invocation_payload` 创建命令并一次调用 `decide_entry`。该入口内部完成 prepare、专家最大仓位与配置 fraction 取小值、数量检查和 reservation。P12 在同一事务中写回新 state、receipt、reservation 和 `funding_ledger_entries`；失败分支保持原 state。相同命令重试返回原 quantity/reservation/result digest，新增分录为空，原始分录保存在 immutable result 中。

P12 必须提供的账户事实包括：同一 account/as-of economic equity、tradeable cash、完整 marked positions、remaining unfilled pending buys、pending sells、已入账 payable、价格上界、费用上界、stock/industry/total 比例、静态 level-one membership 和实际 settlement evidence。当前包不提供真实阈值或真实账户映射；`config/research/r11/implementation-policy.v1.json` 明确 `real_account_limits=null`。

## 后段完整研究报价

合成实现之后的完整历史用途比较尚未启动。按当前精确缺口估计，后续新增 P11 工作为 46–92 主动小时：数据/用途资格与共享 manifest 4–8；研究 adapter 和旧 control 接缝 8–16；一个旧 control 与一个 R11 arm 的完整资金路径及事件归因 20–40；cold rebuild/reconciliation 6–12；内部复核、证据和发布 8–16。该估计不含 P12 v8 原有 W1/W2 52–84 小时与 W3/W4 36–56 小时，也不含 P10 获取/修复。计算和产物量在冻结实际窗口、证券总体、费用与 replay manifest 后再报价。

本轮新增市场 row read、付费 reference、training、capital replay、daily serving 与 production 均为 0。

## Internal acceptance repair 1.1.0

研究负责人对首包独立复现后，DS 在既有合同内完成 R1–R10 整批修复。实现版本更新为 `1.1.0`，原 21 个冻结预期、P4/P6 阈值、PIT 与 T+1 窗口不变。

- 已完成 cycle 的 immutable settlement history 独立鉴权 late cash；同一桶进入新 RESERVED/HELD cycle 后，旧 cycle 费用或退款仍归旧 cycle，新 B 不变。正负现金差额共用确定性分配。
- 所有 reserve 流入共享同一 helper，先抵 negative adjustment，再进入 reserve。结算盈利不再绕过欠资债务。
- `CLOSED` 仅代表待结算状态；没有唯一 completed settlement 时禁止新 cycle 复用。
- 每个对外的 command 绑定 canonical operation 与实际业务 payload。同 command id 改金额、cycle、operation 或 observation 会报 `idempotency_conflict`；同键重试返回 immutable 原结果与当前 state revision。
- C13 临时 BLOCKED 可在原窗口内重评；原 event 的 eligibility/expiry window 不可改写，EXPIRED 终态不可 rearm。
- C15 composite entry 只消费原 expert runner 归一化后的 explicit entry action；BLOCKED 或 expert veto 不得分配资金。成功 `decide_entry` 在一个 pure state 中保存 reservation、quantity、result digest 和唯一 `decide_entry` receipt，prepare intermediate 不作为 entry 已提交证据。
- `E_floor <= 0` 直接 fail closed。Boolean、整数、Decimal 和 cash quantum 在 typed boundary 严格校验；float、truthy string、bool-as-int 与 sub-cent cash 不进入 state/ledger。

完整 expected/actual 验收由 `scripts/r11_synthetic_verify.py` 独立比较冻结 JSON 的每个 key；Q02 的 next-lot exposure、F07/F08 的 old-settled/new-cycle 身份及三条因果故事均从实际公共状态导出。

## 研究负责人补充修复 1.1.1

内部复核继续复现三项遗漏：原 expert 最大仓位没有参与定量、组合入场丢弃 reserve 划转分录、验证器在实际证据文件缺失时可能成功。共享实现已修复并增加五项边界回归：仓位两种大小关系、缺失 fraction、平衡分录与幂等保留、缺失证据失败。原 21 项冻结预期保持不变。旧 R7 回放测试的原专家上限为20%，正确数量为300股，旧500股断言已据此修正。

完整历史比较停放，日常采用保持 false；详见[本批采用处置](../work-items/research-r11-value-brief.md)。46–92小时为未核实估计，后续按具名用途和账户前置重新核价。

## FA-01修复：1.2.0周期身份与预约占用

最终审计在1.1.1复现了同桶第二次`decide_entry`被历史预约阻断。1.2.0将`EntryReservation.cycle_id`绑定到不可变结果；`entry_reservations`完整保留已接受命令历史，`active_entry_reservations`按尚未出现在completed settlement中的(bucket_id,cycle_id)派生。只对活动集合约束每桶一个预约；每周期和每命令的历史身份仍唯一。结算完成释放派生占用，历史回执和旧结果继续保留，原命令可在后续周期重放。

P12持久化本版完整history/receipt/settlement state，在同一事务中计算派生活动视图。`active_entry_reservation_ids`是展示/消费投影，不是可独立改写的事实。首轮结清、第二轮入场、旧轮迟到费及第二轮结算由一个完整回归穿过真实公开入口；活动冲突、失败回滚与历史删除保护同时验证。原21个golden和两个冻结研究合同保持原字节，本版合成状态按1.2.0schema新建，旧包保留历史。
