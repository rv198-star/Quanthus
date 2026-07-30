# ADR 0005: Dual-Lane Canonical and ADS Publication

- Status: Accepted
- Date: 2026-07-29
- Decision id: `META-DATA-PIPE-003`
- Design status: approved
- Delivery gate: open for contract and shadow implementation; production cutover blocked
- Tracking issue: GitHub issue #45
- Partially supersedes: ADR 0002 runtime ownership and daily derived-build placement; ADR 0003
  winner selection, OCI-only daily transformation, and per-dataset consumer publication; ADR 0004
  prohibition on lane-local DWD/DWS candidate builds

## Context

The current production path selects one ODS release, copies the complete release to OCI, builds
DWD/DWS there, and advances separate dataset pointers. Both CN01 and Hermes already acquire and
retain immutable ODS evidence. The resulting normalized daily artifacts are much smaller than the
full ODS handoff, while research and later training need compact, reproducible inputs that can move
to dedicated compute without access to production nodes.

Moving transformation to both acquisition nodes creates useful redundancy only if it preserves one
canonical authority. Arrival order, favorable content, or a row-level merge cannot decide truth.
The design also needs one atomic consumer publication boundary: a valid DWD pointer beside a stale
DWS or ADS pointer is not a complete release.

This decision accepts the target architecture. The repository now implements the bounded fencing,
correction, revocation, rollback, accepted-ODS recovery-evidence, and portable `strategy_replay`
contracts described below; this does not claim that the exact commit is deployed or qualified. The
legacy OCI pull/build publisher remains the operating authority until the shadow, runtime fencing,
recoverability, and cutover gates below pass and an explicit `GO_FOR_CUTOVER` is recorded.

The implemented contract slice remains narrower than the full target. It produces DWD daily bars,
DWD daily status observations, and one DWS daily market summary per lane; OCI can arbitrate those
candidates and build a research-only operational ADS revision containing `market_daily`,
`market_status`, and `market_summary`. It also provides contract-trusted clock windows, fenced
lease/CAS publication, correction/revocation/rollback state, accepted-ODS recovery evidence, and a
self-contained role-scoped portable `strategy_replay` bundle. The migration contract additionally
packages the accepted historical DWD seed with a newly derived historical DWS projection, seals
one byte-identical copy on each candidate node, and builds a lane-local logical history catalog
over that common seed plus the lane's daily candidate chain. Full PIT/tradability, real NTP
qualification, independent restore evidence, and portable `event_library` or `ml_training`
bundles remain open delivery gates. Contract tests are not production or cutover evidence.

## Decision

### 1. Runtime authority

| Runtime | Owns | Must not do |
| --- | --- | --- |
| CN01 | Fixed-primary ODS acquisition and complete immutable DWD/DWS candidate production | Publish canonical state, ADS, PostgreSQL projections, or consumer pointers |
| Hermes | Continuously exercised standby ODS acquisition and complete immutable DWD/DWS candidate production | Claim independent market truth or publish canonical state, ADS, PostgreSQL projections, or consumer pointers |
| OCI | Epoch publication, arbitration, canonical acceptance/catalogs, ADS builds, release sets, consumer publication, and PostgreSQL projections | Select favorable output, merge lanes, or publish from an unaccepted candidate |
| Research node | Read one authorized immutable research bundle and write result artifacts bound to it | Query live source lanes, mutate ADS, publish canonical data, or access an unopened scope |

Hermes is an independently executed standby and cross-lane consistency source. When both lanes use
the same upstream provider, agreement is not independent proof of market truth.

### 2. Arbitration epoch and candidate family

OCI opens one versioned arbitration epoch for a market date before lane builds begin. The immutable
epoch input freezes:

- the expected canonical parent or historical base and its manifest digest;
- the exact shared reference set, including security identity/code validity, universe and calendar
  policy, lifecycle, ST, adjustment, tradability, and other required PIT/reference inputs;
