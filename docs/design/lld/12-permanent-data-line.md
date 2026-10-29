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

`P10-DATA-OPS-016` / [#537](https://github.com/rv198-star/Quanthus/issues/537)
owns the versioned CN02 acquisition/container stack. The Owner's 2026-10-23
[source integration](../../work-items/p10-container-main-integration.md) brings
Daily/recovery acquisition through ODS/DWD verification and sealing, scoped
Weekly, operational API mail, Base 2.4 increment backup and TMP housekeeping into
main. It does not switch a running release or establish natural-run stability.
The [runbook](../../operations/p10-container-jobs.md) owns deployment and rollback.

One immutable image supplies separate roles with explicit mounts. Data roles run
as the configured non-root UID/GID with a read-only root, no capabilities and no
Docker socket; payloads, state, TMP and private credentials remain on host binds.
The host only schedules/controls Docker. Existing approved sudo may elevate the
fixed local Docker client, never the Python job, scratch owner or container user.
Pin pipeline/collector revisions, image and Compose digests before activation.
Do not inherit ambient Docker contexts, Compose files, remotes or credential flags.

Daily defaults to 1d and native 5m; 1m is not a prerequisite. One immutable core
feeds every in-job native/composite consumer. Date locks, acquisition evidence,
READY ordering, actual knowledge clocks and consumer gates remain unchanged.
Normal Daily and late recovery retain separate versioned intake/deadline rules.
Recovery copies verified capture bytes, skips completed securities, preserves
failed attempts and never resets a consumed budget. Login-only repair is bounded
by the original recovery deadline. Weekly's implemented entry qualifies financial
statements only; unsupported nonempty domains fail closed and remain held.

API mail runs inside the data task with private runtime environment injection,
bounded retries and durable event/idempotency receipts. Success, partial, closed,
busy and failure are distinct outcomes. Mail or backup failure cannot recollect
data or invalidate a sealed product; an unavailable daemon leaves an unsent event.
Retry uses the same image's transport role, independently of collection. Backup
freezes exact upstream package identities and qualifications, separates ODS/DWD
Drive roots, writes immutable copies and checks the exact remote set/size/hash.
Only Base 2.4 Daily/Weekly 1d/5m selections qualify for this route. Retired 2.3
archives are not resynchronized. Backup does not grant consumer admission or
authorize local formal-product deletion.

Each job leases a unique owned child of physical, disjoint `QUANTHUS_TMP_ROOT`.
Only complete/authoritatively closed scratch is eligible 72 hours after completion,
with no active lease. Failed, held, interrupted, unknown, linked or unsafe trees
remain. The same-image cleaner has an internal 03:30 Asia/Shanghai clock with
restart catch-up and a persistent completed slot; its only bind is TMP and its
network is disabled. It has no formal products, secrets or Docker socket. Manual
cleanup defaults to preview. Original captures, evidence and formal ODS/DWD are
never scratch. Exact interfaces/configuration and tests are indexed in the
integration work item; runtime activation and full Weekly acceptance stay separate.

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
| P10-DATA-OPS-016 | Integrate acquisition through ODS/DWD, notification, backup and housekeeping in one pipeline image | P10-DATA-000; Owner source-integration instruction 2026-10-23 | [#537](https://github.com/rv198-star/Quanthus/issues/537); [integration](../../work-items/p10-container-main-integration.md); source checks do not accept full Weekly or activate a release |
| P10-DATA-SUPPLY-000 | Update the a-stock-data reference and qualify supplementary skill sources | P10-DATA-000; Owner instruction 2026-10-29 | [#581](https://github.com/rv198-star/Quanthus/issues/581); [bounded work package](../../work-items/p10-source-supply-20261029.md); candidate disposition does not admit a consumer or upgrade production |
| P10-DATA-SUPPLY-001 | Establish CN02 collector lineage and update the upstream reference baseline | P10-DATA-SUPPLY-000 | [#582](https://github.com/rv198-star/Quanthus/issues/582); exact runtime/reference identities and applicable-change mapping |
| P10-DATA-SUPPLY-002 | Freeze the source census, functional contracts and candidate/rejection ledger | P10-DATA-SUPPLY-001 | [#583](https://github.com/rv198-star/Quanthus/issues/583); bounded requests, evidence hashes and explicit unresolved contracts |
| P10-DATA-SUPPLY-003 | Qualify structured financial, disclosure and regulatory APIs online | P10-DATA-SUPPLY-002 | [#584](https://github.com/rv198-star/Quanthus/issues/584); source-identity/schema/time evidence per purpose; no historical PIT inference |
| P10-DATA-SUPPLY-004 | Qualify media, RSS and institution discovery sources online | P10-DATA-SUPPLY-002 | [#585](https://github.com/rv198-star/Quanthus/issues/585); retain functional/discovery distinction and finite unresolved dispositions |

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


## Bounded dated-status evidence — P10-DATA-P6-BASE24-EXEC-001

Issue #538's additive `securities_warehouse/base24_status_evidence.py` publishes
`quanthus.p10-dated-status-evidence.v1` from exact retained ODS members. It embeds
the original inventory, complete selected response bytes, exact security/date
requests and actual source capture times. A source response's `fetched_at` or
its preserved `acquired_at` is required; file time, build time and a synthetic
next-day cutoff cannot substitute. Unknown, duplicate and inconsistent source
identities fail closed. Equal-time status disagreement remains unavailable.
The existing latest-visible-observation rule applies only to the same exact
security/date; it never carries state into another date.

One immutable observation set serves qualification and safe reporting. Independent
offline verification recomputes the observations and dispositions from embedded
originals, validates the exact physical inventory and fails on semantic tampering
as well as damaged bytes. READY is last. Source files and old products are not
modified. This product qualifies dated provider evidence only: canonical status
conflicts, IPO rules, price bounds, P6 admission and historical PIT remain separate
checks. It grants no execution authority. Tests live in
`tests/unit/test_base24_status_evidence.py`; the bounded issue owns exact source
selection, repair receipts and consumer handoff, without P6 ledger changes.

### Successor evidence for current rule scope and instrument corrections

`base24_execution_evidence.py` implements the separately identified
`p10-current-rule-window-exclusion-and-instrument-correction.v1` contract.
The stage graph is exact retained originals → one immutable evidence packet →
bounded successor qualification and safe disposition; cold reconstruction is an
explicit independent mode. This does not reconstruct an old IPO rule or waive
missing evidence. The pinned complete SSE trading rules effective 2026-07-06 and
their actual retrieval receipt can prove current-window exclusion only for an
SSE main-board listing with six distinct, sourced, eligible observations in that
same listing, matched to unique open calendar sessions. Listing age or an exchange
calendar count alone is insufficient. Other venues, an initial window, ambiguous
identity and special lifecycle cases remain held. The old qualification call
without explicit evidence preserves its original IPO gate.

The same retained SSE rule supports a new version of the stale SSE ST overlay:
the preceding effective interval remains 5%, the successor interval starts on
2026-07-06, and the actual new repair knowledge time is recorded separately. No
SZSE fact is inferred from SSE evidence and no frozen price policy is changed.

An exclusive company-bond risk-warning title cannot establish an equity ST
interval. Withdrawal requires the exact native → raw interval → announcement
identity chain and produces a new UNKNOWN equity assertion, never inferred NORMAL.
The prior record and its times remain in the evidence. An actual exact-date
provider observation and all other gates are still necessary. Partial revocations,
mixed instruments and uncertain event titles are not silently adjudicated.

Each successor retains original bytes, actual clocks, source and code identities,
and the prior P6 ledger. A new governance correction is not backdated to a frozen
P6 snapshot. Bounded requalification may evaluate the successor but does not grant
consumer admission, historical PIT acceptance or trading authority. Tests are in
`tests/unit/test_base24_execution_evidence.py`.


The approved bounded official supplement extends current-window evidence to SZSE
main-board and ChiNext with the separately pinned complete SZSE 2026 rules and
actual receipt; SSE authority is never reused across venues. The old qualification
interface still requires its original IPO source. The source-side ST overlay is
versioned by venue, retaining the preceding fragment's original source identity.

`base24_official_notice.py` can withdraw an erroneous retained ST assertion only
through the exact native interval → raw event → official index → complete PDF
chain. An exact reviewed name transition, operative approval and effective-date
bound are frozen and recomputed offline. A document containing conflicting dates
retains both and is usable only after both bounds; it does not acquire an invented
precise historical timestamp. Withdrawal yields UNKNOWN, not a continuous NORMAL
interval. The target still requires an actual exact-date provider observation and
all other frozen qualification checks. New acquisition/repair knowledge is never
assigned to the old replay snapshot. The current issue's receipt controls the
bounded fetch budget; these adapters do not authorize further acquisition.

The one-day supplement adapter (`base24_single_observation.py`) requires the exact
approved unadjusted request, complete response/receipt, unique native listing and
same-venue calendar. It preserves Decimal values and actual retrieval time, keeps
provider acquisition unknown, leaves native limit prices null, and reproduces the
two new native rows offline from embedded originals. It cannot alter an old row.

An exact stale 5% native-bound defect may receive an additive successor through
`base24_execution_bounds.py`: current exchange authority, actual reference close,
all existing identity/status/window checks and the frozen policy must agree. Both
old bounds must reproduce the diagnosed former 5% formula. Only the two bounds
and new state knowledge time change; OHLCV, flags and original records remain.
A missing reference, another mismatch or another failed gate stays blocked.
