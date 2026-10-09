# ADR 0016: Value-proportional warehouse data quality and product-scoped availability

- **Status:** Accepted as an architectural principle; implementation and production cutover remain separately gated
- **Date:** 2026-10-08
- **Authority:** explicit Owner instruction following the 2026-09-24 one-minute data recovery review
- **Scope:** permanent P10 data acquisition, warehouse ODS/DWD, data products and their consumer handoffs
- **Predecessor:** ADR 0015 (P10 ownership); P1/PIT historical contracts remain authoritative for their accepted products
- **Follow-ups:** P10-DATA-WH-001 / #495 (quality tiers and consumer contract); P10-DATA-WH-002 / #496 (daily-core publication independence)

## Core rule — 数仓铁律

> **数据质量以业务决策的可靠性为目标，而非以理论上的绝对精度为目标。质量要求必须与数据用途、实际风险和修复成本相匹配；局部、非关键的数据缺陷不得无条件阻断其他合格数据的交付。**
>
> **Do not optimize data precision in isolation from material consumer value. Preserve source truth, PIT, lineage and safety, and isolate optional defects to the smallest affected product/consumer.**

This is a default **decision rule for future P10 design and incident handling**, not a waiver of the existing 1m, ODS/DWD, protected research or trading admission contracts.

## Context and failure mode

The September 24 1m recovery repeatedly tried to prove all-vendor, all-minute numerical identity:

- 2,672 securities had two individually qualified, whole-security 1m candidates but unequal source-native minute OHLCV semantics. Exchange time-boundary conventions differ; numerical inequality alone is not proof that every stock's useful observations are unusable.
- 20 of 4,992 securities still lacked a qualified full 240-observed-minute source. Cross-frequency checks later showed 53 missing actual 1m positions and exact 48-bucket volume conservation against real Tencent-native m5, but did **not** supply missing 1m OHLC.
- The operational daily orchestrator requires four ODS lane READY states for a whole-date DWD attempt, while LLD 01 explicitly identifies 1m as **archive-only research data outside the accepted P1 scope**. A strict optional-minute gate thus became an unconditional daily business-delivery blocker.
- The extended incident cost dominated progress on consumer value. Absence of public complaints does **not** prove a source is correct; nor does a lack of perfect source equivalence prove it is unusable for every consumer.

The mistake was not preserving exact raw evidence; it was applying the **highest possible precision contract to every downstream product and making unrelated delivery dependent on it**.

## Decision

### 1. Every quality rule must name the affected product and consumer

P10 must define each product's actual prerequisites, purpose, acceptable observation semantics, quality grade, known missing/conflicting coverage and authorized consumers. Quality is not a universal "green" bit. A problem blocking a strict 1m replay is not automatically a problem blocking a correctly built daily-bar product.

Do not quietly downgrade or relabel a precision-sensitive consumer. Existing strategy, training, protected-partition, independent-reference and activation gates remain in force until separately reviewed by their owners.

### 2. Minimum cross-product hard invariants do not relax

For every quality grade and all warehouse publications, preserve:

- correct security/listing identity, trading date/session, units, price basis and source representation;
- point-in-time knowledge/effective time, frozen windows and protected partition boundaries;
- provenance, original raw SHA/manifest, lineage, reproducible transformation and immutable release/correction semantics;
- source-observed versus inferred/derived/unavailable explicitly distinguished; **no fabricated trades, OHLC, volumes, timestamps, residual share allocation or cross-source minute stitching disguised as observed truth**;
- consumer-specific stop gates for materially invalid, insufficient or ambiguous inputs, plus existing safety and trading activation controls.

Never use a degraded label to conceal a broken essential invariant. Source conflicts may remain unresolved; they cannot be silently declared identical or used in strict equal-semantics consumers.

### 3. Classify quality by consumer purpose, not by one global precision ceiling

| Product/consumer class | Admission goal | Allowed disposition |
| --- | --- | --- |
| **Daily core / foundational warehouse** | Reliable source-native daily data, identity, daily semantics, PIT, lineage and actually required upstream datasets | Publish independently when **its own** versioned contract is satisfied, even if unrelated archive-only 1m is unavailable |
| **Routine intraday / research 5m or 1m** | Native interval/source semantics, date/volume/price checks and observable coverage | Publish an **explicitly limited or unavailable** separate product where its versioned consumer contract allows; source conflicts/gaps remain visible |
| **Precision-sensitive minute, tick, execution or risk research** | Source-authentic observations and independent accuracy/timing proof at the demanded resolution | Fail closed when required completeness, exchange-boundary evidence, PIT or price/volume precision is missing |