- transform, schema, quality, arbitration, clock, and schedule policy versions; and
- the eligible lane/attempt and cutoff rules.

The epoch is copied to both lanes and verified before use. A node cannot mint, infer, or remap
`security_id`. A candidate built against a different parent/base or shared-reference digest is
ineligible and is not compared as equivalent work.

Each lane builds one complete candidate family from its own ODS evidence. DWD, its DWS projection,
and every lane-bound PIT/status/tradability artifact are selected as one family. A family writes
`READY` last and declares its lane, attempt, ODS release, epoch, references, transform/runtime,
quality results, source cutoffs, and artifact hashes. DWS remains a reproducible projection of its
exact DWD manifest, not a second normalized truth.

Candidate and derived manifests contain logical ids, digests, and safe bundle-relative paths only.
Host-local absolute paths, external links, credentials, and producer database references are
forbidden. A copied artifact must verify after its producer path is unavailable.

Historical completion is a separate migration action, not a fabricated lane candidate. OCI wraps
the exact accepted P1 DWD revision as one immutable history-base bundle, derives the versioned DWS
history from its bars and observed daily status keys, and sends the bundle through the write-only
ingress to both nodes. Each node verifies every source checksum, copies the bundle into a root-owned
sealed base root, and joins it to its post-base candidates only through an immutable lane-history
catalog. The common seed is recovery and continuity evidence; it is not independent CN01/Hermes
market evidence. No process appends daily rows to the seed or relabels a legacy artifact as a lane
candidate.

### 3. Gate first, compare second

Every candidate passes its own integrity, identity, PIT, coverage, schema, clock, and business
quality gates before cross-lane comparison. A failed candidate cannot donate rows, artifacts, or
gate results to the other lane.

OCI then applies the frozen arbitration policy:

1. Accept the eligible CN01 candidate when it is ready by cutoff and passes every independent hard
   gate, regardless of Hermes arrival order or favorable content.
2. Accept Hermes only when CN01 is missing, late, unverifiable, or fails a predeclared independent
   lane-local or external-authority hard gate, and Hermes independently passes all gates.
3. If both independently pass but have an unresolved material difference, publish neither and
   record `withheld_unattributed_divergence`. Cross-lane difference alone cannot identify the bad
   lane.
4. If both fail, publish neither. Persist exact attempt, gate, and reason ids and alert.
5. Parent/reference, transform/runtime, clock, or open/closed-session disagreement blocks
   comparison and publication rather than being resolved by primary order.

Attempt selection uses persisted epoch metadata, not directory arrival order. The arbiter uses a
fenced lease and compare-and-swap so concurrent retries accept at most one generation for an
epoch. A correction uses a new correction decision; it does not reopen a completed automatic
epoch.

### 4. Time and identity

Manifests preserve each source input's original timezone-aware `knowledge_cutoff`. They separately
record candidate readiness, OCI acceptance, release publication, and other operational timestamps.
Copying, rebuilding, accepting, or publishing an artifact never replaces its source cutoff.

Historical research binds every decision to its historical snapshot and decision cutoff; a later
bundle build time does not become historical knowledge. A forward or operational decision must
also bind a release set resolved from `release-set_CURRENT.json` whose bound `published_at` is no
later than the decision time; an uncommitted publication intent and its timestamp are insufficient.

The contracts distinguish:

- `content_digest`: deterministic data/schema/upstream/policy identity, excluding volatile run
  timestamps;
- `manifest_digest`: the exact immutable revision envelope and its file metadata; and
- `idempotency_key`: the epoch, exact inputs, and frozen policy used to recognize a retry.

Results bind an exact revision id and manifest digest. Rebuild comparisons use the content digest;
a logically similar artifact is never silently substituted for the revision named by a result.

### 5. Canonical catalog and atomic release

Publication is a one-way immutable dependency graph:

