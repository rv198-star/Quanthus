# DWD Base V2 Full Pre-Rebuild Audit Runbook

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / Issue #78 |
| Machine policy | `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json` |
| Audited Base | `dwd-base-202607-v2` through 2026-07-31 |
| Intended successor | new immutable `dwd-base-202607-v2.1` through 2026-07-31 |
| Audit execution node | CN01 |
| Audit/build workspace | mandatory `/personal` NFS |
| Audit delivery gate | open for read-only evidence collection |
| V2.1 build gate | blocked until the full audit and one-time remediation scope are accepted |

This audit exists because a DWD Base reconstruction is expensive and disruptive. It expands Issue
#78 from a timestamp-focused investigation into a complete Base-quality gate. The objective is to
find physical, semantic, identity, relationship, key, interval, coverage, numeric, lineage,
reproducibility, and consumer-compatibility defects before the V2.1 builders start, then freeze one
complete remediation ledger for a single successor build window.

The audit does not mutate ODS V2, DWD V2, Daily packages, mirrors, or pointers. A successful audit
is permission to review a proposed remediation scope, not automatic permission to build or cut over.

## Hard Boundary

Until the machine policy's `audit_closure_gate` is satisfied and the exact digests are approved:

- do not start a V2.1 domain builder;
- do not assemble a partial or complete V2.1 candidate;
- do not repair only the already known PIT findings and defer other detected defects;
- do not rewrite or normalize V2 files in place;
- do not override historical timestamps in a consumer;
- do not treat legacy DWS, ADS, release sets, mutable pointers, or local databases as authority; and
- do not close Issue #78 after audit scope freeze while the successor remains unaccepted.

Child Issues may implement bounded audit tools or remediation changes, but Issue #78 remains the
cross-workstream gate and owns the final findings/remediation digest.

## Fixed Inputs

The audit binds:

```text
DWD V2 package:
  dwd-base-202607-v2
  manifest sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f
  content  sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9

ODS V2 evidence:
  ods-base-202607-v2
  manifest sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b
  content  sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01
```

The DWD mirror labels are `hermes`, `cn01`, and `oci`. Every physical result records hostname,
resolved path, device/mount identity, principal, code revision, command, start/end time, and every
package-relative artifact accessed.

## Workspace

All audit outputs are written under:

```text
/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit
```

The audit uses the same CN01 single-writer and NFS preflight rules as
`docs/operations/dwd-v2-1-cn01-nfs-rebuild.md`. The local rebuild lock remains authoritative. NFS
failure stops the current stage; there is no local-scratch fallback.

The output root contains immutable-identity run directories. A later run never overwrites an earlier
report. Canonical summaries are created only after all referenced run reports are checksum-bound.

## Audit Dimensions

### 1. Package integrity and mirror equality

For all three DWD mirrors:

- verify READY/manifest binding;
- verify package id, manifest SHA, content digest, and through-date;
- verify size and SHA-256 of every manifest-declared member;
- compare physical inventory with the manifest; and
- prove byte equality or record every divergent file and package identity.

Semantic analysis may proceed on a verified copy, but Issue #78 cannot close while a required mirror
is missing, unreadable, or divergent.

### 2. Schema and partition contract

For every Parquet part in all 33 tables:

- compare physical schema with the manifest and DWD model;
- check logical types, timestamp timezone, nullable fields, decimals, enums, and dictionary types;
- verify partition values match physical paths and row values;
- detect mixed schemas inside one table; and
- classify verified zero-row/zero-column files separately from corrupt or undeclared schema.

A package-wide row count cannot substitute for this partition-level check.

### 3. PIT and field availability

Audit both rows and mutable fields:

- `knowledge_from`/`knowledge_to` validity;
- business/effective/observation/publication/acquisition/detection coordinates;
- attributes taken from a current snapshot but attached to historical entities;
- exact versus normalized/date-level timestamps;
- maximum-input-knowledge rules for derived rows; and
- forward-only or fail-closed boundaries.

Every table receives a disposition, and every mutable field family receives an availability rule or
an explicit unavailable classification.

### 4. Identity and entity graph

Test:

- stable security and company identity;
- code reuse and identifier intervals;
- listing, delisting, relisting, venue, and company lifecycle;
- name/reference attributes that may leak from the current snapshot; and
- consistency of identity bindings in every dependent domain.

The audit must distinguish a complete current mapping from proof that the same attributes were
historically knowable.

### 5. Referential integrity

For every declared relationship, check at the relevant effective and knowledge coordinate:

- orphan parents;
- parents introduced after children;
- cross-version mismatches;
- interval containment; and
- complete dependency closure for derived rows.

A foreign key resolving in the final snapshot is insufficient when it did not resolve at historical
knowledge time.

### 6. Business keys, row keys, and versions

Measure:

- primary/business-key uniqueness;
- deterministic row-key stability;
- duplicate coordinates with conflicting values;
- source revision and knowledge-version identity; and
- whether late corrections create immutable versions instead of replacing history.

Every duplicate is either proven equivalent or receives a finding. Silent last-write-wins behavior
is forbidden.

### 7. Temporal interval integrity

Check:

- start/end order;
- overlaps where exclusivity is required;
- unexplained continuity gaps;
- listing/calendar bounds; and
- derived interval alignment with all source intervals.

