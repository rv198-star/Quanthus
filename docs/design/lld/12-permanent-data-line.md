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

The Owner's 2026-10-08 warehouse principle is decision reliability rather than theoretical
absolute precision. Requirements must fit the declared use, actual decision risk and repair cost.
A local, noncritical defect does not unconditionally block other qualified deliveries. P10 records
the affected tables/keys/fields, source evidence, permitted and unavailable uses, business impact
and the smallest useful repair. A use that needs unavailable precision remains unavailable while
unaffected qualified scopes continue. Source units, identity, dates and actual knowledge clocks
remain truthful; accepting a disclosed limitation does not invent evidence or silently amend a
frozen consumer gate. New tolerance or source-comparison rules require a versioned contract with
the applicable use and evidence, rather than treating provider differences as universal corruption.

Base 2.2 uses the opt-in `p10-base22-purpose-qualified-delivery-v2` contract for its isolated
governance mirror. Its assembler retains pinned native/qualified entities and binds scoped
unavailable uses to local evidence; missing legacy reproduction or supplier authority does not
universally stop unrelated physical delivery. Exact physical/semantic integrity, independent cold
acceptance and all frozen consumer gates remain mandatory. The complete scope, interfaces and
receipts are routed through [the Base 2.2 work item](../../work-items/base22-build.md).

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

The [Daily price-basis candidate](01-data-foundation/daily-price-basis.md) prepares #473 through
the existing versioned Daily builder. Its explicit candidate kind cannot enter default CURRENT,
mirroring or P6 admission; historical replacement and each consumer's acceptance remain separate.

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
| P10-DATA-BASE24-000 | Prepare source-grounded ODS/DWD Base 2.4 and isolated October Daily continuation | P10-DATA-000; Owner successor preparation direction, 2026-10-16 | [Draft takeover/stage plan](../../work-items/base24-build.md); default1d/native5m intent and inherited-source reuse require new versioned contracts, capacity/source/image/reader acceptance; no production activation |
| P10-DATA-BASE22-000 | Base 2.2 独立基准镜像：领域闭合、隔离构建与最终验收 | P10-DATA-000; Owner four-principle continuation 2026-10-08 | #480; [programme](../../work-items/base22-build.md); complete source/repair/33-table/capacity and whole-image acceptance gates |
| P10-DATA-BASE22-010 | 闭合剩余公司行动60键并物化完整领域修复 | P10-DATA-BASE22-000 | #481; exact original body/clock closure, allowed new versions and complete domain replay |
| P10-DATA-BASE22-020 | 闭合股东208冲突及weekly非空领域并完成原生合并 | P10-DATA-BASE22-000 | #482; exact conflict/source dispositions, native versions and complete nonempty-domain replay |
| P10-DATA-BASE22-030 | 闭合normalization原证据或有资格完整新revision | P10-DATA-BASE22-010; #471 retained | #483; original complete evidence or truthful qualified successor, never fabricated legacy |
| P10-DATA-BASE22-040 | 补齐42个August分钟release原始价格口径证据 | P10-DATA-BASE22-000; #473 retained | #484; original mode/policy/deployed-code identity for all 42 releases and exact numerical/time preservation |
| P10-DATA-BASE22-050 | 闭合权威August日历、上市身份和状态生命周期 | P10-DATA-BASE22-000 | #485; all required market/venue/dates and evidenced identity/state changes or unchanged reuse |
| P10-DATA-BASE22-060 | 完成精确33表August覆盖账本与领域集成 | P10-DATA-BASE22-010, P10-DATA-BASE22-020, P10-DATA-BASE22-030, P10-DATA-BASE22-040, P10-DATA-BASE22-050 | #486; exact physical/semantic coverage and one immutable shared history TMP; no empty-table substitution |
| P10-DATA-BASE22-070 | 冻结完整预算并构建CN02实体独立候选 | P10-DATA-BASE22-060 | #487; all gates pass, actual filesystem budget, independent entity files and READY last |
| P10-DATA-BASE22-080 | 完成整份镜像两次独立冷验收和损坏负测 | P10-DATA-BASE22-070 | #488; complete native/streaming no-source/no-network replay, all hashes/global constraints and fail-closed faults |
| P10-DATA-BASE22-090 | 准备精确版本发布、同步和回滚方案 | P10-DATA-BASE22-080 for final identity | #489; reviewable exact-identity plan, no production execution without separate Owner confirmation |

Governed assets: `config/data-ownership.v1.json`, `scripts/sync_github_planning.py`,
`scripts/sync_p10_data_issues.py`, `tests/contract/test_p10_data_ownership.py`, root `AGENTS.md`
and the work-item index. Verify selective/idempotent migration, preserved closed history,
consumer separation and P10 planning. Green contracts or issue movement do not accept data.