```text
candidate family
  -> canonical acceptance record
  -> canonical catalog snapshot
  -> operational ADS revision
  -> release-set manifest
  -> immutable publication intent
  -> release-set CURRENT
```

The canonical acceptance record is OCI's immutable decision evidence for one market-date
generation. It binds the selected lane/ODS, epoch, parent/base, references, DWD/DWS/PIT manifests,
gates, reason, digests, and `accepted_at`. It contains no forward reference to an ADS or release
set. If a later ADS build fails, the record remains internal evidence but creates no consumer
release.

A canonical catalog snapshot maps each included market date to exactly one accepted generation.
Catalog snapshots are created, not independently promoted. An operational ADS revision binds one
exact catalog snapshot and the exact generation subset it read. A release-set manifest binds that
catalog snapshot, the release profile's required canonical inputs and operational ADS outputs.

`release-set_CURRENT.json` is the only consumer-visible publication pointer. OCI seals every
required artifact and writes release-set `READY`, then seals an immutable
`release_set_publication_intent` containing the prior/new pointer identity and new manifest digest
before one atomic compare-and-swap. The intent's `published_at` field is the intent-creation
timestamp; it is not proof that the pointer advanced. `release-set_CURRENT.json` is the
consumer-visible commit point and binds the intent path and digest. An intent may therefore exist
without a published release after a crash, and an idempotent retry validates and reuses it before
advancing `CURRENT`. Earlier immutable records are not edited. Dataset-specific pointers retained
for compatibility are projections of the current release set and cannot be joined independently
or authorize a consumer. A distinct post-commit success record may be added only by a future
contract revision; the current implementation does not emit one.

The legacy and target publishers share one fenced single-writer lease. Cutover quiesces and fences
the legacy publisher before target publication is enabled. Rollback reverses the fencing sequence
and selects an older complete, non-revoked release set; it never deletes evidence.

### 6. Corrections and descendants

A late primary after a Hermes fallback is comparison evidence:

- business-equivalent output closes as `late_equivalent` without republication;
- material output creates `correction_proposed` and changes no pointer; and
- approval creates a new immutable generation with `supersedes`, policy and authority evidence,
  diff digest, and an impact report.

A corrected historical date cannot be placed beneath successors that still depend on the old
generation. A new catalog snapshot is eligible for a release set only after publication-critical
descendants are rebuilt or explicitly revalidated. Publication-critical descendants are the
DWS/PIT, operational ADS, and serving projections required by the active release profile.

Frozen research bundles and their bound results are retained research descendants. They remain
immutable under their original digests and receive an impact, supersession, or revocation record;
they do not block an operational release merely because a newer bundle could be built. A revoked
integrity-invalid generation remains reproducible evidence but cannot appear in a newly published
or rollback-selected release set.

### 7. ADS lifecycles

ADS has two separate immutable publication lifecycles:

1. Operational ADS is built on OCI from an exact catalog snapshot and declared generation subset.
   It is a mandatory member of the applicable atomic release profile.
2. A portable research bundle is a frozen, self-contained snapshot assembled from an exact catalog
   snapshot for `strategy_replay`, `event_library`, or `ml_training`. It has no `CURRENT` pointer
   and resolves no mutable pointer after construction.

Adding a date or correcting an upstream generation creates a new revision; no published ADS is
appended to or rewritten. Research results remain bound to the original bundle revision.

The initial research-bundle layout does not partition directories by calendar year. Date is a
typed field and sort key. The manifest exposes semantic tables through `tables[].parts[]`; each
part declares a safe relative path, role, hash, byte and row counts, schema hash, date bounds, and
sort keys. Consumers use only the manifest and do not glob paths or infer semantics from filenames,
part boundaries, or row groups. Later size-based sharding requires measured transfer, scan, memory,
or parallelism evidence and creates a new immutable layout revision.

