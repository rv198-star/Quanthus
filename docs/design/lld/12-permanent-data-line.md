# LLD 12: Permanent P10 Data Line

| Field | Value |
| --- | --- |
| Phase | P10; permanent and parallel to P1-P7 |
| Design status | approved |
| Delivery gate | open |
| Owner authority | Explicit Owner instruction and follow-up, 2026-10-03 |
| Governing decision | `../../architecture/adr-0015-permanent-p10-data-line.md` |
| Ownership policy | `../../../config/data-ownership.v1.json` |
| Parent | `../hld.md` |
| Physical data contracts | `01-data-foundation.md` and its children |

## 1. Purpose and ownership

P10 permanently owns acquisition, shared data quality and data governance. It runs beside the
business lines; it is not a tenth sequential release waiting for P7. P1 retains its accepted
historical foundation and sealed evidence; ongoing collection, maintenance and quality work is P10.

P10 owns provider qualification, collection, ODS/DWD builds and recovery, source semantics,
coverage, identity, corporate-action corrections, PIT source evidence, shared numerical
consistency, lineage, retention, immutable publication and data handoff. Temporary research data
repair and source recovery are also P10 work. Physical contracts and topology remain in LLD 01
and ADR 0007.

P10 does not choose strategy rules, model architecture, target-label meaning, independent research
reference criteria or promotion. Shared data-consistency #193 belongs to P10; feature domain
definitions and changed consumer acceptance remain with the domain owner. P6 retains its access
ledger and the business impact decision for a published correction; source repair belongs to P10.

### OCI native Base 2.3 transition (Owner 2026-10-12)

[P10-DATA-BASE23-OCI-001](../../work-items/base23-oci-consumer-migration.md)
owns the approved transition of new OCI API/event/replay inputs and daily-core parent
references to the exact native image through 2026-09-30. A distinct source contract and
new independently verified canonical input baseline preserve historical frozen contracts.
The exact original normalization attachment keeps its sealed parent; a separately pinned
continuation proves the state preimages. Source `NOT_GRANTED` and protected research/P6
gates remain unchanged. Production activation and retention remain pending the work item's
consumer, maintenance and first-scheduled-sync receipts. Cloud recoverability now uses the Owner-confirmed CN01 verification; this OCI line must not repeat the remaining archive checks.

## 2. Business disposition instead of local repair

Other lines declare inputs, perform minimal admission checks and implement domain features,
cohorts, models, strategies and consumption logic. On a quality/governance finding they may only
register a bounded P10 issue; they must not turn it into source hunting, collection, backfill,
private supplementation, inferred values/times or publication of repaired data.

After registering the issue, the business owner chooses and records one of three dispositions:

| Choice | Permitted business action | Required record |
| --- | --- | --- |
| Bypass | Use an already admitted alternative or disable an optional capability | Issue, selected route, business impact and any new versioned business policy |
| Temporarily ignore | Defer a nonessential affected capability and continue other business work | Issue, deferred scope and explicit unavailable status |
| Accept known limitation | Continue a use allowed by the existing consumer contract with the defect/uncertainty disclosed | Issue, purpose, limitation, impact and evidence grade |

No data issue automatically stops an entire business line. Only a step that cannot meet an existing
hard safety, PIT, protected-partition or activation gate stops. Acceptance of a limitation is not
repair or proof that missing evidence exists. Do not silently shrink a frozen window, infer a
cutoff, invent labels or describe diagnostic/inconclusive evidence as qualified acceptance.
Business owners retain research target/reference choices and remain focused on business value.

Earlier P3/P5 temporary-source approvals and completed outputs remain sealed historical evidence.
They do not authorize future repair/recovery outside P10. Existing immutable artifacts may be
referenced only within their original authority and an explicit business disposition; full input
qualification still needs a P10 versioned handoff and consumer acceptance. Do not reset attempts.

## 2A. 数仓数据质量核心铁律：业务价值优先（Owner 2026-10-08）

