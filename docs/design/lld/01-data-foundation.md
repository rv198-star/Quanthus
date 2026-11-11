# LLD 01: Point-in-Time Data Foundation

| Field | Value |
| --- | --- |
| Phase | P1 |
| Design status | approved |
| Delivery gate | accepted-conditional |
| Gate source | `config/p1-gates.v1.json` |
| Governing requirements | FR-DATA-001 through FR-DATA-006 |
| Conditional boundary | C1 residual 4,173 is fail-closed under `p1-c1-conditional-20260725-v1` |
| Coverage follow-up | Issues #38/#40 resolved through OCI DWD revision `daily_ohlcv_none_dwd_v2_oci_20260725` |
| Dual-lane design | ADR 0005, `META-DATA-PIPE-003` |
| Dual-lane delivery | three-node shadow deployment, role-scoped historical ADS bootstrap, and final OCI operational ADS Base accepted; catalog-native consumer publication and cutover remain blocked pending their own evidence and `GO_FOR_CUTOVER` |
| Product-bounded DWD model | ADR 0006/0007/0008/0009, `META-DATA-DWD-002`, `META-DATA-PIT-001`, `01-data-foundation/dwd-domain-model.md`; all 33 objects are released in independently audited `dwd-base-202607-v2.1`; one Base-bound correction is accepted, recurring weekly corrections remain open, rebuild/audit/promotion are node-independent, and generic DWS is deferred |
| ODS/DWD reference | `01-data-foundation/ods-dwd-data-dictionary.md` and `01-data-foundation/ods-to-dwd-lineage.md` |
| Permanent maintenance owner | P10 under ADR 0015 and `12-permanent-data-line.md`; P1 historical acceptance retained |

## 1. Goal

Produce a complete and replayable view of what the system could know at a historical market
date. No downstream module may read vendor frames directly.

## 2. Scope

The opt-in P10 Base 2.4 source interfaces are integrated under
[the bounded P6 main-integration work item](../../work-items/p6-main-integration.md).
`native_base24_reader.py`, `base24_input_view.py`, `base24_p6_handoff.py` and
`base24_p6_purpose.py` own standalone reading, explicit source projection and
purpose qualification. They preserve physical hashes, field times and scoped
unavailable uses; a readable governance snapshot is not historical PIT or an
execution admission. Existing frozen legacy interfaces retain their contracts.

The remaining Base 2.4 construction modules are integrated separately under
[the P10 construction work item](../../work-items/p10-base24-main-integration.md):
bounded native-5m acquisition/qualification, monthly projection and ownership
transfer, independent ODS materialization/verification, domain observation
adapters and paired acceptance reconciliation. They share immutable job-local
prerequisites with explicit TMP lifecycles. This opt-in 1d/native-5m profile
preserves actual knowledge clocks, the exact 33-table DWD model and independent
cold-verification requirements; merging it does not rebuild or switch a Base.

