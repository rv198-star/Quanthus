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
| Product-bounded DWD model | ADR 0006/0007, `META-DATA-DWD-002`, `01-data-foundation/dwd-domain-model.md`; all 33 objects accepted in `dwd-base-202607-v2`; Issue #78 blocks its historical PIT authority and prepares CN01-only NFS-built V2.1, while daily maintenance evidence remains immutable and generic DWS deferred |
| ODS/DWD reference | `01-data-foundation/ods-dwd-data-dictionary.md` and `01-data-foundation/ods-to-dwd-lineage.md` |

## 1. Goal

Produce a complete and replayable view of what the system could know at a historical market
date. No downstream module may read vendor frames directly.

## 2. Scope

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

Issue #78 defines the bounded successor `dwd-base-202607-v2.1`. It is reconstructed only on CN01
from immutable ODS V2 and retained evidence. CN01 local storage owns the single-writer lock, final
hidden ingress, installed Base directory, and `BASE_CURRENT.json`; mandatory `/personal` NFS owns
plans, candidates, spill, checkpoints, July-tail cleaning, complete-package assembly, and build
evidence. NFS is root-squashed and mounted without distributed locking, so no cross-host lock or
production pointer may live there. NFS loss or capacity failure blocks the build rather than
falling back to local scratch. The NFS candidate is deeply verified, copied manifest-by-manifest to
a local hidden ingress, deeply verified again, and installed without changing the pointer. Pointer
cutover and rollback remain separately approved compare-and-swap actions.

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

### 5.4 ADS lifecycle and layout

Operational ADS participates in one atomic OCI release profile. A portable research bundle is a
separate frozen revision assembled from an exact catalog snapshot for `strategy_replay`,
`event_library`, or `ml_training`; it has no `CURRENT` and resolves no mutable pointer after
construction.

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
snapshot path, or any mutable `CURRENT` while running. Normal replay commands use this profile;
the hidden snapshot path is retained only to reproduce frozen P3 evidence.

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

### Implementation conformance map

| Capability | Governing implementation | Contract and evidence |
| --- | --- | --- |
| PIT identity, tradability, and snapshots | `src/quanthus/adjustments.py`, `src/quanthus/tradability.py`, `src/quanthus/snapshot_publish.py`, versioned P1 config | `tests/p1/`, `tests/contract/test_p0_decisions.py`, P1 acceptance files |
| Mixed-unit corporate-action audit and immutable replay revision | `src/quanthus/corporate_action_normalization.py`, `src/quanthus/corporate_action_corpus.py`, `src/quanthus/corporate_action_revision.py`, `src/quanthus/historical_replay_snapshot.py`, `config/p1-corporate-action-normalization.v2.json` | `tests/unit/test_corporate_action_normalization.py`, `tests/unit/test_corporate_action_corpus.py`, `tests/unit/test_corporate_action_revision.py`, `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |
| Immutable ODS handoff and daily DWD/DWS | `src/quanthus/pipelines/`, `config/pipelines/` | `tests/unit/test_ods_handoff.py`, `tests/unit/test_pipeline_runner.py`, `tests/p1/test_daily_warehouse.py` |
| Repository-owned ODS schedule | `src/quanthus/pipelines/lane_runner.py`, `config/schedules/`, `ops/bin/`, `ops/systemd/` | `tests/unit/test_lane_runner.py`, `tests/unit/test_schedule_contracts.py`, ADR 0004 and its runbook |
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
