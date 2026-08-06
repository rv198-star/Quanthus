# ODS Base V2 PIT Sufficiency Audit

This runbook verifies the exact ODS evidence available for `META-DATA-PIT-001` / Issue #78 before
`dwd-base-202607-v2.1` semantics are approved. It is read-only and does not build, copy, rename,
promote, or mutate an ODS or DWD package.

## Scope And Fixed Boundary

| Item | Value |
| --- | --- |
| ODS package | `ods-base-202607-v2` |
| ODS manifest SHA-256 | `sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b` |
| ODS content digest | `sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01` |
| ODS through-date | 2026-07-31 |
| Required physical holders | Hermes and CN01 |
| OCI ODS role | disabled; OCI source-mirror observations are not package authority |
| Successor DWD | new immutable `dwd-base-202607-v2.1` through 2026-07-31 |
| Later Daily | excluded from V2.1 and cleaned/rebuilt/rebased separately |

The machine policy is `config/audits/ods-pit-sufficiency.v1.json`. It defines 18 source families,
21 physical checks, all 33 mapped DWD tables, the accepted successor boundary, and the remaining
source-semantic decisions. CN01's package completed this deep verification on 2026-08-06; Hermes
still requires the same check and checksum comparison.

## Safety Rules

- Run against the physical sealed package directories, not `CURRENT` pointers.
- Hermes and CN01 labels must resolve to different physical roots.
- Mounts or delegated access must be read-only.
- Write reports outside both package roots.
- Do not copy Parquet/SQLite parts into a normalized workspace before inspection.
- Do not infer a missing publication timestamp from period end, effective date, change date, filing
  deadline, file mtime, package build time, or current source snapshot.
- A successful physical scan confirms package facts only. It does not approve identity, valuation,
  financial, or holder-change historical rules.

## Required Node Paths

The normal node-local package path is:

```text
/srv/quanthus/ods/base/ods-base-202607-v2
```

Before running the combined audit, each node operator records:

- hostname and node role;
- resolved package path;
- mount/device identity;
- owner/group and mode;
- manifest and READY readability; and
- confirmation that the audit principal has no write authority.

## Node-Local Deep Verification

On Hermes and CN01 separately:

```bash
cd /root/work/Quanthus

.venv-p1/bin/python ops/bin/verify-ods-base \
  --policy config/pipelines/ods-base-final-through-20260731.v2.json \
  --bundle /srv/quanthus/ods/base/ods-base-202607-v2 \
  --deep
```

The output must report the fixed package id, manifest SHA, content digest, through-date, file count,
and byte count. Any READY mismatch, file checksum mismatch, Parquet metadata drift, SQLite integrity
failure, unexpected path, or policy mismatch blocks the audit.

## Combined Two-Mirror Audit

Use one designated audit host with two distinct read-only mounts, or run node-local full audits and
combine only the immutable reports after package identity comparison. The preferred combined form
is:

```bash
PYTHONPATH=src .venv-p1/bin/python ops/bin/audit-ods-pit-sufficiency \
  --mirror hermes=/audit/ods/hermes/ods-base-202607-v2 \
  --mirror cn01=/audit/ods/cn01/ods-base-202607-v2 \
  --policy config/audits/ods-pit-sufficiency.v1.json \
  --mode full \
  --output /audit/evidence/issue-78-ods-pit-full.json
```

Full mode:

1. deep-verifies every manifest-declared file in each ODS package;
2. confirms exact package/manifest/content/through-date identity;
3. requires Hermes and CN01 package identities to match;
4. locates the manifest-declared parts for each required dataset;
5. checks required Parquet/SQLite columns;
6. records row counts, null counts, and min/max values for business and knowledge fields;
7. records every package-relative part accessed; and
8. writes no file inside either package.

Manifest-only mode is useful for permission setup, but it does not satisfy the ODS closure gate:

```bash
PYTHONPATH=src .venv-p1/bin/python ops/bin/audit-ods-pit-sufficiency \
  --mirror hermes=/audit/ods/hermes/ods-base-202607-v2 \
  --mirror cn01=/audit/ods/cn01/ods-base-202607-v2 \
  --mode manifest-only \
  --output /audit/evidence/issue-78-ods-pit-manifest.json
```

## Required Interpretation

A physically successful report has `execution_gate.status=passed`. The
`remediation_gate.status` remains `blocked_pending_source_semantic_approval` until the following are
resolved:

| Source family | Required decision |
| --- | --- |
| Identity/company/listing/venue | approve stable-key/listing reconstruction from complete ODS coverage; keep unevidenced historical display/reference attributes unavailable or add evidence |
| Security/index bars | approve interval/session-completion availability rules |
| Valuation | approve provider-specific availability evidence or exclude history |
| Realized calendar | approve conservative end-of-date/session knowledge without future-schedule authority |
| Corporate actions | approve end-of-announcement-date rule for date-granular source evidence |
| Financial reports/facts/concepts | use actual publication dates for full/weekly sources; exclude 86 post-cutoff versions and augment or fail close unmatched legacy coordinates |
| Holder changes | use disclosure date where present; augment or exclude rows without disclosure evidence |
| Industry/F10/governance | retain forward-only acquisition/detection semantics |

The audit report must be attached to Issue #78 together with the exact command, code revision,
policy SHA, mount mapping, and operator identity.

## Daily Boundary

The V2.1 Base ends on 2026-07-31. No Daily ODS or DWD package dated 2026-08-01 or later is included
in the Base build.

Later Daily handling is a separate migration:

1. retain original immutable ODS/DWD Daily packages as evidence;
2. audit and clean source artifacts under the corrected source-family rules;
3. rebuild against V2.1 identities and references;
4. publish new immutable increment identities;
5. compare original and rebuilt business rows with explicit reason codes; and
6. move consumer bindings only after the Daily migration gate is approved.

## Failure Semantics

The command exits `2` and records a blocked execution gate for missing mirrors, permissions,
package identity mismatch, checksum failure, missing datasets, schema drift, incomplete scans, or
cross-mirror divergence. A blocked report is evidence of an unresolved gate, not an infrastructure
success and not permission to infer source sufficiency.
