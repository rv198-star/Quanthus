# ADR 0008: DWD Historical Knowledge Remediation

- Status: Proposed; CN01 machine audit complete with 31 classified findings; exact remediation and builder authorization pending
- Date: 2026-08-06
- Decision id: `META-DATA-PIT-001`
- Design status: partial decision recorded; final remediation not approved
- Delivery gate: blocked pending exact ledger/remediation approval, execution-readiness closure, builder implementation, package acceptance, and cutover approval
- Affects: ADR 0006/0007 DWD Base V2 historical PIT authority; does not supersede their current operational topology

## Context

`dwd-base-202607-v2` is the accepted immutable 33-table warehouse baseline through 2026-07-31.
P4's approved candidate contract requires every consumed row to have `knowledge_from <= T close`.
The Stage B implementation found that several historical DWD objects instead carry the July/August
2026 Base-build cutoff.

Issue #78 opened a package-wide investigation rather than permitting a P4-specific timestamp
substitution. The code audit classifies the 33 tables as follows:

| Code disposition | Tables | Meaning |
| --- | ---: | --- |
| `build_timestamp_substituted` | 17 | The builder demonstrably writes a Base build/acquisition timestamp where historical source or derived availability is required |
| `ambiguous_source_insufficient` | 3 | Disclosure timestamps mix precise and placeholder times, while holder-change rows mix disclosure evidence with an unsafe business-date fallback |
| `source_derived_historical` | 3 | Adjustment factors/inputs and shareholder counts already use defensible source knowledge timestamps |
| `forward_operational` | 10 | Seven original operational/governance objects plus all three financial tables are knowable only from immutable acquisition/detection without a revision archive |

The machine matrix is `config/audits/dwd-pit-semantics.v1.json`. CN01's exact ODS V2 package passed
deep verification and source-family scans. CN01 then completed the 33-table, 13-dimension DWD
machine audit with 31 classified findings and no critical finding. Hermes and OCI are replica
targets rather than semantic-audit authorities; successor replicas require checksum verification
only if they are deployed after publication. The exact closure packet is retained at
`docs/acceptance/issue-78-dwd-base-v2-pre-rebuild-audit-closure-20260807/`.

## Recorded Decisions And Remaining Proposal

The following boundaries are accepted for the remediation design:

- the successor is a new immutable package named `dwd-base-202607-v2.1`;
- its data cutoff remains exactly 2026-07-31, matching V2;
- no 2026-08-01-or-later Daily data is folded into the V2.1 Base; and
- post-Base Daily data is cleaned, rebuilt against corrected identities and PIT rules, and
  published through a separate migration/replay decision;
- the complete V2.1 reconstruction runs only on CN01; and
- `/personal` NFS is mandatory storage for the full pre-rebuild audit, plans, candidates, spill,
  checkpoints, July-tail cleaning, complete-package assembly, and acceptance evidence; and
- no V2.1 domain builder or candidate assembly may start until the 33-table, multi-dimensional
  pre-rebuild audit closes and its complete findings/remediation digests are approved.

The audit expansion is machine-readable in
`config/audits/dwd-base-v2-pre-rebuild-audit.v1.json`. The exact findings ledger and one-time
remediation scope are frozen in the closure packet but remain pending explicit digest approval.
Required additive evidence, builder implementation, the final V2.1 semantic digest, package
acceptance, and cutover remain subsequent gates.

### 1. Keep V2 immutable and historically blocked

`dwd-base-202607-v2` remains retained exactly as sealed. No process may rewrite its Parquet files,
manifest, READY marker, content digest, or installed mirrors. No reader may silently replace
`knowledge_from` with effective, observation, announcement, publication, or build time.

V2 may continue to support explicitly operational current-date continuity where its existing
contract is sufficient, but it is not a historical PIT authority for P4 or another research
consumer until the investigation closes. Every increment derived from V2 remains lineage-bound to
V2 and does not inherit authority from a later fix.

### 2. Use a successor immutable DWD Base, not an in-place repair

The successor package identity is `dwd-base-202607-v2.1`. It is a new immutable package, not a
revision of bytes under the V2 directory. It must be built from exact immutable inputs, retain the
same through-date of 2026-07-31, and receive a new manifest SHA-256 and content digest. The successor
must cover all 33 tables so identity, foreign keys, row keys, lineage, and knowledge intervals are
validated as one graph.

This does not require changing every business value. It requires re-materializing or explicitly
carrying every table under the successor manifest after its parent identities and knowledge
semantics have been validated. Reusing V2 bytes is allowed only when a table has no changed row,
foreign key, lineage, schema, or semantic contract and the successor acceptance evidence proves
that equivalence.