上位治理决议：[ADR 0016](../../architecture/adr-0016-value-proportional-warehouse-quality.md)；
落实任务：[P10-DATA-WH-001 / #495](https://github.com/rv198-star/Quanthus/issues/495)
及 [P10-DATA-WH-002 / #496](https://github.com/rv198-star/Quanthus/issues/496)。

> **数据质量以业务决策的可靠性为目标，而非以理论上的绝对精度为目标。
> 质量要求必须与数据用途、实际风险和修复成本相匹配；局部、非关键的数据缺陷
> 不得无条件阻断其他合格数据的交付。**

### 从统一完美门禁改为按产品/消费者分级

| 用途 | 合格判据重点 | 未满足时的处置 |
| --- | --- | --- |
| 日级核心数仓、正常日线决策 | 有权威来源的日级价格/成交量、证券/日期/价格基准、PIT、血缘及真正必需的前置 | 仅阻断受影响的日级产品；可在该产品独立合格时交付，不因档案级 1m 缺口自动失败 |
| 常规分钟/5m 研究 | 原生频率/交易所时间边界、实有覆盖、可解释的来源差异、明确质量/缺口状态 | 记录来源与缺行，按声明的消费者契约有条件使用或隔离；绝不把推导 OHLC 冒充实有 |
| 高频、精确分钟、逐笔或撮合回测 | 消费者实际要求的时间与价格/成交量精度、独立验证和完整性 | 必需精度证据不足则对应消费者 fail-closed；不以日线合格或同源 m5 守恒冒充精确 1m |

**跨级不放宽的硬约束：** 股票/交易日期/单位/复权基准正确，PIT/封存分区与独立参照、
原始 SHA/来源血缘可追溯，不造交易、不造分价/分钟、不残差摊量、不暗中拼源。
已接受历史与受保护窗口不可为了提高覆盖率而重写。明确源语义冲突只能做分产品
可比性治理，不能用多数票、隐形源优先级或放宽精度假装已证明相同。

**局部缺陷局部隔离：** 数仓设计应按实际消费依赖为日级核心、5m、1m 等产品分别声明
READY、覆盖率、来源口径、缺口和降级状态。缺陷只阻断其真正必需的产品/消费者，
不得以可选、归档或高精度支线失败而无条件停止日级主链路。当前四 Lane
共同 READY 的物理发布规则**尚未变更**；#496 必须先完成实际消费者与 DWD
依赖审计、版本化 schema/manifest、独立构建、Linux shadow、双节点及 OCI
验证并申请部署，不能在原路径上直接删校验或伪造整个交易日 READY。

**高成本质量追查的准入门槛：** 每次扩大供应商采集、全市场重验或追求更高精度之前，
先记录受影响消费方、实质性交易/状态/回测影响、最小可靠抽样、工程与网络时间账、
预算和停止条件。如果没有明确且值得投入的消费者收益，保留精确可审计的质量债、
采用已有允许的业务处置，把资源让给主线；无新来源或语义版本变化时不反复全扫。
“不存在用户投诉”不是数据准确的证据；“两个提供方不完全一致”也不是业务
不可用的证据。

**特定事故适用：** 2026-09-24 的 2,672 个双源分钟冲突及剩余 20 只无完整实有
240 根分钟来源继续保留原有证据、问题和严格 1m 资格；不因此自动否认合格日线。
P10 #492/#493 后续高成本探源转为具体消费者影响驱动的有界任务。版本化
质量分级/消费契约（#495）与日级发布解耦（#496）必须分别验收；治理接受
≠ 代码/部署/数据/研究消费者验收。

## 2B. 1m 时间预算与下一交易日补全（Owner 2026-10-09）

将 1m 视为 **可选、独立、按实际消费者价值投入**的数据产品。日常 T 目标 30 分钟、单阶段活动处理满 **60 分钟后禁止启动新操作**；下一权威交易日 T+1 另为前一交易日提供 **60 分钟的独立新操作准入窗口**，主要用于修订验证和定向补全。两段准入窗口合计 120 分钟，**不是 120 分钟强杀工作进程的墙钟限制**。截止前真实开始并登记的、具有独立超时的原子操作，允许在截止后完成剩余执行、校验和原子提交；截止后不得开下一分片、下一次新重试或排队未启动操作。操作必须可拆分，单原子操作试行独立最长 10 分钟，防止用无限长任务规避截止。已启动操作的收尾耗时、累计耗时及截止后延长均如实记录，重启不能清零。

日级核心不继承 1m 失败。T+1 检查真实目标日期分钟语义变更，不能用供应商滚动 HTTP 响应全体 SHA 变化冒充分钟修订，也不能重复整市场抓同一错误源。新口径由版本化政策 `config/pipelines/minute-daily-tplus1-budget.v2.json` 约束，并保留原 V1 作为历史记录。

R0/R1 按用途准入；R2 真实观测及缺陷标签允许入正式仓库，受限消费；R3 原始数据仅在独立隔离区，拒绝正式 ODS/DWD。运行与账本状态、幂等锁、交易日历、累计预算、T+1 截止、后置修订版本化的验收依 [ADR 0016](../../architecture/adr-0016-value-proportional-warehouse-quality.md) 和 [#505](https://github.com/rv198-star/Quanthus/issues/505)；R2/R3 物理准入依 [#503](https://github.com/rv198-star/Quanthus/issues/503)。**治理通过不等于服务定时器已经启用或旧 V1 已完成切换。**

## 3. Intake and versioned handoff

### Current complete Daily handoff (Owner 2026-10-13)

The versioned successor `p10-trading-day-complete-daily-v1` implements the Owner's
next-trading-day repair instruction in [ADR 0016](../../architecture/adr-0016-value-proportional-warehouse-quality.md).
Shared T daily market/event ODS is materialized once per Job, before native 1m/5m
acquisition. Both intervals have persistent bounded budgets and real R0/R1/R2/R3
status; R3 originals remain in quarantine. Independently admitted 1d continues.
Only a physically verified joint 1d/1m/5m product with both R3 counts zero enters
`CURRENT_COMPLETE_DAILY.json`; OCI copies exact immutable members before publishing
its visibility receipt. Consumers request intervals explicitly and respect actual
OCI visibility cutoff. Historical source and protected consumers stay gated.

Runtime config: `config/pipelines/p10-complete-daily.v1.json`; contracts:
`tests/contract/test_p10_complete_daily.py`; [operations](../../operations/p10-complete-daily-delivery.md).
Deployment evidence and the first real scheduled delivery must be recorded separately.

New acquisition or data-quality/governance issues use `phase:P10`, `area:data` and `P10-DATA-*`
ids. Existing open issues retain numbers, titles, evidence, execution gates and subgroup dependencies.
The reviewed routing inventory and consumer exceptions are in the ownership policy. Closed issues
and historical acceptance are not moved or reopened. Keyword matching or `area:data` alone cannot
decide ownership.

P10-DATA-000 is the standing coordination epic; it has no automatic closure or delivery due date.
Bounded repair children may close with their own acceptance. #424 remains the ODS/DWD reliability
subgroup with its original scope and operational gates.

A qualified handoff binds source members/hashes, dates/universe, schema/source/policy versions,
acquisition/publication/effective/knowledge clocks and evidence grade, coverage/exclusions,
unresolved reasons, corrections/exceptions, authorized consumer purpose and partition boundaries,
and required independent construction/verification. Replacing temporary sources needs comparison
and retained old lineage. A path, HTTP success or successful Job is insufficient.

P10 may deliver qualified supply, a bounded rejection or a formal unavailable disposition.
Consumers decide business treatment and their own acceptance; closing the source issue never
automatically accepts research or activates training/production.

## 4. Current consumer dependencies

| Consumer | P10 data issues | Retained business responsibility |
| --- | --- | --- |
| P3 #412 / #317 | #470 industry lineage, #473 canonical price basis, #474 historical availability | Choose/disclose business disposition, admit qualified B input if available, then apply existing strategy-comparison gates |
| P5 #402 / #400 | #471 original normalization scope/lineage, #472 state-only slot classification | Choose/disclose business disposition; own research samples, split, coordinates, ACTIVE and independent reference under the existing P5 contract |

P3 B and P5 B remain unaccepted. P3 D and P5 E/F are not activated by this ownership decision.
Their required-data steps remain blocked, while permitted business engineering may continue.
The recorded current choice for both consumers is `temporarily_ignore`: defer unqualified
P3 B acceptance/D results and P5 B acceptance/E/F training/evaluation, retain honest existing
diagnostics, and continue only previously authorized business/synthetic engineering.

## 5. Execution and invariants

GitHub remains normal code distribution and NexusAgent default remote control. The existing SSH
override belongs only to #424, not all P10. Routing authorizes no deployment, timer activation,
new collection, cleanup, credential change, Base mutation or P6 production change. Every child
retains its execution gate.

PIT cutoffs, windows, partitions and policies are unchanged. P1's 4,173 visibility exceptions stay
fail closed. Missing/invalid data blocks new entries and alerts; it is not structural damage and
must not force a sale. Batch DAGs, shared prerequisites, complete reuse identities and required
independent cold construction remain governed.

## 6. Work item and verification

| ID | Title | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P10-DATA-000 | Coordinate permanent data acquisition, quality and governance | Owner instruction 2026-10-03; P1 historical foundation retained | Ownership/routing contracts and verified live issue migration; bounded repairs/handoffs need separate evidence and standing epic remains open |
| P10-DATA-BASE23-OCI-001 | Migrate OCI new consumers to native Base 2.3 | Owner decisions 2026-10-11/12; native source and continuation proofs | [Versioned migration work item](../../work-items/base23-oci-consumer-migration.md), real canonical input receipt, actual consumer and first scheduled sync acceptance; retain old contracts and unique unbacked-up data |

Governed assets: `config/data-ownership.v1.json`, `scripts/sync_github_planning.py`,
`scripts/sync_p10_data_issues.py`, `tests/contract/test_p10_data_ownership.py`, root `AGENTS.md`
and the work-item index. Verify selective/idempotent migration, preserved closed history,
consumer separation and P10 planning. Green contracts or issue movement do not accept data.

### P10 daily-core implementation and rollout scope (2026-10-08)

Owner has explicitly authorized the new-standard restart with 2026-09-24 as baseline.
See `docs/work-items/p10-daily-core-20260924-recovery.md` (from repository root) and
`config/pipelines/warehouse-quality-policy.v1.json`. The opt-in `daily-core-v1` product
requires market and calendar/event ODS only, publishes seven DWD tables with 1d bars,
and uses `CURRENT_DAILY_CORE.json` plus immutable dated descriptors. It does not
change the old four-lane CURRENT, assert 1m/5m acceptance, or activate P6/training.
Dedicated CN01/Hermes daily-core services and OCI core-only mirroring are allowed
after their baseline, physical-source, contract and runtime verification.

### Delivery notification trial (Owner 2026-10-15)

P10-DATA-NOTIFY-001 governs the notification-only successor in
`config/pipelines/p10-notification-trial.v1.json`. Ordinary T-day attempts wait for
the existing 23:45 daily-ODS deadline; explicit access refusal, storage/identity or
controller evidence faults alert immediately. T+1 retains the authoritative trading-day
13:00/13:20/14:00 checkpoints. A downstream wait may reuse a transport-confirmed,
unresolved upstream incident from the same business date; independent new faults
and absent/stale/unverified incident evidence still alert.

During the trial, verified T-day daily ODS and jointly qualified native 1m/5m each
queue one success per business date/stage. Minimum R2 and R3=0 are unchanged.
Success binds physical daily source/catalog evidence or the already verified minute
receipts and exact seals. It separately labels complete DWD, OCI and GDrive as
unconfirmed until their own evidence exists. No success is backfilled before 2026-10-15.
The existing bounded mail retry service sends immutable queued requests; missed
enqueue recovery inspects only the two current authoritative operating targets.

Set `success_notifications_enabled=false` to end success messages without reverting
failure throttling. This policy does not modify the frozen complete-data policy bytes,
data gates, production schedule, Base identity or historical receipts. Implementation:
`p10_notification_trial`, `p10_notification_incident` and
`p10_complete_daily_notifications`; contract: `test_p10_notification_trial.py`.

### 2026-10-15 Daily source retry and failover

Owner requested an automatic backup and retry-switch mechanism. The independent opt-in
`config/pipelines/p10-daily-source-failover.v1.json` admits native Tencent **whole-equity daily
bars** for gap-aware non-execution warehouse use. It does not rewrite the existing product,
minute, ST, ownership or Base policies. BaoStock remains first; primary readiness is bounded to
120 seconds and the primary scan gets at most 900 seconds and half the remaining original Job
budget. Fallback shares the same original completion deadline, makes at most two sequential
HTTP attempts, and only requests absent observations. Exact preserved responses are requalified
and reused with their original acquisition clocks. No cross-source OHLC assembly is permitted.

Explicit provider access denial opens a provider-wide one-hour local cooldown immediately;
ordinary transport failures use a per-scope circuit. After cooldown only a scheduled probe may
reassess the provider; that duration is not a prediction of the vendor's ban release. Engineering,
identity, schema and shared-budget failures are not mislabeled as a supplier outage.

A calendar fallback requires same-observation-day saved BaoStock authority, exact row digest,
complete target-window coverage, original clocks and a preserved source envelope. It is labeled
reuse, never refreshed. The universe remains exact target-date, physically verified with original
listing/status evidence. Missing new-date authority still blocks; previous-date snapshots and
weekday arithmetic are never substitutes. Tencent index units and quote valuation semantics are
not admitted by this change: exact target-date BaoStock index observations and the unchanged
nonempty valuation gate remain required. These are explicit remaining source dependencies.

Tencent's native reported precision is preserved using Decimal and declared source units;
amount/volume quantization is disclosed rather than silently rounded into equality with BaoStock.
Missing ST is unknown, never NORMAL: daily warehouse can_buy is false for affected observations,
and the original S2/ST qualification still blocks. Missing valuation fields retain four measure
rows with null values and explicit source absence. Existing genuine BaoStock valuations remain
independent observations. Restricted daily completion mail names those source/use limitations;
it never accepts S2, a minute product or complete two-end delivery. Joint delivery still requires
native 1m/5m minimum R2, R3=0 and its existing exact transfer receipts.
