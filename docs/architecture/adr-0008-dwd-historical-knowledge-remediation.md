# ADR 0008: DWD Historical Knowledge Remediation

- Status: Proposed
- Date: 2026-08-06
- Decision id: `META-DATA-PIT-001`
- Design status: investigation; not approved
- Delivery gate: blocked pending physical Base and mirror audit
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
| `ambiguous_source_insufficient` | 3 | Financial source families mix provider publication evidence, acquisition timestamps, and fallback Base cutoffs; one universal rewrite is not justified |
| `source_derived_historical` | 6 | The table already uses a source publication or source knowledge timestamp, although corrected parent identities may still require a rebuild |
| `forward_operational` | 7 | The object is correctly knowable only from its Base acquisition/detection point and must not be projected backward |

The machine matrix is `config/audits/dwd-pit-semantics.v1.json`. The code classification alone is
not package acceptance evidence. The physical bytes on Hermes, CN01, and OCI still require deep
verification and complete row-distribution scans.

## Proposed Decision

### 1. Keep V2 immutable and historically blocked

`dwd-base-202607-v2` remains retained exactly as sealed. No process may rewrite its Parquet files,
manifest, READY marker, content digest, or installed mirrors. No reader may silently replace
`knowledge_from` with effective, observation, announcement, publication, or build time.

V2 may continue to support explicitly operational current-date continuity where its existing
contract is sufficient, but it is not a historical PIT authority for P4 or another research
consumer until the investigation closes. Every increment derived from V2 remains lineage-bound to
V2 and does not inherit authority from a later fix.

### 2. Use a successor immutable DWD Base, not an in-place repair

The preferred remediation is a new package identity, provisionally DWD Base V3. It must be built
from exact immutable inputs and receive a new manifest SHA-256 and content digest. The successor
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

The table-specific policy is:

- bars, index bars, trading sessions, source ST, price-limit rules, factors, disclosures,
  shareholder counts, and disclosed holder changes can be reconstructed from existing ODS under
  reviewed conservative availability contracts;
- identity, venue, benchmark definition, and some valuation history require additive evidence or
  fail-closed exclusions;
- financial reports, concepts, and facts require source-family classification. Provider publication
  evidence may be used only where it is present and semantically defensible; missing publication
  evidence remains unavailable rather than being inferred from period end or filing deadline; and
- industry snapshots, F10 ownership snapshots, observation-gap detections, and quality exceptions
  remain forward-only.

Each availability rule is versioned by table/source family and is included in the successor
semantic digest.

### 4. Rebuild dependent increments and consumers explicitly

After successor Base acceptance:

1. rebuild or rebase all DWD daily increments after 2026-07-31 against the successor identity and
   reference graph;
2. do not activate weekly DWD corrections until they use the same successor contracts;
3. publish new CN01/Hermes node-local Base identities and a checksum-identical OCI mirror before
   moving any consumer pointer;
4. revise and reapprove the P4 physical-source policy and candidate input contract if table fields,
   availability rules, or package identity change; and
5. leave legacy DWS/ADS/research bundles retained but unauthorized. They cannot provide historical
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
- ODS sufficiency is confirmed against physical source schemas and rows, not only builder code;
- daily, weekly, DWS, ADS, event-library, and other consumer dependencies are enumerated with
  migration dispositions;
- focused tests prove corrected semantics and reject future leakage, missing evidence, timestamp
  substitution, mixed Base identities, and in-place mutation; and
- a human approves the exact successor policy and semantic digests.

The read-only audit command is `ops/bin/audit-dwd-pit-semantics`; its operation is documented in
`docs/operations/dwd-base-pit-audit.md`.

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

- the physical three-node audit and successor rebuild require privileged read-only data access;
- some history may remain unavailable when source publication evidence cannot be recovered;
- post-Base daily increments require replay or explicit retirement; and
- P4 Stage B remains blocked until its source package and exact availability contracts are
  reapproved.