### 3. Preserve the ODS Base and add evidence immutably

`ods-base-202607-v2` remains immutable. The successor build may read that exact package plus
separately sealed additive evidence packages. It must not edit ODS rows or claim that business time
alone proves historical availability.

The source-family audit is frozen in `config/audits/ods-pit-sufficiency.v1.json`. On CN01, the exact
`ods-base-202607-v2` package passed READY/manifest/file deep verification: 112 datasets, 21,396
files, 13,643,600,642 bytes, manifest
`sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b`, and content digest
`sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01`.
The exact-source findings are:

- stable security/listing/code joins are complete: all 5,329 historical bar codes and all 5,316
  valuation codes resolve to the 5,868-row master or the eight explicit code-validity intervals;
  historical display names and other current-snapshot attributes still require an explicit
  contract or remain unavailable;
- bars and index bars require versioned observation-completion rules; realized calendar rows may
  use a conservative session/date-end rule without granting earlier future-schedule authority;
- valuation has no historical publication/acquisition field. Full-history receipts cover only 209
  securities and were collected after the Base cutoff; 4,992-security receipts cover only the
  2026-07-20 through 2026-07-30 repair window. Canonical historical valuation therefore remains
  forward-only from independently evidenced acquisition. Benchmark definitions likewise require a
  static/versioned reference contract or additive evidence;
- ST has 1,492 proven event rows and 5,538 explicitly not-proven rows. The latter remain
  fail-closed. Suspension rows retain exact cutoffs, but canonical coverage starts in 2016 and the
  12,145-row supplementary status ledger covers only 347 codes from late 2018 onward. Price-limit
  rules and shareholder counts retain direct source knowledge evidence;
- all 46,649 adjustment factors have cutoffs, including the retained 4,173 fail-closed visibility
  exceptions;
- 6,395,198 announcements all have `publish_time`, but 5,413,404 are exactly midnight and 205,275
  are exactly 06:30:00. The field therefore mixes precise timestamps with date-level/normalized
  placeholders. V2.1 may use exact time only where precision is proven; otherwise availability is
  conservative announcement-day end or the later immutable artifact cutoff;
- all 40,594 corporate-action rows and all 54,509 dividend rows have announcement dates, so the
  domain is reconstructable under a conservative end-of-announcement-date rule rather than the
  current 2026 `built_at` value;
- the full-statement snapshot decodes to 852,497 reports and all carry `publish_date`, but the
  ten-digit provider `update_time` is shared across many reports and falls after publication for
  roughly half the rows and before it for the remainder. The package does not preserve report-level
  value revisions, so current reports/concepts/facts are forward-only from acquisition rather than
  historically available at original publication. Eighty-six report versions dated 2026-08-01 are
  excluded from V2.1. In 2012-2021, 4,879 canonical quarterly coordinates and 4,976 abstract rows
  additionally lack a match to the publish-date source;
- 69,648 of 194,322 holder-change rows carry `disclosure_date`; the remaining 124,674 require an
  additive mapping or fail closed; and
- industry and F10 snapshots remain forward-only from their 2026-07-31 acquisition time, as do
  observation-gap and quality detections.

The July tail inside ODS V2 also reconciles exactly to its manifest: 24,960 daily bars, 24,960 daily
status rows, 15 index rows, 4,992 valuation rows, 31,190 announcements, 21 suspensions, 175 factor
rows, 358 dividend rows, 7,179,120 one-minute bars, and 1,435,824 five-minute bars. Some July 27-28
facts were not acquired until July 29-30, so V2.1 must preserve each artifact cutoff. Five
corporate-action and five financial-disclosure deltas, plus one factor delta, are verified zero-row,
zero-column Parquet parts and must be handled as valid empty increments rather than schema failures.

Hermes still needs checksum-identical ODS verification, but the CN01 package is sufficient to freeze
these source-content dispositions. Each availability rule remains separately approval-gated.

Each accepted availability rule is versioned by table/source family and included in the successor
semantic digest.

### 4. Complete a full DWD Base V2 audit before any V2.1 build

The already confirmed PIT defect is not treated as the only possible Base defect. A second expensive
Base reconstruction after V2.1 would repeat CN01/NFS build, package verification, multi-node mirror,
consumer migration, and rollback work. Issue #78 therefore owns one full pre-rebuild audit and one
complete remediation freeze before the first V2.1 builder starts.

The audit covers all 33 tables and every physical partition across these dimensions:

- package integrity and Hermes/CN01/OCI mirror equality;
- schema, nullable/type, timestamp-zone, decimal, enum, and partition consistency;
- row-level and field-level PIT availability;
- security/company/listing identity and lifecycle;
- time-aware referential integrity;
- business keys, row keys, duplicate coordinates, and immutable versions;
- temporal interval overlap, inversion, gaps, and containment;
- full-history, 2012-2021, 2026, and July-tail completeness;
- numeric and domain invariants;
- authorized immutable lineage only;
- deterministic replay of derived builders on representative real partitions;
- Daily/weekly/consumer compatibility; and
- CN01 rebuild, publication, cutover, and rollback readiness.

Every finding receives a stable id, severity, affected row/partition count, root cause, one approved
disposition, required code/contract change, regression tests, and a V2.1 acceptance check. Valid
dispositions are repair in V2.1, retain under an approved contract, forward-only, fail-closed,
add immutable evidence then repair, or no-change-proven. Unknown or deferred disposition is forbidden
when the remediation scope is frozen; no critical finding may remain open.

The required outputs include the mirror/package inventory, table/partition profiles, field
availability matrix, identity and referential audits, key/version ledger, temporal and coverage
reports, numeric/lineage/replay audits, downstream impact map, complete findings ledger,
`v2.1-remediation-scope.json`, and `v2.1-build-contract.json`. Every JSON output is canonically
digested. Only explicit approval of those digests opens the V2.1 builder gate.

The operating procedure is
`docs/operations/dwd-base-v2-pre-rebuild-audit.md`. Child Issues may implement bounded audit or fix
work, but Issue #78 remains open through complete V2.1 package acceptance and successor mirror/
consumer cutover approval.

### 5. Execute the rebuild only on CN01 with mandatory NFS build storage

The V2.1 reconstruction has one execution authority: CN01. Hermes and OCI may verify or mirror the
accepted package later, but neither may build it. Repository code reaches CN01 only through the
normal GitHub `pull --ff-only` path.

CN01's local filesystem had 37,807,775,744 free bytes during the topology audit. The installed V2
package occupies 14,913,159,818 bytes, while the prior Base planner estimated 100,290,327,430 bytes
for final outputs, largest stage/spill, and margin. Local disk is therefore not a valid candidate or
spill root. The mounted `/personal` NFS had more than 11 PB reported free and passed controlled
write, atomic rename, same-mount hard-link, and CN01-local `flock` probes. The probe directory was
removed after verification.

The accepted path contract is:

| Role | Path | Filesystem |
| --- | --- | --- |
| Immutable ODS input | `/srv/quanthus/ods/base/ods-base-202607-v2` | CN01 local |
| Immutable DWD V2 comparison input | `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2` | CN01 local |
| Local coordination root | `/srv/quanthus/dwd-dws-v2/v2.1-rebuild` | CN01 local |
| Single-writer lock | `/srv/quanthus/dwd-dws-v2/v2.1-rebuild/locks/rebuild.lock` | CN01 local |
| NFS workspace | `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1` | NFS |
| Hidden local ingress | `/srv/quanthus/dwd-dws-v2/base/.incoming-dwd-base-202607-v2.1` | CN01 local |
| Installed successor | `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1` | CN01 local |
| Active pointer | `/srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json` | CN01 local |

The NFS mount uses NFSv3 with `hard`, `proto=tcp`, `noresvport`, and `nolock`, and is root-squashed.
Therefore CN01 is the only writer, no cross-host lock is trusted, and builders must not depend on
`chown`. If NFS is absent, read-only, remounted with incompatible options, below 120 GiB free, or
fails an atomic-rename/hard-link probe, the rebuild stops. It never falls back to local scratch.

All domain builders run sequentially because CN01 has two CPUs and about 3.9 GB RAM. A partition
pilot must freeze the per-builder memory limit before the full run. DuckDB temp/spill, checkpoints,
domain candidates, July-tail cleaning, package assembly, and evidence all remain on NFS. Lock and
pointer authority remain local.

Because the current domain publisher assembles packages with hard links, all domain candidates and
the complete V2.1 candidate are assembled on the same NFS mount and deeply verified there. NFS is
not an operational Base catalog. Installation is a cross-filesystem, manifest-driven copy into the
hidden local ingress, followed by a second local deep verification. Only then may the ingress be
atomically renamed to the installed V2.1 path. The local free-space gate is:

`free_bytes >= sealed_candidate_bytes + 17,179,869,184`.

No pointer moves during build or installation. After human approval, `BASE_CURRENT.json` advances
with compare-and-swap against V2 manifest
`sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f`.
Rollback restores the V2 pointer without modifying or deleting either immutable package.

The exact topology, observed capacity, probes, paths, forbidden actions, and execution phases are
machine-readable under `cn01_rebuild_execution_topology` in
`config/audits/ods-pit-sufficiency.v1.json`.

