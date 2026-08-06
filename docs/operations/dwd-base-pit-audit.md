# DWD Base Historical PIT Audit Runbook

## Purpose

This runbook produces the read-only evidence required by `META-DATA-PIT-001` / Issue #78. It
verifies installed DWD Base packages, compares Hermes/CN01/OCI identities, and scans all 33 tables
for historical `knowledge_from` distributions. It does not repair, copy, promote, republish, or
modify a Base.

The governing machine policy is `config/audits/dwd-pit-semantics.v1.json`. The target package is:

- package id: `dwd-base-202607-v2`;
- manifest SHA-256: `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f`;
- content digest: `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9`.

## Safety Boundary

- Use the physical package directory containing `manifest.json` and `READY`; never pass a mutable
  `CURRENT` descriptor or symlink.
- Grant the audit principal read and directory-traverse permission only. The tool writes reports to
  a separate evidence root and rejects an output path inside any supplied mirror.
- Do not grant the audit process ODS write access, DWD publication access, consumer-pointer access,
  or credentials for legacy ADS/research bundles.
- Do not copy or normalize Parquet rows before the audit. A read-only bind mount is acceptable when
  it exposes the original installed files and preserves their exact bytes.
- A missing mirror, unreadable manifest, checksum mismatch, absent table, row-count mismatch, or
  incomplete scan blocks the execution gate.

## Prerequisites

Use Python 3.10 with the repository's `p1` dependencies, including DuckDB and PyArrow. Confirm that
the audit principal can read the exact package roots:

```bash
PYTHONPATH=src .venv/bin/python -c 'import duckdb, pyarrow; print(duckdb.__version__, pyarrow.__version__)'
test -r /path/to/dwd-base-202607-v2/manifest.json
test -r /path/to/dwd-base-202607-v2/READY
```

The deployed CN01 and Hermes examples use
`/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2`. OCI may expose the verified mirror under a
different protected root. Resolve the installed physical package path from node configuration;
do not guess it from a directory name.

The final audit environment must expose all three physical package roots at once, normally through
read-only mounts on a designated audit host. Node-local single-mirror runs are useful permission
and identity probes, but remain incomplete by policy.

## Permission Probe

Run a manifest-only probe before the full scan:

```bash
PYTHONPATH=src .venv/bin/python ops/bin/audit-dwd-pit-semantics \
  --mirror hermes=/audit/hermes/dwd-base-202607-v2 \
  --mirror cn01=/audit/cn01/dwd-base-202607-v2 \
  --mirror oci=/audit/oci/dwd-base-202607-v2 \
  --mode manifest-only \
  --output /var/lib/quanthus/audits/issue-78/manifest-audit.json \
  --markdown-output /var/lib/quanthus/audits/issue-78/manifest-audit.md
```

This mode still performs deep package verification: READY binding, manifest identity, exact file
inventory, file sizes, every declared SHA-256, exactly 33 row-count entries, and payload totals. It
does not issue Parquet aggregate queries. Exit code `0` means all three mirrors were verified and
matched the policy identity; the historical PIT gate in the report remains blocked.

## Full 33-Table Scan

After the manifest probe succeeds, run:

```bash
PYTHONPATH=src .venv/bin/python ops/bin/audit-dwd-pit-semantics \
  --mirror hermes=/audit/hermes/dwd-base-202607-v2 \
  --mirror cn01=/audit/cn01/dwd-base-202607-v2 \
  --mirror oci=/audit/oci/dwd-base-202607-v2 \
  --mode full \
  --output /var/lib/quanthus/audits/issue-78/full-audit.json \
  --markdown-output /var/lib/quanthus/audits/issue-78/full-audit.md
```

For each table and mirror, the command records:

- manifest and actual row counts;
- Parquet file count and representative physical schema;
- minimum and maximum `knowledge_from` and table-specific business/reference coordinate;
- 2012-2021 row count and rows whose knowledge is later than 2021-12-31;
- 2026 reference-coordinate row count;
- count where knowledge is later than the selected reference coordinate;
- development-period knowledge-year distribution; and
- up to ten representative historical rows with future knowledge.

The selected reference field is table-specific and versioned in the audit policy. It is an audit
coordinate, not an authorized replacement for `knowledge_from`. The audit connection pins DuckDB
to UTC before any `TIMESTAMPTZ` cast, year extraction, or rendering. Do not accept cross-node table
profiles from an older revision that inherited the node timezone.

## Report Interpretation

`execution_gate.status=complete` proves that the requested verification and scans completed for all
required mirrors. It does not approve historical use. `historical_pit_gate.status` remains
`blocked` because the code matrix contains confirmed defects and unresolved source families.

Review these sections:

| Report section | Required result |
| --- | --- |
| `mirror_inventory.missing_labels` | empty |
| `mirror_inventory.failed_labels` | empty |
| `mirror_inventory.identity_mismatches` | empty |
| `execution_gate.row_count_mismatches` | empty |
| every mirror `verification_status` | `verified` |
| every mirror `scan_status` | `full` for final evidence |
| `accessed_artifacts.mutation_performed` | `false` |

Any future-knowledge sample must be traced to its ODS source and builder rule. Do not bulk-rewrite
rows based on the distribution alone.

## Evidence Attachment

Attach both JSON and Markdown reports to Issue #78 with:

- audit host and principal role, excluding credentials;
- exact repository commit used to run the command;
- exact read-only mount/source path mapping for Hermes, CN01, and OCI;
- command exit code;
- report digest;
- start/end time; and
- explicit confirmation that no Base, pointer, or downstream package was modified.

The JSON report includes the exact manifest-declared files deep-verified and Parquet files scanned.
Do not attach raw market rows beyond the bounded samples already emitted by the audit.

## Failure Handling

- **Permission denied:** correct directory traversal/read ACLs for the audit principal; do not run a
  mutating deployment account merely to bypass the gate.
- **Manifest or READY mismatch:** quarantine that installed mirror from consumer use and compare it
  with the authoritative release identity before any resynchronization.
- **File checksum mismatch:** retain the bytes and incident evidence; do not overwrite the mirror in
  place.
- **Cross-mirror mismatch:** treat the mirrors as different artifacts even when directory names
  match.
- **Missing table or row-count mismatch:** block the package and inspect publication evidence.
- **DuckDB/PyArrow unavailable:** install the repository's declared `p1` dependency set in the audit
  environment; do not substitute a partial CSV or sampled-file audit.

## Current Phase A Evidence

On 2026-08-06 the authorized principal resolved the exact OCI mirror to
`/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2`, not the older `/srv/quanthus/dwd` tree. OCI and
CN01 independently passed full package verification and all 33 table scans: 76,722 manifest
members verified, 76,671 Parquet files scanned, and 468,268,180 rows reconciled on each node. Their
matching manifest and independent member-SHA checks prove declared package-byte equality. Hermes
remains pending.

The first reports inherited UTC on OCI and `Asia/Shanghai` on CN01. Package and manifest row counts
matched, but all table profiles rendered timestamp offsets differently and five tables returned
different date-window/year metrics. Those reports remain retained finding evidence and are not
accepted as deterministic semantic profiles. Use the UTC-pinned audit revision and rerun both nodes
before Phase B accepts the distributions. OCI's package is complete, but its
`/srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json` descriptor is absent; resolve that publication
contract before V2.1 cutover.