Decision-visible, runner-only execution, and label-only outcome surfaces remain role-separated.
Development, arbitration, permanent-blind, and forward-simulation data are issued as physically
separate authorized bundles. P4 and P5 own event, label, feature, target, split, and model semantics;
this ADR owns their publication envelope, integrity, lineage, portability, and authorization
boundary.

### 8. Retention and migration safety

Implementation under this decision is copy-only. Existing ODS, legacy OCI mirrors, DWD/DWS,
canonical artifacts, ADS, logs, and evidence must not be deleted, moved destructively, or reused as
staging. The target is built beside the legacy path. This ADR does not authorize retention cleanup;
any future deletion requires a separate reviewed decision, exact inventory, reference analysis,
expired rollback/correction horizons, and proven recovery.

Before cutover, the retention contract must identify owner, minimum horizon, purge prohibition,
failure-independent copy count/location class, RPO/RTO, and restore evidence for every artifact
class. The exact accepted ODS rebuild basis must survive loss of its producer node; hashes and the
other lane's different ODS are not recovery copies. This does not require OCI to keep a routine ODS
working mirror after a separately authorized future cleanup.

The accepted historical DWD seed must exist as complete sealed data, not only as an epoch metadata
descriptor, on both CN01 and Hermes before any OCI DWD cleanup can be proposed. Its DWS history is
a reproducible projection governed by `dual-lane-history-base-v1`; the lane-local logical catalog
references the seed and immutable daily candidates without physically merging or rewriting either.
That catalog is a recovery/read boundary and never competes with OCI's canonical catalog or
consumer release-set authority.

Recovery is proved in layers: retained canonical inputs rebuild ADS without live lanes; retained
DWD rebuilds DWS; and exact ODS, epoch parent/base, references, runtime, and transform rebuild the
same DWD content digest on a clean path.

## Transition and delivery gates

1. Freeze and test epoch, candidate, arbitration, catalog, ADS, release-set, correction, fencing,
   alert, and retention contracts.
2. Seal the governed DWD/DWS historical base on both nodes and verify each lane-local logical
   history catalog through the latest candidate.
3. Build candidates on both lanes in shadow without canonical or consumer publication authority.
4. Run frozen multi-session comparisons and rehearse primary/standby failure, material divergence,
   clock/reference drift, concurrent arbitration, crash recovery, correction, and resource limits.
5. Build target release sets in shadow and verify copied research bundles on clean CPU/GPU readers.
6. Record `GO_FOR_CUTOVER`, fence the legacy writer, and atomically enable the target publisher only
   after the accepted evidence and rollback rehearsal pass.

Until step 6, ADR 0003's OCI pull/build path remains the live operating procedure. Target artifacts
must use separate roots and pointers and cannot alter live `CURRENT` values.

## Consequences

The design moves repeatable daily normalization close to acquisition while keeping one OCI
canonical authority. It reduces the eventual daily transfer and working-storage footprint,
continuously exercises the standby, and gives replay/training a compact portable boundary. The
single release-set pointer removes cross-dataset partial publication from consumer behavior.

The cost is a larger contract and operational surface: epoch distribution, equivalent runtime and
reference inputs, two complete candidate builds, directional failover rules, catalogs, release-set
fencing, retention copies, and correction impact analysis all require explicit evidence. Cross-lane
agreement still cannot detect a common-provider error.

## Acceptance boundary

Implementation and cutover require, at minimum:

- same-epoch candidates with identical parent/reference/runtime contracts and no mixed-lane
  artifacts;
- independent gates before comparison and fixed-primary/fail-closed arbitration fixtures;
- portable relative-path verification after producer paths are unavailable;
- atomic old-or-new release-set crash tests and legacy/target writer fencing;
- historical and forward PIT cutoff tests;
- late-equivalent, correction, successor, revocation, and rollback tests;
- self-contained authorized research bundles with exact result binding and no sealed-scope files;
- clean-node DWD/DWS/ADS recovery drills and failure-independent accepted ODS evidence; and
- explicit `GO_FOR_CUTOVER` with all legacy data retained.