Expected gaps must be reason-coded and counted. They cannot be hidden by forward-filling.

### 8. Completeness and coverage

Build expected-versus-actual coordinate matrices for:

- security/date observations;
- entity/period records;
- lifecycle and state intervals;
- 2012-2021 development data;
- 2026 data through 2026-07-31; and
- the July Daily tail already embedded in ODS V2.

Every excluded row or coordinate has an explicit reason and count. The audit rejects unexplained
loss, unsupported synthetic completion, and accidental tail truncation.

### 9. Numeric and domain invariants

At minimum audit:

- OHLC ordering and nonnegative volume/amount;
- factor positivity and discontinuities;
- valuation/share-capital constraints;
- price-limit and tradability consistency;
- financial unit, currency, concept, and value compatibility; and
- categorical values outside the governed domain.

A checksum-valid value can still be semantically impossible.

### 10. Lineage and source authority

For every table and sampled row family, prove:

- source path/revision resolvability;
- exact ODS/input-plan identity;
- builder code revision and policy digests;
- exclusion/correction reason lineage; and
- absence of legacy or mutable authority.

No report may infer lineage from filenames alone when the manifest declares stronger identity.

### 11. Derived replay determinism

For every derived builder, run two independently named executions over representative real
partitions with identical frozen inputs. Require byte identity or an explicitly declared semantic
digest when physical byte equality is not the contract.

Reject dependencies on:

- network access;
- mutable databases or pointers;
- wall-clock time or file mtime;
- ambient current snapshots;
- nondeterministic ordering; or
- unbound environment configuration.

### 12. Downstream and increment compatibility

Enumerate and classify:

- post-Base Daily packages;
- planned weekly corrections;
- P4 and other historical PIT consumers;
- mirror/restore/deployment processes;
- retained research and operational artifacts; and
- rollback dependencies.

A repaired Base does not automatically authorize a V2-bound increment or consumer.

### 13. Rebuild, publication, and rollback readiness

Before opening the builder gate, verify:

- CN01/NFS capacity and single-writer topology;
- partition pilot memory/spill bounds;
- all findings included in one remediation scope;
- NFS assembly and local-ingress double verification;
- compare-and-swap cutover; and
- retained V2 rollback.

## Finding Ledger

Every finding contains:

- stable `finding_id`;
- audit dimension and table/cross-table scope;
- severity;
- exact evidence and reproduction command;
- root cause;
- affected row/partition count;
- one allowed disposition;
- required builder or contract change;
- regression tests; and
- V2.1 acceptance check.

Allowed dispositions are:

- `fix_in_v2_1`;
- `retain_with_approved_contract`;
- `forward_only`;
- `fail_closed_exclusion`;
- `add_immutable_evidence_then_fix_in_v2_1`; or
- `no_change_proven`.

`unknown`, `defer`, and unclassified findings are not valid at remediation-scope freeze. A high
finding may remain only with an approved explicit disposition. No critical finding may remain open.

## Required Outputs

The canonical audit package contains:

- `audit-plan.json`;
- `mirror-and-package-inventory.json`;
- `table-and-partition-profile.json`;
- `field-availability-matrix.json`;
- `identity-graph-audit.json`;
- `referential-integrity-audit.json`;
- `key-version-and-duplicate-ledger.json`;
- `temporal-interval-audit.json`;
- `coverage-and-exclusion-matrix.json`;
- `numeric-domain-invariant-audit.json`;
- `lineage-authority-audit.json`;
- `replay-determinism-audit.json`;
- `downstream-and-increment-impact.json`;
- `findings-ledger.json`;
- `v2.1-remediation-scope.json`;
- `v2.1-build-contract.json`; and
- `audit-summary.md`.

Every JSON output has a canonical digest. The summary records those digests rather than duplicating
or redefining their content.

## Phase Gates

| Phase | Exit gate |
| --- | --- |
| A — scope and inventory | Package identities, mirrors, 33 tables, dimensions, commands, code revision, and output paths frozen |
| B — physical/schema | Every mirror and manifest part has integrity and schema disposition |
| C — semantic/identity/temporal | Every table and field family has PIT, identity, relationship, key, interval, and lineage evidence |
| D — coverage/numeric/replay/consumer | Coverage, invariants, real-partition replay, and downstream impact measured and reason-coded |
| E — remediation freeze | No unknown finding; complete one-time findings and remediation digests produced |
| F — approval to build | Audit, remediation, builder contracts, semantic digests, and CN01 execution plan explicitly approved |

Only Phase F opens the V2.1 domain-builder gate.

## Issue #78 Closure

Issue #78 does not close when the audit plan or remediation proposal is written. It closes only after:

1. the full pre-rebuild audit is accepted;
2. the complete 33-table V2.1 package is built and accepted;
3. the CN01 local package is installed and deeply verified;
4. Hermes and OCI successor mirrors are checksum-identical;
5. consumer cutover and rollback are accepted; and
6. V2 remains retained as immutable rollback evidence.

The post-2026-07-31 Daily clean/rebuild/rebase migration may remain a separately tracked execution
workstream, but its exact compatibility and migration disposition must be frozen under Issue #78.