An observation may have enough quality for one declared purpose and insufficient quality for another. Different source-native products are not interchangeable without an explicit reconciliation contract.

### 4. Local failure is local; independent delivery is a versioned architecture decision

A missing/conflicted optional lane must generate precise issue, status, manifest and retry/disposition evidence **without automatically failing otherwise qualified independent daily products**.

A new product-scoped READY or daily DWD release is **not** authorized by this ADR alone. #496 must first inventory actual consumers/dependencies and version the schema, publication, rollback and CN01/Hermes/OCI mirror acceptance. Existing four-lane READY and frozen artifacts remain unchanged until approved controlled implementation.

No silent drop of formerly required inputs, retroactive acceptance of old receipts, false whole-date READY or use of a daily product to satisfy an intraday consumer.

### 5. Time and precision expenditure require evidence of decision impact

Before a costly source chase, correction or global requalification, record:

1. the exact affected date/securities/fields and affected **business consumer**;
2. a credible severity estimate: does it change the consumer's states, entries, risk, attribution or backtest conclusion?
3. a bounded sample/reproduction and an explicit time/request/cost budget;
4. the simplest qualified alternative, including independent product delivery, bypass, temporary deferral or acceptance of disclosed limitations where contractually allowed;
5. the **stop/escalation condition**: if evidence does not establish material value from more precision work, leave an honest open debt/unavailable status and return effort to higher-value work.

An inability to prove material impact is not proof of safety for a precision-sensitive consumer. It is a reason **not to devote unlimited resources to a global perfection requirement without a justified beneficiary**. Repeated identical requests against a stable failed source are not progress.

### 6. Measure actual business service, not abstract perfect-data coverage alone

Track core data product availability and latency, per-product admitted coverage and explicit degraded/unavailable gaps, impact on protected decisions, number of consumers blocked, recovery/test effort, provider/network costs and the number of repeated attempts. Keep denominator and exceptions visible. Do not present raw HTTP success, source-level correctness, and consumer acceptance as the same state.

## 2026-10-09 Owner addendum — 1m T/T+1 采集预算与停止条件