### 6. Rebuild dependent increments and consumers explicitly

After successor Base acceptance:

1. keep `dwd-base-202607-v2.1` bounded through 2026-07-31 and exclude every later Daily package from
   its Base payload;
2. clean post-2026-07-31 Daily source artifacts separately, rebuild them against the V2.1 identity
   and availability graph, and publish new immutable increments rather than carrying V2-bound DWD
   rows forward;
3. do not activate weekly DWD corrections until they use the same successor contracts;
4. publish new CN01/Hermes node-local Base identities and a checksum-identical OCI mirror before
   moving any consumer pointer;
5. revise and reapprove the P4 physical-source policy and candidate input contract if table fields,
   availability rules, or package identity change; and
6. leave legacy DWS/ADS/research bundles retained but unauthorized. They cannot provide historical
   knowledge evidence or serve as migration inputs.

Generic DWS remains demand-gated. This remediation does not activate it.

### 7. Fail closed by table and source family

A row without independently defensible historical availability is excluded with an explicit reason
code. No package-wide constant, observation-date shortcut, or current-identity join may fill a
missing knowledge timestamp. Derived rows use the maximum knowledge time of all accepted inputs and
any required observation-completion bound.

## Required Acceptance Evidence

This proposal may become accepted only after all of the following are attached to Issue #78:

- Hermes, CN01, and OCI installed package roots pass READY, manifest, file-size, file-SHA, package
  identity, and cross-mirror equality checks;
- the full pre-rebuild audit completes every dimension in
  `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json`, including partition schema, field-level PIT,
  identity, referential, key/version, temporal, coverage, numeric, lineage, deterministic replay,
  downstream compatibility, and rebuild-readiness checks;
- all 33 tables have actual row counts, complete partition profiles, min/max knowledge and business
  coordinates, 2012-2021 and 2026 distributions, and representative future-knowledge samples;
- the findings ledger contains no unknown disposition or unresolved critical finding, and every
  repair/fail-closed decision has a measured impact, regression test, and V2.1 acceptance check;
- the exact findings, one-time remediation scope, builder contracts, and semantic digests are
  explicitly approved before any V2.1 domain builder starts;
- CN01 ODS sufficiency evidence is retained and the Hermes `ods-base-202607-v2` mirror is confirmed
  checksum-identical to the same manifest/content identity;
- daily, weekly, DWS, ADS, event-library, and other consumer dependencies are enumerated with
  migration dispositions;
- focused tests prove corrected semantics and reject future leakage, missing evidence, timestamp
  substitution, mixed Base identities, and in-place mutation; and
- a human approves the exact successor policy and semantic digests.

The DWD read-only audit command is `ops/bin/audit-dwd-pit-semantics`; its operation is documented in
`docs/operations/dwd-base-pit-audit.md`. The ODS read-only audit command is
`ops/bin/audit-ods-pit-sufficiency`; its operation is documented in
`docs/operations/ods-base-pit-sufficiency-audit.md`.

## Rejected Alternatives

### Rewrite V2 in place

Rejected because V2 is already sealed and distributed to at least three node roles. Rewriting any
copy would destroy its published identity and make cross-node evidence ambiguous.

### Override `knowledge_from` in the P4 reader

Rejected because one universal mapping is false. Bars, identities, calendars, suspensions,
financial filings, industry snapshots, and governance detections have different availability
semantics.

### Use a P4-only projection as the sole fix

Rejected as the package-wide remediation because the defect affects DWD authority and recurring
increment lineage beyond P4. A bounded projection could be proposed separately as a temporary
consumer contract, but it would require its own version, table-specific evidence, approval, and
fail-closed rules and would not repair V2.

### Recover timestamps from legacy ADS or research bundles

Rejected because those artifacts are downstream, retained legacy evidence and are not authoritative
inputs to the current DWD chain.

## Consequences

Positive consequences:

- sealed V2 evidence remains reproducible;
- one successor identity resolves cross-table and cross-node consistency rather than creating a
  consumer-specific fork;
- historical unavailability is explicit instead of being converted into apparent precision; and
- daily maintenance and P4 can resume against one reviewed authority after cutover.

Costs and constraints:

- the full pre-rebuild DWD audit, physical three-node mirror verification, and Hermes ODS mirror confirmation still require approved read-only access;
- some history may remain unavailable when source publication evidence cannot be recovered;
- the build requires continuous CN01 access to the external NFS and a cross-filesystem installation
  copy after NFS verification;
- post-Base Daily data requires a separate clean/rebuild/rebase migration and cannot be absorbed into
  the V2.1 Base; and
- P4 Stage B remains blocked until its source package and exact availability contracts are
  reapproved.
