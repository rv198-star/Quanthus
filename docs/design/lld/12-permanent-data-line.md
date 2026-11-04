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

`P10-DATA-BASE25-001` / #596 standardizes the paired Base plus Daily build input closure.
The [Base input operations runbook](../../operations/base-build.md) owns selection freezing,
explicit source/reference supplements and verified recovery retries. Inventory or frozen-byte
receipts are input integrity evidence only; they do not establish semantic admission, a completed
Builder, paired Base acceptance or consumer activation. Original source representation, historical
quality restrictions and actual knowledge clocks remain unchanged.

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

`P10-DATA-OPS-016` / [#537](https://github.com/rv198-star/Quanthus/issues/537)
prepares one versioned pipeline image for separate Daily/recovery/Weekly jobs,
covering acquisition through ODS/DWD verification and sealing, API notification,
Base 2.4 Daily/Weekly GDrive backup and temporary-file housekeeping. Host systemd retains
the Daily/Weekly scheduling clock. Under the Owner's 2026-10-22 direction, lightweight operational
API mail runs inside each data task; it adds no separate mail container/image and does
not open strategy/forward-email transport. See the
[container operations runbook](../../operations/p10-container-jobs.md) for exact
entry points, qualification gaps and controlled switching. Image preparation does
not activate deployment, complete held Weekly domains or grant consumer acceptance.

Daily and recovery share a per-market-date mail lock. A complete/closed result
matching the latest successfully notified status, reason and manifest identities
gets a suppression receipt rather than a second email. Failure/partial transitions,
changed products, Weekly and channel tests remain separately reportable. Existing
events, API identities and successful receipts are preserved; an unsent notification
is never relabelled sent. This notification rule changes no data or consumer gate.

The Owner authorized existing noninteractive sudo for CN02 Docker control. A
deployment selects `docker_access=direct|sudo-noninteractive`; only the fixed
local Docker client is elevated. The host wrapper, leases and receipts retain the
operator UID, and the container retains its configured non-root UID/GID. Sudo
preserves only the explicit Compose interpolation variables, pins the local daemon
socket, and never prompts for a password or changes user groups/socket ACLs.

The same job wrapper owns a unique leased scratch directory below the configured
`QUANTHUS_TMP_ROOT`; containers receive only that run's directory as `/data/tmp`
and `TMPDIR`. Policy `p10-success-tmp-72h-v1` permits reclamation only after a
verified complete/closed job, 72 hours after completion, with no active lease.
Failed, held, interrupted, unknown or unsafe trees are retained. Base, original
captures, Daily/Weekly products, evidence and notification state are never scratch.
The Owner subsequently chose a same-image, independently running `cleanup` role:
its own in-container clock sweeps at 03:30 Asia/Shanghai, catches up one missed
slot on restart, and persists the last successful slot. Only TMP is mounted;
network, data products, credentials and the Docker socket are absent. Manual
one-shot cleanup defaults to preview. The host only validates mounts and starts
or stops the container; it no longer performs deletion or schedules cleanup.
The earlier uninstalled host cleanup unit/timer examples are superseded and removed.

Owner scope also includes fixed directories for original ODS, sealed DWD and
GDrive backup. Formal products are retained: cloud upload is not local deletion
authority. Backup has a separate read-only source role and durable verified-copy
receipts; it must not recollect/rebuild data or relabel partial data qualified.
The older Base 2.3 / 1m archive admission contract cannot admit Base 2.4 / 1d+5m.
The prepared replacement (`config/pipelines/base24-backup.v1.json`,
`src/quanthus/p10_gdrive_backup.py`) binds Base 2.4 and 1d/5m, separate ODS/DWD
folder IDs, exact frozen package inventory and upstream qualification receipts.
Immutable copy and complete cloud size/MD5 checks establish backed-up bytes only;
partial data remains partial. Exact live selection generation/queueing, image
qualification and approved Drive folder mapping remain activation gates. Retired
2.3 series are excluded from automatic sync; historical cloud archives stay.
A native capture budget failure may use the separately versioned
`config/pipelines/base24-native-recovery.v1.json` in explicit recovery mode:
one additional 3,600-second, at-most-two-worker batch per date reuses independently
copied, hash-verified original capture bytes and the same qualified core/source
closure. Completed securities are not acquired again. This does not extend the
normal intake/delivery windows or qualify source rows; terminal events identify
late recovery, with original acquisition clocks and consumer gates unchanged.
A login-only transport repair may retry at most four empty pre-query shards once,
for at most 900 seconds within the original recovery capture deadline. It grants
no further hour, retries no failed row and preserves original failed logs. Initial
SDK login has one paced retry with both actual responses retained; a worker
exception cancels queued work.
Host-side triggers never send API mail or perform cleanup. A daemon failure retains
an unsent event for recovery, rather than claiming an in-container sender ran.

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
| P10-DATA-BASE24-000 | Source-grounded ODS/DWD Base 2.4 and CN02 T-day Daily, REQ v1.3 | P10-DATA-000; Owner v1.3 direction, 2026-10-16 | [#527](https://github.com/rv198-star/Quanthus/issues/527); [execution plan](../../work-items/base24-build.md); received requirement, no deployment or acceptance |
| P10-DATA-BASE24-010 | 证明2020起原生5m与退市证券来源可行性并保全原始字节 | P10-DATA-BASE24-000 | [#529](https://github.com/rv198-star/Quanthus/issues/529); bounded preparation ready; not accepted |
| P10-DATA-BASE24-020 | 闭合历史listing×session分母和逐桶/日/月/年覆盖账本 | P10-DATA-BASE24-000 | [#530](https://github.com/rv198-star/Quanthus/issues/530); bounded preparation ready; not accepted |
| P10-DATA-BASE24-030 | 复用既有8/9月5m并修复9月底来源→DWD断点 | P10-DATA-BASE24-010, P10-DATA-BASE24-020, P10-DATA-BASE24-060 | [#531](https://github.com/rv198-star/Quanthus/issues/531); dependent execution blocked; safe design may proceed; not accepted |
| P10-DATA-BASE24-040 | 构建新ODS闭包及完整33表DWD Base2.4实体配对 | P10-DATA-BASE24-030, P10-DATA-BASE24-020, P10-DATA-BASE24-060 | [#532](https://github.com/rv198-star/Quanthus/issues/532); dependent execution blocked; safe design may proceed; not accepted |
| P10-DATA-BASE24-050 | 适配CN02 1d+5m读取器并生成真实隔离October Daily | P10-DATA-BASE24-040 | [#533](https://github.com/rv198-star/Quanthus/issues/533); dependent execution blocked; safe design may proceed; not accepted |
| P10-DATA-BASE24-060 | 冻结每盘峰值容量、保留/回收与分片恢复方案 | P10-DATA-BASE24-000 | [#528](https://github.com/rv198-star/Quanthus/issues/528); bounded preparation ready; not accepted |
| P10-DATA-BASE24-070 | 完成成对镜像恢复、全表冷验收与损坏失败关闭 | P10-DATA-BASE24-040, P10-DATA-BASE24-050, P10-DATA-BASE24-060 | [#534](https://github.com/rv198-star/Quanthus/issues/534); dependent execution blocked; safe design may proceed; not accepted |
| P10-DATA-BASE24-080 | 准备CN01→CN02单writer交接、OCI/GDrive与成对回滚 | P10-DATA-BASE24-070, P10-DATA-BASE24-050 | [#535](https://github.com/rv198-star/Quanthus/issues/535); dependent execution blocked; safe design may proceed; not accepted |
| P10-DATA-BASE24-090 | 取得CN02自然T日盘后、异常恢复及逐端真实运行证据 | P10-DATA-BASE24-080 | [#536](https://github.com/rv198-star/Quanthus/issues/536); dependent execution blocked; safe design may proceed; not accepted |
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