**正式批准：** source-native 1m 是辅助、独立、按消费者价值投入的数据产品；1m 精度问题不得使合格日级核心或其他独立产品停止交付。执行范围由 [P10 #505](https://github.com/rv198-star/Quanthus/issues/505) 和现行版本化政策 `config/pipelines/minute-daily-tplus1-budget.v2.json` 约束（V2 替代 V1 对 60 分钟硬结束的解释；V1 历史记录保留），运行时部署需要独立验收。

| 处理阶段 | 预算 | 目标 | 允许结束状态 |
| --- | --- | --- | --- |
| **T 交易日** | 30 分钟常规目标；**累计处理到 60 分钟停止启动新操作** | 源原始采集、基本真实性/质量核验、R0/R1/R2/R3 分级及分产品交付 | 合格数据按用途入库、剩余质量缺陷公开 |
| **T+1 下一交易日** | 对 **T 数据额外给予 60 分钟的新操作准入窗口** | 验证来源是否发生后置修订；仅对有新证据的受影响股票定向补采、重新分级和版本化更正 | 合格修订另出新版本；没有新来源证据时提前停止 |
| **超过 T+1** | 无默认自动追加 | 仅有明确消费者价值和新证据才单开定向恢复 | 保留 R2 质量债、R3 不准入，不无限重试 |

**每个目标交易日享有两段各 60 分钟的新操作准入窗口（T + 独立 T+1），而不是累计 120 分钟后强杀工作进程。** 在连续交易日的稳态运行中，同一个自然日可能同时执行本日 T 与上一交易日 T+1，各自账本独立。按权威交易日历计算 T+1，休市/周末顺延。请求、退避、重试、见证、构建和验收均计入活动处理耗时，跨进程及重启累加，不允许通过多开实例或再次触发清零。

**60 分钟为新操作的准入截止，不是正在执行操作的终止截止。** 操作须在本交易日阶段累计活动处理耗时达到 60 分钟之前真正开始且登记 operation_id；截止时已经在执行的、有明确独立超时的原子操作可以完成其真实处理、校验与原子提交，并记录超时段实际耗时；截止时及以后不得启动下一批、下一只/下一分片、后续新重试或未真正开始的预排队操作。**禁止把无限长的全市场任务包装成一个操作规避截止**；单原子操作在 V2 试行规则下有独立最长 10 分钟超时，超时仍按自身失败治理，而非凭 60 分钟统一 kill。T+1 按同一语义执行；已启动任务的收尾耗时不会被伪装成新增的 60 分钟操作预算。

T+1 **先做 12–24 只跨市场、有代表性、包含已知异常的 source-maturity canary**，判断目标交易日的原生分钟观测是否真正变化。提供方历史接口返回滚动近 800 根时，完整 HTTP 响应 SHA 变化 **不等于指定交易日被修订**：必须同时比较绑定到 `market_date+security` 的原生分钟记录/观测 SHA、真实缺口、日期成交量与收盘价。来源未实际修订或无法增加资格时，记录并立即停止，不能用剩余预算反复扫同一数据。

**质量/存储边界维持 Owner 2026-10-09 的明确决议**：R0/R1 允许按用途入库；R2 **允许保留真实观测入正式仓库，逐证券记录缺陷、时间戳、SHA、PIT 和限制，默认不作为常规研究合格信号**；R3 **不入正式 ODS/DWD 的原始响应或 OHLCV**，仅保留隔离区证据和拒绝元信息。现有封存 V1 不可为了适用该规则而重写，执行新规则须通过 [#503](https://github.com/rv198-star/Quanthus/issues/503) 的 V2 版本验收。严格 240 根、PIT、P3/P5 冻结权限与 P6 决策不受影响。

每运行满 10 个交易日复盘 T/T+1 的实际累计耗时、超过准入截止的收尾耗时、来源真实修订率、R2/R3 变化、消费者实际受益和正常日级 SLA；调整预算须单独版本化，不因争取清零而无限延长。

## 2026-10-10 Owner correction — calendar-day T+1 and actual repair

This decision supersedes the 2026-10-09 *next-exchange-session* timing only, not the accepted 60-minute admission budget or quality gates. T must be verified as an open exchange trading day, but its T+1 maturity recovery is scheduled at 10:30 on the **next calendar day**, including Saturdays and holidays. A verified target T is not created for nontrading dates. Deadline warning checks likewise move to the next calendar day, with one message per target date and phase.

T+1 first compares 12–24 date-bound canaries. Genuine target-date revisions trigger one bounded, identity-bound pass over the affected R2/R3 cohort (50-security max atomic shards, each independently timeout-bounded) and versioned source-grade publication. An unchanged vendor response does not authorize a speculative repoll. The research-ready gate remains R0/R1 >=99% overall and >=98% per board; R2 storage is not R0/R1 research acceptance and R3 never enters formal ODS/DWD.

The 2026-10-08 historical V1/V2 and old T+1 ledger are immutable. Owner-authorized one-off recovery retains their SHA-bound source receipts and charged elapsed budget, publishes a distinct `loss-aware-1m-historical-correction-v1` version and reports its actual R distribution. Neither a successful collection exit nor a sealed quality package is by itself Research READY. The warning email must display acquisition execution, published-grade distribution, R3 reasons and consumer readiness separately.

## Consequences and change control

- P10 is the sole source-qualification/warehouse engineering owner; business owners define admission sensitivity and accept products only under their own contracts (ADR 0015).
- #495 owns quality grades/manifest/consumer matrix and impact-cost policy; #496 owns independently buildable daily-core readiness and safe DWD publication, after real dependency inventory. Neither is implemented by documentation alone.
- Source research #492/#493 becomes **impact-led and bounded**: preserve open forensic evidence and only resume expensive precision recovery when demanded by a specific consumer or new independent source capability. Do not close facts simply because a lower-grade product may be usable.
- No changes to accepted P1 evidence, Base V2.1, previous ODS/DWD/OCI releases, exact 1m real-240 qualification, 2,672 existing conflicts, disabled timers, alerts, P3/P5 scientific truth or P6 trading controls arise automatically.
- An implementation PR changing warehouse publication or semantic contracts must include independent review, versioned consumer impact, adversarial missing-required-input cases, Linux shadow replay, rollback and explicit production approval.

## References

- [ADR 0015 — permanent P10 ownership](adr-0015-permanent-p10-data-line.md)
- [LLD 12 — P10 responsibilities](../design/lld/12-permanent-data-line.md)
- [LLD 01 — PIT data foundation and accepted P1 scope](../design/lld/01-data-foundation.md)
- [ODS / 1m reliability parent #456](https://github.com/rv198-star/Quanthus/issues/456)
- [P10 quality grades #495](https://github.com/rv198-star/Quanthus/issues/495)
- [P10 daily-core decoupling #496](https://github.com/rv198-star/Quanthus/issues/496)
