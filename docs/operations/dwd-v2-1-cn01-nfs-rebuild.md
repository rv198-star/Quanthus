# DWD Base V2.1 CN01 NFS Rebuild Runbook

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / Issue #78 |
| Governing decision | `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` |
| Pre-rebuild audit gate | `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json` |
| Machine topology | `config/audits/ods-pit-sufficiency.v1.json` → `cn01_rebuild_execution_topology` |
| Execution node | CN01 only |
| Build storage | mandatory `/personal` NFS |
| Successor | `dwd-base-202607-v2.1` through 2026-07-31 |
| Delivery gate | blocked until the full pre-rebuild audit, findings/remediation digests, builders, full build, and acceptance complete |

This runbook freezes the storage, execution, installation, and rollback topology for the V2.1
reconstruction. It does not approve table availability semantics or authorize a full build before
the exact pre-rebuild audit, findings ledger, remediation scope, builder contracts, and semantic
digests are accepted.

## Mandatory Pre-Rebuild Audit Gate

The complete audit in `docs/operations/dwd-base-v2-pre-rebuild-audit.md` runs before this rebuild.
The known PIT findings are not sufficient authorization to begin a partial repair. The builder gate
remains closed until all 33 tables and every required audit dimension are complete, all findings
have an approved disposition, no critical finding remains open, and the exact canonical digests of
these artifacts are approved:

- `findings-ledger.json`;
- `v2.1-remediation-scope.json`;
- `v2.1-build-contract.json`;
- table/source availability contracts; and
- the audit summary that binds every supporting report.

The accepted remediation scope is an immutable input to planning. A domain builder must reject a
plan that omits an approved finding assigned to its table/domain or uses a different audit/remediation
digest. New material findings discovered during a pilot or full build close the builder gate again;
they are added to the ledger and require scope reapproval rather than being patched informally.

## Fixed Inputs And Outputs

| Role | Path |
| --- | --- |
| ODS Base V2 input | `/srv/quanthus/ods/base/ods-base-202607-v2` |
| DWD Base V2 comparison/rollback input | `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2` |
| Local coordination root | `/srv/quanthus/dwd-dws-v2/v2.1-rebuild` |
| Local lock | `/srv/quanthus/dwd-dws-v2/v2.1-rebuild/locks/rebuild.lock` |
| NFS workspace root | `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1` |
| Hidden local ingress | `/srv/quanthus/dwd-dws-v2/base/.incoming-dwd-base-202607-v2.1` |
| Installed successor | `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1` |
| Active pointer | `/srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json` |

No build may use Hermes or OCI as an execution node. Code reaches CN01 through GitHub and a
node-local `git pull --ff-only`; repository code is not copied from OCI.

## Observed Capacity And Host Limits

The 2026-08-06 preflight observed:

| Resource | Observation |
| --- | ---: |
| CN01 local free bytes | 37,807,775,744 |
| Installed V2 package bytes | 14,913,159,818 |
| Prior Base planner required free bytes | 100,290,327,430 |
| NFS free bytes reported | 11,258,750,132,289,536 |
| CPU | 2 |
| RAM | 3,900,575,744 bytes |
| Swap | 4,294,963,200 bytes |

Local disk cannot be used for candidates, spill, checkpoints, or full-package assembly. NFS is a
hard prerequisite, not an optimization.

## NFS Contract

The accepted mount is `/personal`, backed by:

```text
9b7fc480d7-tqn18.cn-zhangjiakou.nas.aliyuncs.com:/4467464/3IJ8Z5uBnQ/personal
```

The observed mount is NFSv3 with `rw`, `hard`, `nolock`, `noresvport`, and `proto=tcp`. Controlled
probes passed write, atomic rename, same-mount hard link, and CN01-local `flock`; their temporary
probe directory was removed.

Before every build or resume, fail closed unless all of the following hold:

- mount point is exactly `/personal` and filesystem type is NFS;
- options include `rw`, `hard`, `proto=tcp`, and `noresvport`;
- free space is at least 128,849,018,880 bytes (120 GiB);
- the workspace parent is writable by the build principal;
- an atomic rename and same-mount hard-link probe succeeds;
- the local CN01 lock can be acquired; and
- the ODS and DWD V2 input identities still match the frozen manifests.

The mount is root-squashed. Builders must use explicit `umask`/modes and must not depend on
`chown`. Because the mount is `nolock`, no NFS or cross-host lock is authoritative. CN01 is the only
writer; the authoritative rebuild lock remains on local storage.

If NFS disappears, becomes read-only, changes identity/options, falls below the capacity floor, or
fails a probe, stop the active stage. Do not continue on local scratch and do not partially install.

## NFS Workspace Layout

Create one immutable-identity workspace only after preflight:

```text
/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/
  plans/
  runs/
  candidates/
  scratch/duckdb/
  checkpoints/
  july-tail/
  assembled/
  evidence/
```

Roles:

- `plans/`: sealed input plan, source ledger, semantic policy identities, capacity receipt;
- `runs/`: stage receipts and deterministic run identifiers;
- `candidates/`: nine domain candidates and their READY/manifest envelopes;
- `scratch/duckdb/`: DuckDB temp/spill only;
- `checkpoints/`: resumable builder checkpoints bound to exact plan and code revision;
- `july-tail/`: cleaned ODS July-tail projections and reconciliation reports;
- `assembled/`: complete 33-table V2.1 package candidate;
- `evidence/`: audits, row counts, schema/digest comparisons, and acceptance reports.

No `CURRENT`, production pointer, service state, or operational catalog may be stored in this NFS
workspace.

## Execution Limits

CN01 has two CPUs and about 3.9 GB RAM. The full rebuild runs at most one domain builder at a time.
A bounded partition pilot must establish and freeze each heavy builder's memory limit before the
full run. All DuckDB temp/spill paths point to NFS. Swap is emergency protection, not normal working
memory.

The build sequence is deterministic:

1. acquire the local single-writer lock;
2. verify Git revision and clean node-local checkout;
3. verify ODS V2 and DWD V2 immutable identities;
4. verify NFS mount, capacity, probes, and empty/non-conflicting workspace identity;
5. verify the accepted pre-rebuild audit, findings ledger, one-time remediation scope, and builder-contract digests;
6. freeze the complete V2.1 input plan and semantic digests on NFS, binding those audit/remediation digests;
7. run partition pilots and record memory/spill bounds;
8. build the nine domain candidates sequentially on NFS;
9. clean and reconcile the July tail through 2026-07-31 on NFS;
10. assemble the complete 33-table package on NFS using same-filesystem hard links;
11. deep-verify package inventory, READY, manifest, every file SHA, schemas, row counts, cross-table
    identities, PIT distributions, audit-finding closure, and V2 business-row differences;
12. check local installation capacity;
13. copy the manifest-declared package into the hidden local ingress;
14. deep-verify the local ingress byte-for-byte and semantically;
15. atomically rename the ingress to the installed V2.1 path without changing `BASE_CURRENT.json`;
16. obtain human approval for the exact manifest, closed findings ledger, and semantic digests;
17. compare-and-swap `BASE_CURRENT.json` from the exact V2 manifest to V2.1; and
18. verify consumers and retain V2 as the immediate rollback target.

## Capacity Gates

### NFS build gate

NFS free space must be at least 120 GiB before the plan is frozen and before every heavy stage.
The threshold exceeds the prior 100,290,327,430-byte estimate and is recalculated after partition
pilots. A larger measured requirement replaces, never lowers, this floor without approval.

### Local installation gate

Immediately before copying the sealed candidate:

```text
local_free_bytes >= sealed_candidate_total_bytes + 17,179,869,184
```

Use actual manifest-declared bytes, not the V2 reference size. If the gate fails, stop. Space relief
may only target non-authoritative historical candidates, failed workspaces, diagnostics, or old
plans after a checksum-bound NFS cold copy and explicit deletion approval. Never move or delete the
installed Base, Daily packages, pointers, notifications, or original evidence as an automatic
capacity action.

## Cross-Filesystem Installation

The existing domain publisher uses hard links and therefore cannot publish directly from NFS to the
local Base root. The accepted boundary is:

1. assemble and deep-verify the complete candidate on NFS;
2. copy only manifest-declared files plus `manifest.json` and `READY` to the hidden local ingress;
3. preserve relative paths and verify size/SHA while copying;
4. fsync copied files and local directories;
5. run full local deep verification;
6. atomically rename the hidden ingress to the final local package directory; and
7. leave `BASE_CURRENT.json` unchanged until separate approval.

NFS candidate identity is evidence; the local installed package is the only package eligible for
CN01 operational pointer activation.

## Pointer Cutover And Rollback

Cutover is compare-and-swap. The expected predecessor manifest is:

```text
sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f
```

Reject cutover if `BASE_CURRENT.json` moved, the local V2.1 package changed, any acceptance gate is
open, or consumer migration is not approved. Pointer mutation is the only cutover action; neither
V2 nor V2.1 bytes are modified.

Rollback restores `BASE_CURRENT.json` to the retained V2 package after verifying the exact V2
manifest. Do not delete V2.1 during rollback; retain both packages and all build/acceptance evidence.
Post-2026-07-31 Daily migration has its own rollback and is not part of the Base pointer change.

## Forbidden Actions

- start a V2.1 builder or candidate assembly before the full pre-rebuild audit closes;
- omit, defer, or silently reclassify an approved finding from the one-time remediation scope;
- build V2.1 on Hermes or OCI;
- mutate ODS V2, DWD V2, or existing Daily packages;
- place lock authority, `BASE_CURRENT.json`, or another operational pointer on NFS;
- fall back to local candidate, checkpoint, or spill storage;
- attempt hard links across NFS and local filesystems;
- run more than one domain builder concurrently;
- activate V2.1 before local deep verification and exact human approval; or
- delete V2 or original Daily evidence to create space automatically.