The [P10 collection/container integration](../../work-items/p10-container-main-integration.md)
adds the CN02 1d/native-5m Daily cycle, frozen-input intake, bounded recovery and
same-image notification/backup/TMP roles. Its operational boundary is owned by
[LLD 12](12-permanent-data-line.md#5-execution-and-invariants). Legacy Daily
interfaces remain frozen; Weekly financial qualification is scoped, and source
integration does not activate timers or modify warehouse products.

Base 2.4 delivery status is resolved by `base24_release_status.py` with the exact
manifest and receipt pins in `config/pipelines/base24-acceptance-bindings.v1.json`.
The sealed manifest records build-time state; later independent acceptance is an
additive, hash-bound receipt, never an in-place manifest rewrite. The resolver
requires both ODS and DWD receipts, two distinct source-hidden/network-denied cold
processes per image, and the exact missing/corrupt rejection evidence. Only the
two independent-acceptance checks may supersede their sealed `NOT_RUN` values;
failed schema, key, lineage or other structural checks cannot be overridden.
The original limitation registry remains effective. This metadata reconciliation
reports recorded image acceptance separately from current payload verification,
October Daily/Weekly qualification and research/trading consumer admission. It
does not replace physical reader integrity checks or grant those consumer uses.
The local delivery index locates physical image roots and carries its own receipt
copies; it is not another image or an external runtime dependency. See the current
[Base 2.4 delivery checkpoint](../../work-items/base24-build.md).

The CN02 successor's isolated frozen-input Daily orchestration is owned by
`src/quanthus/pipelines/securities_warehouse/daily_base24_job.py` and
`config/pipelines/base24-daily-window.v1.json` under `P10-DATA-BASE24-050` and
`META-OPS-CN02-001`. The DAG creates one daily-core ODS catalog and one immutable
core, then builds and fully replays the native-5m composite. Only daily and native
5m enter this product; Weekly and 1m are not prerequisites. Pinned source controls
and exact selected releases must match before projection and after verification.
The versioned Shanghai window is enforced through final job receipt creation;
an expired or failed job retains evidence without a job READY. Explicit historical
replay cannot establish natural T-day acquisition or consumer/production admission.
`daily_base24_intake.py` freezes exact already sealed market/event releases,
native-5m and Base identities plus configuration/prior controls into an immutable
dated plan. Publisher and runner share the offline control/date checks. The plan
seal is atomically visible last; same-identity recovery may complete an interrupted
publication but cannot overwrite a different plan. Intake does not scan or qualify
payloads, infer acquisition times or claim producer/consumer admission: each payload
still passes its existing domain verifier in the downstream DAG. The 20:45 intake
deadline reserves the final 15 minutes for build and replay.
This runner consumes already sealed inputs. Acquisition scheduling, fresh source
qualification, independent new-process cold acceptance, notifications and single-writer
cutover remain separate obligations; timer examples do not install or enable them.
See [the migration work item](../../work-items/cn02-project-migration.md) for operation.

The source-producing successor is `daily_base24_cycle.py`, invoked by
`ops/bin/run-cn02-base24-cycle`. Its source-only worker imports the separately
pinned published collector revision `f839af0033ab4aa8cbe6d9fb933f29db88792e91`;
it never invokes that revision's legacy complete-Daily/DWD cycle or loads old-node
service environments. The legacy lane schedule is used only as the collector's
validated request protocol, not as CN02 deployment ownership, a 1m dependency or
a T+1 schedule. A new private `cn02_base24_daily_cycle_v1` configuration pins the
Base, release policy, collector and pipeline revisions, isolated output root and
at most two native-5m capture workers. One target-date supplier universe is shared
by market/events acquisition; one immutable daily reference binds all 5m shards.
Only two source lanes and supplier-native 5m are invoked. Raw SDK strings and real
observation clocks are retained. Unknown provider availability remains unknown.

The cycle verifies the actual authoritative calendar, including every requested
date, before deciding whether to collect market prices. Closed dates still acquire
announcement evidence against an explicit previous open-date universe; they never
create a price session or an empty substitute market table. On open dates the
cycle constructs and publishes a qualified daily core once before optional 5m
completion. An unqualified 5m scope retains its original R3 status, a local alert
and a core-only receipt; it cannot enter the composite pointer or receive a final
cycle READY. This preserves useful qualified daily data without weakening strict
native quality checks. Core and full-product pointers use new Base2.4-specific
names and never masquerade as legacy CURRENT or grant research/execution admission.

The dated index serializes updates across backfill and current-day jobs. Replaying
an older date cannot regress the latest pointer; each pointer discloses known
per-date states rather than implying gap-free coverage. Exact completed retries
reverify and reuse. The configured Shanghai intake/delivery windows remain binding;
late work requires explicit recovery mode, keeps actual completion time and is not
described as on-time delivery. Ordinary operation permits at most two attempts per
date before requiring an explicit disposition. Write budgets and per-device free
space/inode checks precede each bounded batch; child processes have finite time/RSS
limits and retain failures. Formal cold acceptance and node cutover remain explicit
evidence obligations. The Owner separately authorized actual CN02 Daily activation
and the October 8 onward backfill on 2026-10-20; this does not activate P3/P5/P6.

Physical data contracts and P1 historical acceptance remain here. From 2026-10-03 acquisition,
shared quality/governance, maintenance and source recovery/repair belong to P10 under
[`12-permanent-data-line.md`](12-permanent-data-line.md). Other lines only register data defects
and choose a disclosed business bypass, temporary ignore or acceptance of a known limitation;
they do not repair data. The source, schema, PIT, topology and release contracts below are unchanged.

**P10 数仓质量与发布依赖的现行上位原则：** 2026-10-08 接受
[ADR 0016](../../architecture/adr-0016-value-proportional-warehouse-quality.md)：
数据质量按实际消费者用途、业务风险和修复成本裁决，非关键分钟缺口
不得无条件阻断其他独立合格的数据产品。下述 P1 历史验收与物理合约、
PIT 及不可变发布边界仍保持原状；四 Lane READY 的生产解耦属于
[P10 #496](https://github.com/rv198-star/Quanthus/issues/496) 的单独工程评审/发布，
并**未**由 ADR 0016 自动改成新的生产 READY 条件。

P1 includes SSE/SZSE daily bars from 2011 onward, security lifecycle, trading calendar,
corporate actions, adjustment factors, tradability, historical ST state, board-specific price
limits, delisted securities, static level-one industry mapping, and snapshot manifests.

P1 excludes BSE, B shares, explicitly configured non-A-share instruments, news, financial
statements, historical concept membership, and official minute features. The active frozen
`config/p1-universe.v2.json` is the explicit selection contract; it must name exceptional
exclusions with reasons rather than silently dropping them. Official 5-minute data begins in
2024-07 and its consumption is deferred until event-window work; acquisition continues to retain
recoverable history. One-minute data beginning 2026-03 is continuously acquired as archive-only
research data because its upstream retention window is short.

The DWD child defines a product-bounded model over the governed ODS corpus. Its seven
business domains follow the Quanthus research objective; security/listing identity, venue,
calendar, report concepts, and similar structures remain internal support rather than additional
domains. It does not retroactively add financial statements, minute features, or other later
content to the accepted P1 scope. The current immutable `V2 Build202607` identities are bound by
`config/releases/v2-build202607.json`. The DWD build manifest retains its direct ODS build-time
identity (`ods-base-202607-v1.2`); the current `ods-base-202607-v2` name has the same content digest
and a new manifest after the naming migration. Parent and Hotfix identities remain embedded
lineage rather than required external roots. An exact same-run predecessor candidate may supply
declared referential identities after full package verification. Historical DWD/ADS packages and
mutable pointers remain retained but cannot be read as sources, migration targets, reconciliation
baselines, or acceptance evidence for the new Base.

Issue #78 is closed by `dwd-base-202607-v2.1` from R18. The 33 tables and physical partitions pass
package/schema, semantic contract, domain invariant, coverage/exclusion, and lineage-authority
audits; exact digests are recorded in the release acceptance packet. The rebuild workflow is
checkpointed and pointerless. Its execution host is not part of the data contract: a host is
eligible when immutable inputs, clean revision, ext4 filesystem semantics, capacity, and resource
bounds pass preflight. Promotion deeply verifies the candidate and retained predecessor, installs
under a local single-writer lock, and compare-and-swaps `BASE_CURRENT.json`; the old V2 package is
retained for rollback.

P1 acceptance establishes deterministic replay and fail-closed behavior. The initial S3 full build
exposed 276 securities with incomplete normalized unadjusted-price history and 639,009 affected
execution rows. Issue #38 restored their source history in a new OCI child revision. The rebuilt S3
has zero unexpected prior-close gaps; 72 exact first-supported-trade boundaries remain separately
coded and fail closed because no earlier tradable close exists inside the supported history.
Issue #40 subsequently repaired three active histories frozen at an old 2,000-row acquisition
checkpoint. Its OCI child retained the accepted parent and inserted 5,334 exact unadjusted tail
keys from independently checksummed raw evidence.

## 3. Data layers

| Layer | Mutability | Contract |
| --- | --- | --- |
| Landing | Append-only | Original payload plus source, request, acquisition time, checksum |
| Normalized | Rebuildable/versioned | Canonical columns, types, lifecycle, and source revision |
| Curated | Versioned | Adjustment factors, tradability, gaps, and derived state |
| Snapshot | Immutable | Atomic dataset references at a knowledge cutoff |
| Lane candidate | Immutable | One epoch-bound DWD/DWS/PIT family produced from one lane's ODS |
| Canonical catalog | Immutable | OCI acceptance records and a market-date to generation mapping |
| Operational ADS | Immutable | Release-profile output bound to an exact catalog and generation subset |
| Research bundle | Immutable | Self-contained, scope-authorized semantic tables for replay or training |
| ODS Base | Immutable | Hermes-authoritative reconstruction seed with one checksum-identical CN01 mirror |

Raw files never receive in-place corrections. A source correction creates a new revision and
new downstream snapshot id.

The non-activated [P10 Daily price-basis candidate](01-data-foundation/daily-price-basis.md)
addresses #473 through an explicit version of the existing Daily builder. Its candidate package
cannot enter default CURRENT or consumer admission; source replacement and release remain gated.

The additive [Base 2.2 independent image engineering contract](01-data-foundation/base22-independent-image.md)
prepares CN02-only synthetic construction and cold verification through the Shanghai business
date 2026-08-31. The separate approved Phase2 scope permits bounded retained-source governance
and observation outputs; full source qualification, complete-image acceptance and release remain gated.
The linked child also owns the approved opt-in holder exact-column preparation, with the
legacy model and sealed observations retained unchanged.

The ODS Base packaging, source precedence, completeness boundary, and recovery lifecycle are
owned by [`01-data-foundation/ods-base.md`](01-data-foundation/ods-base.md). It is separate from
the DWD/DWS history seed and from offline research bundles: the Base preserves accepted source
evidence needed
to rebuild downstream layers, while those other packages expose normalized or consumer-facing
data.

The curated daily view filters the versioned universe but retains an in-scope raw row that
violates a market-data invariant with `data_valid=false`. Downstream new-entry logic fails
closed from that flag and the corresponding gap/tradability state; it must not delete the row
or invent a structural-break exit.

## 4. Canonical datasets

### `security_master`

Primary key: `security_id`.

Required fields: code, exchange, board, list date, delist date, name validity interval,
security status, source, source revision, and knowledge cutoff.

Codes are attributes, not identity. A code or name change must not create a new instrument
unless the exchange identity changes.

### `trading_calendar`

Primary key: exchange and market date. Fields include session state, previous/next session,
and source revision.

### `bars_daily`

Primary key: security id and market date. Store unadjusted OHLC, volume, amount, source,
source revision, acquisition time, and row checksum. Prices must satisfy low <= open/close <=
high and non-negative volume/amount.

### `corporate_actions` and `adjustment_factors`

Corporate actions retain announcement, registration, ex-right, and payment dates separately.
Factors are effective-date versioned and reproducible from action inputs. A historical query
may use only factors known at its knowledge cutoff.

`P1-DATA-004` governs the replay-facing normalization boundary. The source corporate-action corpus
contains one frozen share-quantity unit but mixed cash units. The v1 factor-only fallback remains
reproducible, while `config/p1-corporate-action-normalization.v2.json` makes an exact Eastmoney
implementation plan the first authority. Its notice must precede the effective date, its plan text
must agree with its structured cash/share fields, and source fields must select one uniquely best
same-security/same-date candidate.

Eastmoney cash and share quantities are per ten shares. Binary float noise is removed at `1e-12`;
display-precision matching is followed by business tolerances of `0.00001` yuan per share for cash
and `0.0001` shares per ten shares for bonus/transfer quantities. These tolerances absorb
economically immaterial source precision differences. They do not absorb materially different cash
plans, missing dates, late notices, or conflicting plan text.

When exact authority is unavailable, each non-zero source cash value is tested as either per share
or per ten shares. The selected factor fallback must reconcile the observed ratio to:

```text
(1 + bonus_share_ratio) * prior_unadjusted_close
/ (prior_unadjusted_close - cash_dividend_per_share)
```

The maximum absolute factor-ratio error is `0.005`, and the best two cash candidates must differ
by at least `0.00001` error. The preferred baseline is the previous PIT-visible adjustment factor;
when that factor is unavailable or not historically visible, the prior-day qfq/raw close ratio is
allowed only as snapshot-bound corroboration. It does not become a strategy feature or repair a
factor cutoff. Missing dates, cross-checks, candidate separation, or factor agreement remain
explicit exceptions. Under v2 they enter a versioned `security_fail_closed` ledger: dated
exceptions block from the effective date, while missing-effective-date rows block from their
announcement date. A report is ready as publication input only when every exception is covered by
that same-version ledger.

`src/quanthus/corporate_action_corpus.py` is the read-only real-shape adapter for this contract. It
binds the action source, relevant Eastmoney raw files, raw bars, qfq bars, and factors by SHA-256 and
one immutable P1 snapshot manifest. The 2026-07-29 audit normalizes 35,157 dated rows and versions
all 272 retained exceptions without granting execution authority.

`src/quanthus/corporate_action_revision.py` atomically materializes that audit as normalized-action
and exception-ledger Parquet datasets. It always rebuilds the bounded full corpus, verifies its own
checksums and row reconciliation, and compares exception ids with an optional verified prior
revision to report new, persistent, and resolved exceptions. Known exception reason codes require
no AI or row-by-row approval. `check-corporate-action-revision` gates a revision and
`promote-corporate-action-revision` advances its immutable CURRENT pointer without allowing the
knowledge cutoff to move backward.

`src/quanthus/historical_replay_snapshot.py` extends one verified P1 snapshot with the accepted
corporate-action revision and authoritative calendar, materializes by hard link, and runs the
historical-bundle gate before the catalog's atomic rename. Snapshot
`p1-historical-replay-20260729-v1` contains all three
formerly missing datasets and is ready for frame construction with `execution_authorized=false`.
Evidence: `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md`.

`quanthus audit-corporate-actions` is the repeatable interface. It prints a stable summary, may
atomically write the full row-level report and exception ledger to explicit paths, and returns
non-zero whenever an exception is not versioned or no normalized row is available.

Future accepted full-corpus or authority revisions use `build-corporate-action-revision` with the
current verified revision as their comparison baseline. A known unresolved row remains explicit in
the next ledger; newly resolvable evidence removes it and appears in the resolved count. Schema or
source-semantic drift, an unknown reason code, checksum failure, backward cutoff, or abnormal
exception-volume change is an operational alert rather than an invitation to silently repair data.
The command is ready for the separate weekly OCI transform; scheduler wiring remains an operations
follow-up and is not inserted into the daily market critical path.

### `tradability`

Primary key: security id and market date. Include suspension, ST state, list days, upper/lower
limit prices, limit regime, data-valid flag, and reason codes. The 2020-08 ChiNext 20 percent
regime change is explicit policy data, not an ad hoc conditional.

The first materialized layer is `tradability_coverage`: it crosses every in-scope security with
every open exchange session and classifies the row as `trading`, `suspended`, `not_listed`,
`delisted`, or `missing`. It uses only suspension records visible by the requested knowledge
cutoff, retains invalid observed bars with `data_valid=false`, and fails new entries from that
flag. Historical ST and price-limit fields are later S2/S3 enrichments; their absence may not be
represented as a tradable value.

The price-limit policy also carries the 2026-07-06 main-board risk-warning change from 5% to 10%
as an effective-dated regime. A historical replay selects the rule for that market date; it must
not project the current 10% rule backward.

`data_valid` describes a structurally valid source bar, not fillability. A visible suspension
takes precedence over a vendor placeholder bar. A structurally valid bar with zero volume or
amount has `is_executable=false` and reason `zero_liquidity`; downstream entry logic must not
simulate a fill from its close.

The historical replay reader adds one conservative continuity check over these accepted inputs.
When an observed raw close falls outside the resolved daily price-limit interval and no normalized
action explains that effective date, or the action-adjusted gross return still exceeds the
effective price-limit percentage, the exact security/date is
`unexplained_price_transition`. It blocks new simulated execution, aborts an affected held-
position replay before a false mark enters the ledger, and invalidates rolling research features
until their complete lookback is clean. This is a deterministic data-quality disposition, not a
structural-break label or inferred corporate-action repair. Its count remains explicit in frame
and feature-panel reports so an upstream corpus repair can remove it reproducibly.

### `data_gaps`

Primary key: security id and market date. Every absent expected bar is one of `not_listed`,
`suspended`, `delisted`, or `missing`. Unknown is not an allowed accepted-state value.

### `industry_map`

Static level-one CSRC or Shenwan mapping. It is valid only for concentration controls and its
non-point-in-time limitation must be present in lineage. It cannot enable the sector expert.

### `dataset_manifest`

Include dataset name/version, source revisions, row count, partitions, schema hash, content
checksums, creation time, knowledge cutoff, and parent manifests. Portable manifests use safe
bundle-relative paths only and reject external links or producer-local database/path references.

`content_digest` covers deterministic data, schema, exact upstream inputs, and policy. Volatile
timestamps are excluded. `manifest_digest` identifies one exact immutable revision envelope, while
the idempotency key binds the epoch, exact inputs, and policy used to recognize a retry. Results
bind an exact revision and manifest digest; reproducibility compares content digests.

## 5. Dual-lane daily publication

### 5.1 Epoch and candidate family

OCI publishes one immutable arbitration epoch input for each market date. It binds the expected
canonical parent or historical base, every shared reference/PIT input, and the transform, runtime,
schema, gate, clock, schedule, and arbitration contracts. Both lanes must verify the same epoch
manifest before building. A parent/reference mismatch or any attempt to infer a new `security_id`
makes the candidate ineligible.

The target candidate family contains DWD, its exact lineage-bound DWS projection, and every
required lane-bound PIT/status/tradability artifact. The delivered v1 slice is narrower: CN01 and
Hermes each produce DWD daily bars, DWD daily status observations, and one DWS daily market summary
from their own ODS. The v1 `pit_cutoff` source-time check is not a full PIT/tradability family.
The delivered epoch carries the OCI-selected `ref_security_master.parquet` together with the
universe and code-validity policies; lane-local security-master drift cannot redefine candidate
identity or create a false cross-lane business divergence.
Its id binds both the canonical-source pointer and the verified copied parent/base digest. Epochs
and candidates created before this shared-reference contract remain verifiable as retained audit
evidence, but are ineligible for new candidate construction or arbitration.
`READY` is written last. Candidate ids are immutable and attempt sequencing is monotonic within
`(lane_id, market_date)`; a rerun does not reuse an id with different lineage.
Lane-history continuity follows those sealed epoch sources: the first candidate binds the governed
historical base, while each successor binds the exact prior canonical DWD copied into its own
epoch. A Hermes successor may therefore name a CN01 canonical revision; requiring a same-lane
parent would contradict the fixed-primary arbitration design. The logical catalog records the
epoch and canonical-parent digests and requires the parent date to equal its preceding partition.
The exact lane ODS `CURRENT` descriptor is owned only by `market-daily-core`. Natural-day event
releases remain immutable ODS evidence but cannot replace the market-data attempt selected for a
DWD/DWS candidate.

The daily epoch carries only the exact parent or historical-base identity required for candidate
lineage; it is not the historical data-transfer mechanism. The separate
`dual-lane-history-base-v1` migration bundle contains the complete accepted P1 DWD seed and one
historical DWS projection. DWS begins where observed daily status evidence begins and scopes bars
to matching status keys, preserving missing historical bars as lower `bar_security_count` rather
than inventing rows. Both nodes seal an identical common bundle, then publish a node-local logical
catalog consisting of that seed plus their own ordered daily candidate chain. The catalog rejects
the first candidate unless it binds the exact seed revision/META digest and rejects every later
candidate unless its DWD parent revision and manifest digest equal the preceding entry.

### 5.2 Independent gates and arbitration

OCI validates each candidate independently before cross-lane comparison. Integrity, identity, PIT,
coverage, schema, clock, and business-quality failures are lane-local evidence and cannot be
repaired with rows or gate results from the peer candidate.

That sentence defines the target gate family. The delivered v1 repository slice verifies integrity,
lineage/identity, exact lane ODS `CURRENT` attempt selection, source cutoff, schema, coverage,
business equivalence, epoch/runtime/reference binding, fixed-primary arbitration, and contract-
trusted Shanghai-time windows for its three artifacts. Full PIT/tradability gates and real host
NTP/skew qualification evidence remain open and must not be inferred from epoch metadata or
application timestamps alone.

CN01 is accepted when its eligible candidate is ready by cutoff and passes every independent hard
gate. Hermes is eligible only after an independently established CN01 missing, late, unverifiable,
or hard-gate failure and must pass the same gates itself. If both pass but an unresolved material
difference remains, the epoch ends as `withheld_unattributed_divergence`; OCI publishes neither.
Arrival order, favorable prices/returns, and whole-file hash proximity are never selection rules.

A fenced lease plus compare-and-swap permits at most one accepted generation for an automatic
epoch. Late, duplicate, and stale attempts remain evidence and cannot reopen it. Closed/open
calendar disagreement, excessive clock skew, or parent/reference/runtime drift blocks both lanes.

### 5.3 Acceptance, catalog, and release set

The publication graph is strictly one-way:

```text
candidate -> acceptance record -> catalog snapshot -> operational ADS
          -> release set -> publication intent -> release-set CURRENT
```

An immutable acceptance record binds one selected market-date generation, its complete lane family,
all source and reference lineage, independent gates, reason, digests, and `accepted_at`. It does not
refer to a future ADS or release set. A catalog snapshot maps each included market date to one
accepted, non-revoked generation and is never independently promoted.

An operational ADS revision binds one exact catalog snapshot and the exact generation subset it
read. The release-set manifest binds that catalog, the release profile's required canonical inputs,
and every mandatory operational ADS output. OCI seals all members and release-set `READY`, then
writes an immutable `release_set_publication_intent` before atomically advancing
`release-set_CURRENT.json`, the only consumer-visible pointer. The intent binds the prior/new
release-set identity and new manifest digest. Its `published_at` field records intent creation and
does not prove that `CURRENT` advanced; `CURRENT` is the commit point and binds the intent path and
digest. A crash may leave an intent with no new consumer publication, and retry validates and
reuses that intent. The delivered contract emits no separate post-CAS success record.
Compatibility dataset pointers are projections only and cannot authorize or compose a consumer
view.

The node-local lane-history catalog is distinct from this canonical catalog. It is a verified
recovery and continuous-read index over one shared history seed plus one lane's immutable daily
partitions. It has no canonical acceptance record, no ADS or release-set authority, and cannot be
used to claim that the shared historical seed was independently acquired by both lanes. Its
`CURRENT` selects only a new immutable catalog envelope; it does not mutate or concatenate DWD/DWS
files.

Each successful daily candidate service triggers an independent node-local catalog refresh rather
than relying on a wall-clock merge timer. The refresh takes the lock for that lane's catalog root,
rebuilds the complete logical chain from immutable `CURRENT` descriptors, verifies the result, and
then atomically advances only the lane-history `CURRENT`. Repeating the refresh over unchanged
inputs reuses the same catalog bytes and pointer bytes. A refresh failure emits the standard unit
failure alert but does not mutate or invalidate the already sealed candidate; OCI arbitration does
not consume this node-local catalog.

If ADS build or validation fails, the acceptance record remains internal evidence but no catalog,
ADS, or DWD/DWS-only state becomes consumer-visible. The legacy and target publishers share one
fenced single-writer lease; target cutover first quiesces and fences the legacy writer.

### 5.4 Historical ADS lifecycle and layout

ADR 0013 supersedes ADS as a current application-data concept. This section records the retained
historical publication/reproduction contracts only; current P4 application preparation uses the
thin Research ADM. The former operational ADS participated in one atomic OCI release profile, and
portable research bundles were separate frozen revisions assembled from exact catalog snapshots.

The delivered `operational_daily_v1` ADS is only a bounded migration projection. It contains the
three decision-visible tables `market_daily`, `market_status`, and `market_summary`, and its
manifest is `research_only=true` and `execution_authorized=false`. It is not the portable research
bundle contract described below and does not yet supply strategy replay, event-library, feature,
label, execution-runner, or training surfaces.

`META-DATA-BASE-003` adds a distinct OCI-owned operational historical Base envelope below
`/srv/quanthus/ads/operational/base/`; its detailed contract is in
`01-data-foundation/operational-ads-base.md`. The repository builder requires one exact DWD/DWS/PIT
manifest family and a complete export/reuse ledger, preserves source cutoffs and table lineage,
separates decision/runner/outcome capabilities, and reuses the existing typed strategy replay
reader. No real Base is accepted until the provisional #48 packages exist and the clean-path smoke
passes. Its administrative `BASE_CURRENT.json` does not replace consumer release-set publication.

The delivered portable `strategy_replay` profile is specified by
`01-data-foundation/strategy-replay-ads.md`. Each bundle contains the seven replay tables plus the
checksum-bound CSI 300 curve and static concentration-only industry mapping required by the active
P3 runners. It binds exact content and manifest identities into the feature panel and run manifest,
authorizes one configured reader role, and never resolves ODS, DWD/DWS, PostgreSQL, a legacy
snapshot path, or any mutable `CURRENT` while running. Exact historical reproduction commands may still use this profile; current P4 does not. The hidden
snapshot path is retained only to reproduce frozen P3 evidence.

The initial portable layout has no mandatory calendar-year directories. Each semantic table is
date-sorted where applicable and exposes one or a small number of parts through
`tables[].parts[]`. A part declares its role, safe relative path, SHA-256, size, row count, schema
hash, date bounds, and sort keys. Consumers use the manifest rather than globbing filenames or
inferring semantics from partitions. A future size-based layout requires measured evidence and a
new immutable bundle revision.

Decision-visible, runner-only execution, and label-only outcome tables are separate evidence
surfaces. Development, arbitration, permanent-blind, and forward-simulation scopes are physically
separate authorized bundles. Warm-up context cannot become a sample; a horizon crossing a sealed
scope is excluded or right-censored without reading that scope. P4/P5 own event, label, feature,
target, split, and model semantics, while this LLD owns the common publication envelope.

Each source input retains its original `knowledge_cutoff`; candidate readiness, acceptance, bundle
build, and release publication are separate timestamps. Historical replay uses per-decision PIT
snapshots even when its bundle is built later. Forward/operational decisions additionally require
the release set to be resolved from `release-set_CURRENT.json` and its bound
`published_at <= decision_time`; a publication intent alone is insufficient.

### 5.5 Correction and retention

A late primary after fallback is either `late_equivalent` with no republication or a
`correction_proposed` record that changes no pointer. Approval creates a new generation with
supersession, authority, policy, and diff evidence. A corrected historical date cannot enter a new
catalog while publication-critical successors still depend on the old generation; required
DWS/PIT, operational ADS, and active serving projections are rebuilt or explicitly revalidated.

Existing frozen research bundles/results remain immutable retained descendants. Impact,
supersession, or revocation is recorded separately and does not force their rewrite or block an
operational release. A revoked generation cannot enter a newly published or rollback-selected
release set.

ADR 0005 implementation is copy-only. Existing ODS, OCI mirrors, DWD/DWS, ADS, pointers, logs, and
evidence are not deleted, moved destructively, or reused as staging. The exact accepted ODS rebuild
basis must have a failure-independent recovery copy before cutover. Any future deletion requires a
separate reviewed decision and is not authorized by this work item.

The same cutover block applies to the historical DWD seed. An epoch containing only its META digest
is lineage evidence, not a recovery copy. CN01 and Hermes must each hold a complete root-owned,
checksum-verified bundle plus a verified lane-history catalog through the latest candidate. The
common seed remains explicitly non-independent; daily lane evidence begins only after its boundary.

The repository implementation now records late-equivalent or proposed-correction outcomes,
requires explicit approval before publishing a corrected immutable generation, prevents revoked
generations from entering new release sets, and performs rollback under a fresh fenced lease and
SHA-precondition pointer CAS. It also records accepted-ODS recovery copies, restore assessments and
drills, and denies purge until every retention precondition passes. Production evidence for these
paths, creation of an externally reviewed corrected release set, and failure-independent restore
qualification remain open; code coverage alone does not authorize cutover or cleanup.

Accepted-ODS recovery assessments verify each retained copy receipt against its existing
SHA-named `copy-index` entry before counting failure domains. The index must bind the exact
receipt bytes, copy id and root-relative path; missing, orphaned or inconsistent index entries
and symbolic-link evidence fail closed. An interrupted receipt/index write is not recovery
proof and must not be repaired by deriving a new index from untrusted receipt contents. This
P10 / `OPS-HERMES-GC-001` (#310) evidence-integrity slice is covered by
`tests/unit/test_accepted_ods_recovery_receipts.py`. It preserves the correction horizon and
copy-only purge prohibition, does not re-enable the retired dual-lane runtime, and does not
implement successful-cache GC, live copy revalidation, reference inventory or cleanup authority.

### 5.6 Monthly-complete DWD/DWS Base

`META-DATA-BASE-002` materializes one immutable package per DWD/DWS layer without changing
canonical authority or the consumer release-set pointer. OCI freezes one deterministic input ledger
from an exact canonical catalog snapshot. The ledger discloses the legacy-accepted seed as such and
binds every absorbed date's canonical acceptance, lane, candidate/content identity, epoch parent,
ODS release, shared references, source cutoff, transform/runtime, and quality gates. Missing dates,
parent discontinuity, lane mixing, or conflicting business keys block the build.

DWD uses manifest-discovered members. Schema-compatible daily members retain exact source bytes;
schema-distinct historical and daily members are not coerced into a false common schema. Every part
records safe relative path, SHA-256, bytes, row/key/null/duplicate facts, schema digest, and date
bounds. Boundary gates prove bars have status/tradability evidence and every status key resolves
through exact code-validity evidence. A historical universe snapshot ending earlier than the
status boundary is reported rather than silently extended.

DWS is one deterministic projection from the exact DWD package and binds its package id, manifest
SHA-256, and content digest. The builder recomputes the historical and absorbed daily summaries,
requires exact row equivalence to the prior seed-plus-daily projection, and rejects any duplicate
market date or aggregate-invariant failure. Package identity excludes input/output directory and
attempt time but includes the frozen ledger, policy, code/runtime, member facts, and projection.

Publication is two-stage. `install-dwd-dws-base` copies and verifies both packages without a
descriptor. Only after OCI, CN01, and Hermes prove identical bytes may
`advance-dwd-dws-base-current` write the new layer-local `BASE_CURRENT.json` files under explicit
old-pointer SHA preconditions. The descriptors cross-bind the peer layer and pair digest. The
candidate-success catalog runner detects the complete active pair, builds a Base-aware lane catalog
with zero or more exact post-Base candidates, and rejects any first-parent mismatch against the
absorbed canonical tail. It retains the old history-catalog path until both Base descriptors exist.
Do not alter canonical catalogs, ADS, release-set `CURRENT`, schedules, or retained source
artifacts. The final July pair is published checksum-identically on all three nodes and binds the
actual Issue #47 ODS Base plus the accepted 2026-07-31 generation. Corrective policy v2 retains the
provisional package ids after two pre-activation builds bound different code revisions to the
original provisional v1 ids; all provisional v1 artifacts remain retained evidence and are never
overwritten or relabeled.

## 6. Snapshot interface

Conceptual call:

```python
get_snapshot(
    market_date,
    knowledge_cutoff,
    universe_policy_version,
    dataset_versions,
) -> SnapshotRef
```

Publication is atomic. Resolution requires an exact dataset-version mapping and universe-policy
version, chooses only the newest snapshot whose timezone-aware `knowledge_cutoff` is no later
than the requested cutoff, and fails on ambiguity. It returns `market_date`,
`knowledge_cutoff`, `source_revision`, and `snapshot_id`. Downstream queries receive only views
bound to that snapshot. A held security with missing data produces no synthetic structural-break
value.

Under the target publisher, a consumer first resolves one release-set `CURRENT`, then uses only the
catalog and snapshot ids bound by that release set. It never resolves DWD, DWS, PIT, or ADS pointers
independently. Historical reconstruction may publish a snapshot after the historical date, but its
per-row and per-decision information must still satisfy the requested historical cutoff. A forward
decision additionally requires the bound release set to have been published by its decision time.

## 7. Pipeline sequence

The ADR 0005 target daily sequence is:

1. OCI seals and copies the exact epoch parent/base, shared references, and frozen policies to both
   lanes.
2. CN01 and Hermes independently acquire/checksum ODS and build complete immutable candidate
   families with `READY` last.
3. OCI copies and verifies candidates, then runs every lane's hard gates independently.
4. OCI compares only independently valid candidates and applies fixed-primary/fallback arbitration.
5. OCI copies the selected family into canonical storage and seals an acceptance record.
6. OCI creates a new catalog snapshot, builds and gates required operational ADS, and seals one
   complete release-set manifest.
7. OCI seals an immutable publication intent, then atomically advances only release-set `CURRENT`;
   the latter is the consumer-visible commit point.
8. Optional PostgreSQL projections and portable research bundles consume exact immutable release or
   catalog ids; neither can establish canonical truth.

Each stage is idempotent by exact input manifests, frozen policy, and idempotency key. A failure
before the final compare-and-swap exposes no partial new consumer release.

Until target cutover, recurring production continues to follow ADR 0003. OCI selects and pulls one
immutable ODS handoff, builds the normalized daily DWD partition and lineage-bound DWS projection,
gates both, and advances the legacy pointers. Target candidates, catalogs, ADS, and release sets
live under separate roots and cannot alter those pointers. The cutover gate requires accepted
shadow/failure evidence, recovery and rollback rehearsal, a fenced legacy writer, and explicit
`GO_FOR_CUTOVER`.

Under both paths, DWS binds the exact DWD revision and manifest checksum and is never an alternative
normalized source. The accepted P1 full-history DWD remains the base. Closed sessions are evidenced
by the release calendar and recorded as clean skips. Minute and partial-session data are not inputs
to the current daily profile. Terminal email occurs only after the durable run ledger; delivery
failure is recorded but does not change the data result.

For an open session, daily acquisition first requires BaoStock to return the target date for all
three contract indices. Empty successful responses are availability evidence and never trigger an
immediate reconnect; only provider/network errors do. The full stock scan starts after readiness,
retries only its bounded missing set, records request-local acquisition timestamps, and derives
dataset `knowledge_cutoff` no earlier than the latest observed acquisition. Hermes and CN01 use
the versioned early/late collection-window experiment in ADR 0004; both retain identical coverage
and READY gates. The rendered systemd service checks the contract's node-specific start plus
five-minute activation grace before invoking the lane runner, so restarting a timer after its
window cannot silently turn into an unplanned third observation.

### Daily process ownership and safe retry

`META-OPS-RELIABILITY-001` governs the incremental reliability programme in
[`../../work-items/ods-dwd-reliability.md`](../../work-items/ods-dwd-reliability.md).
Its first slice, `META-OPS-PROCESS-001`, changes process supervision only; data,
schedule, source semantics, and publication gates remain unchanged.

Each daily lane, catalog command, and DWD command runs in an owned POSIX process
group. Completion of the direct shell is not group completion. Before releasing
ownership or retrying, the controller sends TERM, waits up to 10 seconds, escalates
to KILL if needed, and waits up to 10 more seconds for group disappearance. An
unconfirmed group or signalling failure is terminal `process_cleanup_unconfirmed`
(exit 75), persists failure evidence, withholds orchestrator READY, and prohibits
the wrapper's automatic retry. A cleanup error in one group must not prevent
attempting cleanup of other owned groups. Startup failures also unwind ownership;
expired collection deadlines do not launch new lanes.

This is a bounded safety-exit budget, not an extension of acquisition or data SLA.
Collectors must not daemonize or leave their owned group. The deployed systemd
cgroup must cover controller termination and escaped descendants; that Linux
integration is a separate release gate, not proven by local POSIX unit tests.
No historical package, Base, or pointer is modified by process cleanup.

### Minute terminal checkpoint ordering

`META-OPS-MINUTE-001` (#427), the C1 slice of #424, keeps provider semantics and
request/retry policy unchanged. Within one isolated minute collector attempt:

- Before publishing any terminal status, atomically replace `checkpoint.json` with
  all completed per-security success/error results. An unattempted or interrupted
  security does not acquire a fabricated result.
- Circuit breaking and catchable Python exceptions/cancellation attempt the same
  checkpoint-before-failed-status sequence, then propagate the original exception.
  Secondary persistence failure is reported to stderr and must not replace the
  original cause. A failed checkpoint write cannot produce a new terminal receipt.
- Always attempt HTTP session cleanup. Cleanup failure propagates on a normal exit;
  during exception unwinding it is reported without masking the original failure.
- Atomic replacement is per file, not a multi-file transaction or power-loss/fsync
  guarantee. A crash between writes can leave a newer checkpoint and older status;
  missing/stale status never grants READY or recovery-reuse authority.

This is not residual-set retry, checkpoint identity/checksum admission, forced-signal
flushing, or a change to direct formal 5m collection. Default SIGTERM and SIGKILL
are outside Python exception flushing; production process/cgroup validation remains
a separate release gate.

### Opt-in same-attempt 1m residual retry

`META-OPS-MINUTE-002` (#429), C2 of #424, adds a bounded policy on top of C1.
Design status: approved for scoped implementation. Delivery gate: open; production
activation remains blocked by Linux/process and node deployment evidence.

The repository collector accepts `--recovery-policy PATH` only for research `1m`
`incr` with a unique universe. The strict v1 profile is
`config/pipelines/minute-attempt-recovery.v1.json`: explicit opt-in, one additional
round, one-second inter-round delay and 1740-second cooperative attempt budget.
Its exact-byte SHA-256 and policy id are recorded in status. Without the flag there
is no new residual retry or budget. The lane runner passes the profile only for 1m
when `HERMES_ODS_MINUTE_1M_RECOVERY_POLICY` is explicitly set; a seed, custom
collector, non-incremental mode or budget >= outer timeout is rejected before
creating the attempt directory. A 5m lane never inherits this 1m setting.

- Within this invocation only, each next round selects the latest failed symbols.
  Successful symbols are not refetched. Status counts latest outcomes, not attempts;
  checkpoint entries retain per-symbol attempt/round/error history after recovery.
- One monotonic deadline starts before endpoint selection and never resets between
  rounds. Check it before/after provider calls and before accepting responses; clamp
  HTTP/probe timeouts and retry/pacing sleeps to remaining time. Budget exhaustion
  propagates distinctly to terminal `failed` / `budget_exhausted`, without further
  provider requests. An interrupted symbol has no invented completed result.
- Twenty consecutive Eastmoney failures still open the circuit immediately. Storage
  failure is terminal under the opt-in policy, not a reason to repeat network work.
  Initial status persistence now precedes any endpoint selection for all modes;
  failure there opens no session. Terminal C1 checkpoint ordering remains in force.
- This is a cooperative deadline, not a hard cancellation guarantee for an in-flight
  library call or disk operation. The existing outer subprocess timeout and Linux
  process-group/systemd cgroup release gate remain mandatory. This is not yet a
  whole-controller or multi-attempt shared acquisition budget.

No cross-attempt files are admitted or reused by this feature; seed admission needs
its separate complete semantic identity/checksum contract. Existing zero-row and
provider rc/null semantics are not reclassified. Formal direct-5m conformance,
coverage/date/READY checks, immutable Base and CN01-to-OCI DWD sync are unchanged.
Synthetic acceptance includes a 4992-symbol universe with two transient errors:
4994 symbol attempts, not two full scans. This is not a network performance benchmark.

### Qualified 1m materialization consistency

`META-OPS-MINUTE-006` (#455), under the whole-chain programme
[`../../work-items/minute-chain-reliability.md`](../../work-items/minute-chain-reliability.md),
is a fail-closed conformance repair for the opt-in sealed-daily/whole-security 1m
path. Design status: approved for scoped engineering. Delivery gate: open;
isolated Linux smoke passed as recorded in the programme; complete runtime
qualification and node rollout remain separate.

- Before changing status, checkpoint, raw capture or output, require an absent
  or empty attempt home and unique requested symbols. A prior file, partial
  attempt or directory symlink requires a new attempt; preserve all old evidence.
- Never concatenate a qualified source frame with an existing Parquet, whether
  its keys overlap or belong to another date. Cross-attempt replay and historical
  partition replacement require their separate identity/retention contract.
- Serialize the selected whole security into a private temporary Parquet. Read
  it back through the existing exact code/date/240-grid/daily-share/close
  qualifier and require its OHLCV semantic hash to equal the selection hash.
  Only then expose the complete target using a no-replace same-filesystem link.
  A target collision is failure, never overwrite or last-minute merge.
- Record `materialized_semantic_sha256` alongside `source_semantic_sha256` only
  after this check. A suspended security passes the existing empty/zero-volume
  qualifier, writes no bar target and cannot retain a prior target file.
- Storage or consistency failure stops further provider requests, persists the
  complete processed-symbol checkpoint before failed status, and propagates the
  failure. Unknown disk faults are not source-maturity pending.

This does not qualify an inferred source minute, change auction normalization,
remove dual-qualified source conflict blocking, or claim exact minute amount.
PR #454 owns the separate inferred-minute admission repair. Semantic equality
is output consistency, not proof of provider truth. Legacy unqualified/backfill
writers and formal 5m behavior are unchanged. The existing node/attempt lock owns
single-process exclusion; the no-replace link does not prove whole-home concurrent
status/raw safety or power-loss/fsync durability. Linux/filesystem and controlled
rollout evidence are required before activation.

### Runtime qualification before node rollout

`META-OPS-ENV-001` (#431), under programme #424 A/E, tracks the actual interpreter
and dependency boundary separately for orchestrator, lane, minute collector and DWD.
Design status: approved for scoped qualification; delivery gate: blocked.

Preflight must resolve effective service/child interpreter paths, source revision,
unit hash and allowlisted non-secret profile values, rather than assuming a repository
`.venv` is active. Qualification needs versioned, fully resolved role/test-profile
dependencies aligned with both project ranges and applicable frozen runtime contracts.
Historical reproduction profiles do not authorize retired publication routes.
Build new isolated environments; never repair a shared astock environment in place.
An active job blocks a checkout/interpreter switch. Reviewed rollout and rollback
receipts remain distinct from synthetic test success and physical data acceptance.

The 2026-09-22 SSH/Linux evidence is in
`../../reviews/ods-linux-qualification-20260922.md`: the scoped Linux suite and
synthetic cgroup faults passed, while production environment drift and incomplete
full-suite qualification keep rollout blocked. This introduces no new deployed
entry point, package version, timer or data-contract change.

### Implementation conformance map

| Capability | Governing implementation | Contract and evidence |
| --- | --- | --- |
| PIT identity, tradability, and snapshots | `src/quanthus/adjustments.py`, `src/quanthus/tradability.py`, `src/quanthus/snapshot_publish.py`, versioned P1 config | `tests/p1/`, `tests/contract/test_p0_decisions.py`, P1 acceptance files |
| Mixed-unit corporate-action audit and immutable replay revision | `src/quanthus/corporate_action_normalization.py`, `src/quanthus/corporate_action_corpus.py`, `src/quanthus/corporate_action_revision.py`, `src/quanthus/historical_replay_snapshot.py`, `config/p1-corporate-action-normalization.v2.json` | `tests/unit/test_corporate_action_normalization.py`, `tests/unit/test_corporate_action_corpus.py`, `tests/unit/test_corporate_action_revision.py`, `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |
| Immutable ODS handoff and daily DWD/DWS | `src/quanthus/pipelines/`, `config/pipelines/` | `tests/unit/test_ods_handoff.py`, `tests/unit/test_pipeline_runner.py`, `tests/p1/test_daily_warehouse.py` |
| Repository-owned ODS schedule | `src/quanthus/pipelines/lane_runner.py`, `config/schedules/`, `ops/bin/`, `ops/systemd/` | `tests/unit/test_lane_runner.py`, `tests/unit/test_schedule_contracts.py`, ADR 0004 and its runbook |
| Daily process ownership and non-retryable cleanup failure | `src/quanthus/pipelines/process_control.py`, `ops/bin/run-cn01-ods-daily`, `ops/bin/run-ods-daily-with-retry` | `tests/unit/test_ops_process_lifecycle.py`, `tests/unit/test_cn01_ods_orchestrator.py`; #424/#425; Linux/systemd and deployment acceptance still required |
| Minute terminal checkpoint ordering and exception cleanup | `src/quanthus/providers/minute_tdx.py` | `tests/unit/test_minute_terminal_checkpoint.py`, `tests/unit/test_ods_providers.py`; #427; no recovery-reuse authority or production acceptance |
| Opt-in same-attempt 1m residual retry and cooperative budget | `src/quanthus/providers/minute_recovery.py`, `src/quanthus/providers/minute_tdx.py`, `src/quanthus/pipelines/lane_runner.py`, `config/pipelines/minute-attempt-recovery.v1.json` | `tests/unit/test_minute_bounded_recovery.py`; #429; explicit activation, no cross-attempt reuse or formal 5m change |
| Qualified whole-security 1m materialization consistency | `src/quanthus/providers/minute_tdx.py` | `tests/unit/test_minute_materialization.py`; #455 / PR #457; new attempt, staged exact readback and no-replace file visibility; isolated Linux smoke passed, runtime/rollout still open |
| Dual-lane epoch, daily bars/status/summary candidates, fixed-primary arbitration, fenced publication/correction/rollback, recovery evidence, catalog, limited operational ADS, publication intent, and release set | `src/quanthus/pipelines/dual_lane.py`, `config/pipelines/dual-lane-dwd-dws.v1.json` | `tests/unit/test_dual_lane.py`, ADR 0005; runtime qualification and cutover acceptance evidence remains open |
| Sealed DWD/DWS history seed and lane-local logical merge | `src/quanthus/pipelines/dual_lane_history.py`, `config/pipelines/dual-lane-history-base.v1.json`, `ops/bin/*dual-lane-history*` | `tests/unit/test_dual_lane_history.py`, ADR 0005, `docs/operations/dual-lane-data-platform-runbook.md` |
| Monthly-complete DWD/DWS Base packages, guarded Base descriptors, and Base-aware lane catalogs | `src/quanthus/pipelines/dwd_dws_base.py`, `config/pipelines/dwd-dws-base-through-20260730.v2.json`, `ops/bin/*dwd-dws-base*` | `tests/unit/test_dwd_dws_base.py`, Issue #48, `docs/acceptance/meta-data-base-002-dwd-dws-base-202607/README.md` |
| Product-bounded seven-domain DWD/DWS model contract | `config/pipelines/dwd-domain-model.v1.json`, ADR 0006, `01-data-foundation/dwd-domain-model.md` | `tests/contract/test_dwd_domain_model.py`; implementation proceeds under Issue #55 and publication remains gated by Issue #69 |
| Offline strategy-replay research-bundle publication and role-scoped consumption | `src/quanthus/strategy_replay_ads.py`, `config/strategy-replay-ads.v1.json`, `config/strategy-replay-ads.v2.json`, `ops/bin/publish-strategy-replay-ads` | `tests/unit/test_strategy_replay_ads.py`, `tests/contract/test_strategy_ads_boundary.py`, `01-data-foundation/strategy-replay-ads.md`, `docs/acceptance/issue-45-portable-ads-20260730.md`, `docs/acceptance/issue-45-2026-research-ads-backfill-20260730.md` |
| Hermes-authoritative ODS Base, self-contained rollup, and checksum-identical CN01 mirror | `src/quanthus/pipelines/ods_base.py`, `src/quanthus/pipelines/ods_base_rollup.py`, `config/pipelines/ods-base.v1.json`, `ops/bin/*ods-base*` | `tests/unit/test_ods_base.py`, `tests/unit/test_ods_base_rollup.py`, ADR 0005, `01-data-foundation/ods-base.md`, `docs/operations/ods-base-runbook.md`, GitHub issue #47 |
| OCI operational ADS historical Base and replay loader | `src/quanthus/operational_ads_base.py`, `config/operational-ads-base.v1.json`, `ops/bin/publish-operational-ads-base` | `tests/unit/test_operational_ads_base.py`, `tests/contract/test_operational_ads_base_boundary.py`, `01-data-foundation/operational-ads-base.md`, `docs/acceptance/meta-data-base-003-ads-base-202607/` |

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and manifests | Bootstrap | Round-trip typed fixture |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 | Ex-right continuity and no-future-factor tests |
| C2 | Classify every security-calendar gap | S4, security master | No unclassified gap in acceptance corpus |
| S1 | Measure and complete delisted coverage | Security master | Reference-universe reconciliation |
| S2 | Build binary historical ST state from Baostock daily evidence; retain announcements for audit | Landing data | Prior-session PIT shift and dated known-case reconciliation |
| S3 | Resolve board/ST/IPO price limits on actual security sessions | S2, security master | Regime-boundary and suspension-crossing golden cases |
| S4 | Build authoritative trading calendar | Source ingestion | Exchange-session reconciliation |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars | Historical-universe fixture |
| P1-SNAPSHOT-001 | Implement atomic snapshot catalog/API | Canonical datasets | Cutoff and revision contract tests |
| P1-ACCEPT-001 | Run Chapter 01 acceptance suite | C1/C2/S1 and snapshot | D1/D2/D3 evidence bundle |
| META-DATA-PIPE-001 | Operate Hermes ODS releases and OCI T+5h publication | Accepted file-first platform | Open/closed-session rehearsals and durable run ledger |
| META-DATA-PIPE-002 | Retire the legacy weekly monolith and split Hermes ODS schedules | META-DATA-PIPE-001 accepted | Shadow run, explicit cutover, rollback, and no overlapping writers |
| META-DATA-PIPE-003 | Build dual-lane DWD/DWS candidates, OCI arbitration, atomic release sets, and offline research bundles | META-DATA-PIPE-001, ADR 0005 | Contract tests, shadow comparison, failure/recovery drills, and explicit cutover evidence |
| META-DATA-BASE-001 | Build the Hermes-authoritative ODS July Base and checksum-identical CN01 mirror | META-DATA-PIPE-001, ADR 0005 | Classified source inventory, deep verification, clean-path restore, and identical Hermes/CN01 package descriptors |
| META-DATA-BASE-002 | Build authoritative DWD/DWS July Base packages from exact accepted lineage | META-DATA-BASE-001, META-DATA-PIPE-003, Issue #48 | Frozen ledger, semantic equivalence, identical three-node packages, catalog rebuilds, and restore evidence |
| META-DATA-BASE-003 | Build the OCI-authoritative operational ADS July Base | META-DATA-BASE-002, META-DATA-PIPE-003, Issue #46 | Role/PIT isolation, clean replay, identical-input rebuild, and guarded Base descriptor |
| META-DATA-DWD-002 | Implement the product-bounded seven-domain DWD/DWS v2 scripts under Issue #55 | META-DATA-BASE-001, ADR 0006 | Script-level tests, data evaluation, reconciliation, deterministic packages, and three-node evidence; publication remains gated by Issue #69 |
| META-DATA-MAINT-001 | Reconcile post-V2.1 Daily ODS/DWD operational hardening | META-DATA-DWD-002 accepted, ADR 0007 | Current-main reconciliation, focused recovery/sync/schedule tests, and Issue #143 acceptance without changing Base or P4 authority |
| META-OPS-RELIABILITY-001 | Deliver bounded ODS/DWD reliability improvements | META-DATA-DWD-002 accepted | #424; reviewed plan, staged engineering/Linux/deployment evidence, historical gap disposition and ten-session observation |
| META-OPS-PROCESS-001 | Reap owned daily process groups before retry | META-OPS-RELIABILITY-001 | #425; real shell pipeline faults, bounded cleanup, no late writes, exit-75 retry suppression; separate Linux/deployment gate |
| META-OPS-MINUTE-001 | Preserve minute collector terminal checkpoint evidence | META-OPS-RELIABILITY-001 | #427; synthetic circuit, cancellation and disk faults; complete processed-symbol checkpoint before terminal status; Linux/deployment remains separate |
| META-OPS-MINUTE-002 | Bound opt-in same-attempt minute residual retry | META-OPS-MINUTE-001 | #429; 4992-symbol residual-set, one monotonic deadline, fail-closed profile activation; Linux/deployment remains separate |
| META-OPS-MINUTE-005 | Close the entire 1m acquisition and qualification chain | META-OPS-RELIABILITY-001 | #456; [Staged plan](../../work-items/minute-chain-reliability.md); qualification, semantics, residual queue, dated publication and ten-session observation remain separately gated |
| META-OPS-MINUTE-006 | Preserve qualified whole-security semantics during materialization | META-OPS-MINUTE-005 | #455 / PR #457; `tests/unit/test_minute_materialization.py`; fresh attempt, exact staged readback, storage-fault stop and no ghost file; isolated Linux smoke passed, runtime/rollout open |
| META-OPS-ENV-001 | Qualify effective collector runtimes and reproducible test profiles | META-OPS-RELIABILITY-001 | #431; actual interpreter inventory, role/profile locks, read-only deployment preflight, isolated environments and controlled rollback; deployment blocked |
| P1-DATA-002 | Restore incomplete none histories detected by S3 replay | S3 coverage audit | Exact-key source backfill and reconciled child revision |
| P1-DATA-003 | Restore truncated active none histories detected by daily coverage | P1-DATA-002 | Exact-key backfill, active-universe coverage, and OCI child revision |
| P1-DATA-004 | Normalize mixed-unit corporate actions for continuous historical replay | C1, S4, P1-SNAPSHOT-001 | Deterministic authority audit, fully versioned per-security exceptions, and immutable replay input bundle |

The S1 reconciliation report measures each in-scope delisted security over the supported daily
history window. It distinguishes observed bars, known suspension sessions, exact versioned
terminal-boundary exceptions visible at the requested knowledge cutoff, unexplained missing
sessions, and invalid observed bars. Terminal-boundary rows must be evidenced exact delist-date
keys; they are never a blanket delisting exemption. No report may present a suspension-only
history as a missing history. The report and the frozen universe-policy version are required
acceptance evidence.

## 9. Acceptance

### P1 historical baseline

- **D1**: 10 historical dates by 10 securities replay against independent references.
- **D2**: 20 known limit or suspension dates reconcile exactly.
- **D3**: returns around selected ex-right dates contain no artificial jumps.
- C1, C2, and S1 are closed with stable evidence paths.
- Repeating a build from identical manifests produces identical canonical checksums.

The evidence bundle is bound to one published snapshot. It records passed C2/S1 evidence and either
a full C1 pass or the explicit versioned C1 conditional-acceptance policy,
the full 10-by-10 D1 date/security matrix with independent reference ids, at least 20 unique D2
limit or suspension cases, and non-empty D3 ex-right cases with no artificial jump. Passing the
bundle validator is necessary but does not itself change the release gate configuration.

OCI rebuilds this bundle with `quanthus build-p1-evidence`. The command verifies each snapshot
partition checksum before it replays D1 from the Baostock reference pack, D2 from the immutable
coverage matrix and PIT suspension records, and D3 from linked factor rows plus adjacent
none/qfq returns. A mismatched report checksum, missing case, or changed replay result fails the
build. Conservative factor-visibility exceptions remain explicit C1 `open` evidence; the command
cannot silently turn them into a passed blocker.

The gate file and evidence bundle now record this conditional acceptance, so P2 implementation is
open. A future gate regression or invalidated evidence bundle blocks further P2 promotion; prose
in this document cannot keep the gate open by itself.

### `META-DATA-PIPE-003` target delivery

The repository contract covers immutable epochs, daily bars/status/summary candidates, fixed-
primary selection and fail-closed divergence, catalog and release-set construction, sealed
historical DWD/DWS bases with lane-local logical catalogs, limited
operational ADS, pre-CAS publication intent, fenced atomic `CURRENT`, idempotent retry, correction,
revocation, rollback, accepted-ODS recovery evidence, and offline strategy-replay research bundles.
Unit coverage is implementation evidence only. Formal three-node shadow deployment, historical
research-bundle bootstrap, role isolation, and development replay equivalence are accepted in
`docs/acceptance/issue-45-portable-ads-20260730.md`; shadow qualification, catalog-native
publication, full target delivery, and production cutover remain open.

The additive 2026 recent-research snapshot and offline research-bundle publication are accepted separately in
`docs/acceptance/issue-45-2026-research-ads-backfill-20260730.md`. This evidence extends the
historical research input through the 2026-07-27 execution boundary without changing the original
three scope bundles or opening production cutover.

Production cutover remains blocked until all of the following have stable evidence:

- both lanes build portable candidate families from the same epoch parent/reference/runtime inputs;
- independent-gate, fixed-primary, fallback, both-failed, unresolved-divergence, clock/calendar
  drift, late/duplicate, and no-mixing fixtures pass;
- copied candidates and ADS verify without original host paths or network/database access;
- crash/concurrency injection exposes only an old or new complete release set and proves legacy and
  target writers cannot overlap;
- historical and operational PIT time rules, correction/successor/revocation, and rollback pass;
- development bundles contain no arbitration/blind artifacts and reproduce a bound replay result;
- retained canonical inputs rebuild ADS without live lanes, and exact accepted ODS evidence survives
  one producer-node loss; and
- `GO_FOR_CUTOVER` is explicit, target roots remain separate, and every legacy artifact is retained.

No success in this subsection authorizes deletion. Retention cleanup requires a separate design and
reviewed execution decision.
