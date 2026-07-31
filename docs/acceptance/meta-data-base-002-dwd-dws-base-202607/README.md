# META-DATA-BASE-002 DWD/DWS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-002` |
| Tracking issue | GitHub issue #48 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | Phase A tooling and OCI-local dry-run passed; three-node publication open |
| Policy | `config/pipelines/dwd-dws-base-through-20260730.v1.json` |
| Publication authority | unchanged; no consumer or canonical pointer advanced |

## Verdict

The repository can freeze the exact canonical generations for 2026-07-27 through 2026-07-30,
package them with the accepted historical seed into separate immutable DWD and DWS Bases, and prove
that DWS is an exact projection of the packaged DWD members. Copy-only installation and guarded
Base-descriptor advancement also passed in an isolated `/tmp` node layout.

This is not Phase A three-node publication acceptance. The dry-run used uncommitted working-tree
code over checkout baseline `531ff2f51f152ecee95b2fe37f855d01cb16e8b6`; its package digests are
diagnostic and must not be published as reviewed final identities. No `/srv` package, remote node,
schedule, canonical catalog, ADS, release set, legacy pointer, or consumer pointer changed.

## Frozen inputs

The checksum-bound canonical catalog was `catalog-0e8ec5a9571e7ede6922` at
`sha256:cf2a49c09e0ad24808bfb29477582b959a267ce145472a867d2d1a72ca9d0fcd`.
The ledger selected exactly four canonical CN01 generations and produced input digest
`sha256:88b42537ad04d486b500c7fd3cc1dddf9d8a009ee9946e74dae8f6197318fa8b`.
Each entry binds its acceptance record, candidate/content digest, exact epoch parent, ODS release,
shared references, cutoff, transform/runtime identity, and hard gates. The historical seed is
explicitly recorded as legacy-accepted shared evidence, not relabeled as independent lane truth.

## Dry-run results

| Layer | Package | Manifest SHA-256 | Content digest | Payload bytes / files |
| --- | --- | --- | --- | --- |
| DWD | `dwd-base-through-20260730-v1` | `sha256:59db72117be38b9999540c3630b34acb0660e9229dd56c9a4a3ab7238c067b23` | `sha256:a33e6c3c535d0fa6dc6809a0796f7bae40ffd6f3dc73dee56a078d164eab4ce5` | 393,413,062 / 15 |
| DWS | `dws-base-through-20260730-v1` | `sha256:9b9bc43bc6d7afa0c899b24b50896e5deae6f21eb3d46eac962cf438971e53ea` | `sha256:e89b2d50e1ecfa250743ca8406be7dc5ad2965868b61ac9a5f3b0ff86edb2774` | 118,501 / 2 |

DWD retains 14,478,206 historical bar keys and 19,964 accepted daily bar keys. The four daily
partitions each contain 4,991 bar rows and 4,991 status rows. Duplicate/null business keys,
bar-without-status rows, status-without-code-validity rows, invalid status rows, and former-boundary
overlap rows were all zero.

The retained historical universe snapshot ends on 2026-07-23; the verifier does not overstate it.
Continuity at the 2026-07-24/27 boundary is instead proved from exact status and code-validity
members, with zero unmatched rows. DWS contains 3,782 unique market dates from 2011-01-04 through
2026-07-30, zero aggregate-invariant failures, and zero rows of semantic difference versus the old
seed plus accepted daily summaries.

The bounded verifier completed in 23.67 seconds wall time at 1,109,384 KiB peak RSS after enforcing
a 1 GB DuckDB memory limit and two worker threads; spill output was 3,320,064 filesystem blocks.
This is local resource evidence, not node qualification.

## Failure and publication boundary

An isolated install created both immutable package directories while leaving both
`BASE_CURRENT.json` files absent. A deliberately wrong DWD pointer SHA precondition failed with
exit code 2 and left both descriptors absent. The subsequent explicit `absent` preconditions
advanced only the isolated DWD/DWS Base descriptors, whose payloads cross-bound the same pair and
ledger. Unit tests also reject input-ledger and pair-digest tampering.

## Open gates

- Rebuild from a clean reviewed commit and prove identical content on repeat.
- Transfer, install, and verify checksum-identical packages on OCI, CN01, and Hermes before any
  Base descriptor advances.
- Rebuild both lane logical catalogs against the new Base while preserving their exact daily
  lineage and all canonical/ADS/consumer pointers.
- Record clean-path restore, interruption, timing, free-space, transfer, and node resource evidence.
- After July 31 close, bind the actual final Issue #47 ODS Base and accepted July 31 generation,
  build new `dwd-base-202607-v1` and `dws-base-202607-v1`, and rerun every gate.
