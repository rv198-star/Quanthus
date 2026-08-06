# ADR 0008: DWD Historical Knowledge Remediation

- Status: Proposed; successor identity/range boundary accepted, source semantics pending
- Date: 2026-08-06
- Decision id: `META-DATA-PIT-001`
- Design status: partial decision recorded; final remediation not approved
- Delivery gate: blocked pending Hermes ODS mirror confirmation, physical DWD audits, and availability-contract approval
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
| `ambiguous_source_insufficient` | 4 | Financial source families and holder-change rows mix defensible publication evidence with acquisition/business-date fallbacks; one universal rewrite is not justified |
| `source_derived_historical` | 5 | The table already uses a source publication or source knowledge timestamp, although corrected parent identities may still require a rebuild |
| `forward_operational` | 7 | The object is correctly knowable only from its Base acquisition/detection point and must not be projected backward |

The machine matrix is `config/audits/dwd-pit-semantics.v1.json`. CN01's exact ODS V2 package has now
passed deep verification and source-family scans. The physical DWD bytes on Hermes, CN01, and OCI,
and checksum equality of the Hermes ODS mirror, still require completion.

## Recorded Decisions And Remaining Proposal

The following boundaries are accepted for the remediation design:

- the successor is a new immutable package named `dwd-base-202607-v2.1`;
- its data cutoff remains exactly 2026-07-31, matching V2;
- no 2026-08-01-or-later Daily data is folded into the V2.1 Base; and
- post-Base Daily data is cleaned, rebuilt against corrected identities and PIT rules, and
  published through a separate migration/replay decision.

Table/source availability rules, additive ODS evidence package identities, and the final V2.1
semantic digest remain pending approval.

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
- valuation has no historical publication/acquisition field and still requires provider-semantic
  evidence or fail-closed exclusion; benchmark definitions likewise require a static/versioned
  reference contract or additive evidence;
- ST has 1,492 proven event rows and 5,538 explicitly not-proven rows. The latter remain
  fail-closed. Suspensions, price-limit rules, announcements, and shareholder counts retain direct
  source knowledge/publication evidence;
- all 46,649 adjustment factors have cutoffs, including the retained 4,173 fail-closed visibility
  exceptions;
- all 40,594 corporate-action rows and all 54,509 dividend rows have announcement dates, so the
  domain is reconstructable under a conservative end-of-announcement-date rule rather than the
  current 2026 `built_at` value;
- all 852,497 decoded full-statement report versions have valid `publish_date` and `update_time`.
  Eighty-six versions are dated 2026-08-01 and are excluded from V2.1 into Daily cleaning. Legacy
  canonical-quarterly and `fin_abstract` sources still have respectively 10,091 and 19,840
  coordinates without matched actual-publication evidence;
- 69,648 of 194,322 holder-change rows carry `disclosure_date`; the remaining 124,674 require an
  additive mapping or fail closed; and
- industry and F10 snapshots remain forward-only from their 2026-07-31 acquisition time, as do
  observation-gap and quality detections.

Hermes still needs checksum-identical ODS verification, but the CN01 package is sufficient to freeze
these source-content dispositions. Each availability rule remains separately approval-gated.

Each accepted availability rule is versioned by table/source family and included in the successor
semantic digest.

### 4. Rebuild dependent increments and consumers explicitly

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

### 5. Fail closed by table and source family

A row without independently defensible historical availability is excluded with an explicit reason
code. No package-wide constant, observation-date shortcut, or current-identity join may fill a
missing knowledge timestamp. Derived rows use the maximum knowledge time of all accepted inputs and
any required observation-completion bound.

## Required Acceptance Evidence

This proposal may become accepted only after all of the following are attached to Issue #78:

- Hermes, CN01, and OCI installed package roots pass READY, manifest, file-size, file-SHA, package
  identity, and cross-mirror equality checks;
- all 33 tables have actual row counts, representative schemas, min/max knowledge and business
  coordinates, 2012-2021 and 2026 distributions, and representative future-knowledge samples;
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

- the physical three-node DWD audit and Hermes ODS mirror confirmation still require approved read-only access;
- some history may remain unavailable when source publication evidence cannot be recovered;
- post-Base Daily data requires a separate clean/rebuild/rebase migration and cannot be absorbed into
  the V2.1 Base; and
- P4 Stage B remains blocked until its source package and exact availability contracts are
  reapproved.
