# DWD Base V2 Pre-Rebuild Audit Closure And V2.1 Handoff

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / GitHub Issue #78 |
| Evidence status | machine audit complete with findings; exact human approval pending |
| Canonical audit node | CN01 only |
| Audited package | `dwd-base-202607-v2` through 2026-07-31 |
| Audit code revision | `15bcdb6fccdd7f25caea37c6f509798ecd64991b` |
| Audit result | 31 findings: 30 high, 1 medium, 0 critical |
| Builder gate | `blocked` / `not_authorized` |
| Successor | `dwd-base-202607-v2.1`, 33 tables, through 2026-07-31 |
| Physical build node | CN01 only; an external agent may orchestrate but may not build elsewhere |
| Build workspace | mandatory `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1` |

This directory is the repository handoff packet for the completed machine-audit stage. It imports
the four closure artifacts generated on CN01 without changing their bytes. The artifacts remain
blocked because their only open audit-scope item is exact human approval of the findings and
remediation digests. Importing or pulling this packet does not itself authorize a builder,
candidate assembly, installation, or pointer change.

## Frozen Package Identity

| Property | Value |
| --- | --- |
| Package | `dwd-base-202607-v2` |
| Manifest | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` |
| Content | `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9` |
| Tables | 33 |
| Rows | 468,268,180 |
| Through date | 2026-07-31 |

`BASE_CURRENT.json` still names V2. No V2 or V2.1 package, candidate, Daily package, installed
directory, or pointer was mutated during the audit.

## Packet Contents And Digests

| Artifact | Purpose | Canonical digest |
| --- | --- | --- |
| `findings-ledger.json` | all 31 measured findings, evidence, dispositions, tests, and acceptance checks | `sha256:9a30644f64aacecaab2f9c20797d98d09aa968e1679121e16271ca2063ce9708` |
| `v2.1-remediation-scope.json` | one-time V2.1 repair/evidence scope | `sha256:e0c4e984e3508a77ac9e7b0546e6361e8be43bcbdaf3af76813aa40e4b8251db` |
| `v2.1-build-contract.json` | package, source-digest, topology, and execution gates | `sha256:045100edb5baa3cdeec32167c33dd8466d129b6897513876104b168ac70f860c` |
| `audit-summary.md` | concise result and supporting CN01 evidence paths | not a canonical JSON artifact |
| `SHA256SUMS` | exact Git-imported file-byte verification | raw file SHA-256 values |

Canonical JSON digests are calculated over the object without its `canonical_digest` member. Raw
file SHA-256 values intentionally differ because pretty-printing and the embedded digest are part
of the committed bytes. Verify the imported bytes from this directory with:

```bash
sha256sum -c SHA256SUMS
```

## Finding Summary

The 31 ledger entries are audit-coordinate findings, not 31 independent root causes. A table can
appear under more than one audit dimension.

| Finding class | Count | Required handling |
| --- | ---: | --- |
| Schema and partition contract | 7 | repair writer field/partition output in V2.1 |
| Semantic identity, parent, key, interval, or domain contract | 11 | rebuild against time-valid parents and enforce row/version contracts |
| Numeric/business invariant | 1 | make financial fact value representations mutually exclusive |
| Coverage and through-date boundary | 6 | exclude post-cutoff coordinates and prove complete statistics |
| Deterministic replay evidence | 1 | add two independently named, digest-bound representative executions |
| Rebuild readiness | 4 | close local capacity, local lock, partition-pilot, and NFS-capacity gates |
| Approved retained source exclusion | 1 | retain explicitly; reject consumers requiring the excluded grain |

The dispositions are 25 `fix_in_v2_1`, five
`add_immutable_evidence_then_fix_in_v2_1`, and one `retain_with_approved_contract`. There is no
unknown, deferred, later, or unclassified disposition.

## Audit Implementation Entry Points

The audit implementation and tests are already committed at the bound code revision. The primary
operator entry points are:

| Command | Scope |
| --- | --- |
| `ops/bin/audit-dwd-pit-semantics` | package integrity and PIT/table profiles |
| `ops/bin/audit-dwd-schema-partitions` | all Parquet schema and partition contracts |
| `ops/bin/audit-dwd-semantic-contracts` | identity, parent, key, interval, and domain semantics |
| `ops/bin/audit-dwd-semantic-dimensions` | five named semantic-dimension projections |
| `ops/bin/audit-dwd-coverage-exclusions` | coverage, cutoff, and source exclusions |
| `ops/bin/audit-dwd-domain-invariants` | numeric and business invariants |
| `ops/bin/audit-dwd-lineage-authority` | plan, source, model, and revision authority |
| `ops/bin/audit-dwd-replay-determinism` | representative replay evidence |
| `ops/bin/audit-dwd-downstream-impact` | Daily and consumer compatibility |
| `ops/bin/audit-dwd-rebuild-readiness` | capacity, NFS, lock, pilot, install, and rollback readiness |
| `ops/bin/run-dwd-pre-rebuild-audit-batch` | checkpointed phase/batch orchestration |
| `ops/bin/freeze-dwd-pre-rebuild-audit-closure` | ledger, remediation scope, contract, and summary freeze |

The governing execution procedure remains
`docs/operations/dwd-base-v2-pre-rebuild-audit.md`. The physical reconstruction procedure remains
`docs/operations/dwd-v2-1-cn01-nfs-rebuild.md`.

## Dedicated-Agent Transfer

The dedicated agent must use GitHub as the code transport and keep its CN01 checkout isolated from
the production checkout:

1. Fetch `origin/issue-78-audit-closure` and verify that it contains audit revision `15bcdb6` and
   this evidence packet.
2. Run `sha256sum -c SHA256SUMS` in this directory and run the repository handoff contract test.
3. Compare the exact package identity above with CN01 V2 and confirm `BASE_CURRENT.json` still
   names the expected V2 predecessor.
4. Record explicit human approval of the three canonical digests below in Issue #78.
5. Close the five evidence/readiness findings before starting the full builder: replay receipts,
   local installation capacity, CN01-local single-writer lock, bounded partition pilots, and NFS
   capacity reservation.
6. Implement all 25 V2.1 transformations/contracts and their named regression tests as one
   remediation set. A new material finding closes the gate and requires ledger reapproval.
7. Build the nine domains sequentially on CN01 with all candidate, spill, checkpoint, assembly,
   and evidence bytes on `/personal` NFS.
8. Deep-verify the complete NFS candidate, copy manifest-declared bytes to the hidden local
   ingress, deep-verify again, and install V2.1 without moving the pointer.
9. Obtain separate candidate/cutover approval, then compare-and-swap the pointer from the exact V2
   manifest. Retain V2 and use it as the immediate rollback target.

Post-2026-07-31 Daily packages do not enter V2.1. Their clean/rebuild/rebase migration remains a
separate execution after the Base successor is accepted.

## Approval Record

The machine gate may be regenerated as authorized only after the human decision explicitly binds:

```text
base manifest: sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f
findings ledger: sha256:9a30644f64aacecaab2f9c20797d98d09aa968e1679121e16271ca2063ce9708
remediation scope: sha256:e0c4e984e3508a77ac9e7b0546e6361e8be43bcbdaf3af76813aa40e4b8251db
build contract: sha256:045100edb5baa3cdeec32167c33dd8466d129b6897513876104b168ac70f860c
decision: approve the exact findings and remediation scope for V2.1 readiness and build work
```

This is scope approval, not pointer-cutover approval.
