# META-DATA-BASE-002 DWD/DWS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-002` |
| Tracking issue | GitHub issue #48 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | Phase A tooling and OCI-local immutable package installation passed; three-node publication open |
| Policy | `config/pipelines/dwd-dws-base-through-20260730.v1.json` |
| Publication authority | unchanged; no Base, consumer, or canonical pointer advanced |

## Verdict

The repository can freeze the exact canonical generations for 2026-07-27 through 2026-07-30,
package them with the accepted historical seed into separate immutable DWD and DWS Bases, and prove
that DWS is an exact projection of the packaged DWD members. The clean package pair built from
`669b70b2e0d3ea68687f8ead400d09e212fd28cd` is installed in OCI's authoritative Base roots and
passes independent deep verification there. Both packages remain deliberately inactive because
neither Base root contains `BASE_CURRENT.json`.

This is not three-node publication acceptance. CN01 and Hermes were not changed, and no schedule,
Base descriptor, canonical catalog, ADS, release set, legacy pointer, or consumer pointer changed.
The final July Base still requires the actual Issue #47 ODS Base and the accepted July 31
generation; the installed through-2026-07-30 pair must never be relabeled as that final Base.

## Frozen inputs

The checksum-bound canonical catalog was `catalog-0e8ec5a9571e7ede6922` at
`sha256:cf2a49c09e0ad24808bfb29477582b959a267ce145472a867d2d1a72ca9d0fcd`.
The ledger selected exactly four canonical CN01 generations and produced input digest
`sha256:88b42537ad04d486b500c7fd3cc1dddf9d8a009ee9946e74dae8f6197318fa8b`.
Each entry binds its acceptance record, candidate/content digest, exact epoch parent, ODS release,
shared references, cutoff, transform/runtime identity, and hard gates. The historical seed is
explicitly recorded as legacy-accepted shared evidence, not relabeled as independent lane truth.

## Initial diagnostic dry-run

The first clean-path rehearsal used uncommitted working-tree code over checkout baseline
`531ff2f51f152ecee95b2fe37f855d01cb16e8b6`. Its package digests below remain diagnostic and were
not installed as reviewed package identities.

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

## OCI-local immutable installation

The production-identity build froze the same input ledger from a clean checkout and recorded full
producer revision `669b70b2e0d3ea68687f8ead400d09e212fd28cd` in both manifests. The pair was
built in `/tmp/quanthus-issue48-authoritative-669b70b` and installed copy-only at:

- `/srv/quanthus/dwd/base/dwd-base-through-20260730-v1`
- `/srv/quanthus/dws/base/dws-base-through-20260730-v1`

| Layer | Manifest SHA-256 | Content digest | Canonical tree SHA-256 | Payload bytes / files |
| --- | --- | --- | --- | --- |
| DWD | `sha256:ed1d247893d50802e5498bd956c2f7e5365d0dcd357d9bf75da084ec84725f6b` | `sha256:e559cbdf33594e9281b18676f4f4cb1b0c7a611acc84ff5be08f9d1271b3fa7c` | `sha256:c8dec6e3a508a231d490387208b245cb6e11d0c623be5fc793703cf077282d4e` | 393,413,062 / 15 |
| DWS | `sha256:2770a401fb524d6cb5bb8230b5935d9fca471028a2bb6f21c82e0d3e2095cca1` | `sha256:14e2d0553fea3375417b52d6483e8ffff96a997a27ebd3c0960b054eeeb78944` | `sha256:7252322698195a75daa6e003851930bbf7ff3ac91bfd2b0aff309ae22bc17d3f` | 118,501 / 2 |

The canonical tree digest is SHA-256 over a GNU tar stream sorted by path with epoch mtime and
numeric owner/group zero. Staging and installed tree digests match for both layers, and recursive
file comparison reports no differences. Direct installed-pair verification completed in 17.18
seconds at 1,192,300 KiB peak RSS. Re-running the installer completed in 47.67 seconds, returned
the same package paths and ledger digest, and again reported `descriptors_advanced=false`.

OCI package directories and files are owned by `ubuntu:ubuntu`, with directory mode `0750` and
file mode `0640`. Pre-build free space was 11 GiB at 89% utilization; the final observation was
8.6 GiB at 92%. No hidden installer staging directory remained. Both Base roots still have no
`BASE_CURRENT.json`.

| Operation | Wall time | Peak RSS |
| --- | ---: | ---: |
| Ledger freeze | 3.28 seconds | 139,124 KiB |
| Build plus embedded verification | 48.64 seconds | 1,276,916 KiB |
| Standalone staging verification | 16.94 seconds | 1,210,236 KiB |
| Copy-only installation plus verification | 58.21 seconds | 1,207,668 KiB |
| Installed-pair verification | 17.18 seconds | 1,192,300 KiB |
| Idempotent installer replay | 47.67 seconds | 1,255,248 KiB |

## Verifier failure and repair

Two initial install attempts against the obsolete `943df11` package terminated with `SIGSEGV`
before creating a package or staging directory under either `/srv` root. Python faulthandler placed
the crash in `_projection_difference_rows`, called through `verify_dwd_dws_base_pair` and
`install_dwd_dws_base_pair`. The verifier had evaluated the full DWD projection twice through a
bidirectional `EXCEPT ALL` comparison under the 1 GiB DuckDB limit.

Revision `d8f6cd941db0dd8075a1ec47e19ce6de58b70ab8` changed the verifier to execute the full
projection once and compare the approximately 3,782 projected and expected rows with exact Python
multiset subtraction. Revision `669b70b2e0d3ea68687f8ead400d09e212fd28cd` added the regression
test for single-projection and multiset behavior. Follow-up revision
`d95bdf1ed4f8ca4dd506e78a3bf1e2899cc3b4a2`, committed after package generation, materializes
the projected and expected results before ordered reads; it does not change package payload
construction. The installed pair passed verification with that follow-up verifier.

The obsolete package manifests were
`sha256:a9c8c701929e8aab598afde7ee8a6143fe54230ff4baafe57a5cd04b5265d668`
for DWD and
`sha256:2243d415201b47f9893619fa4baf24659b433cf215fad187dab05bad1f97e566`
for DWS. They remain diagnostic identities and were not installed.

## Failure and publication boundary

An isolated install created both immutable package directories while leaving both
`BASE_CURRENT.json` files absent. A deliberately wrong DWD pointer SHA precondition failed with
exit code 2 and left both descriptors absent. The subsequent explicit `absent` preconditions
advanced only the isolated DWD/DWS Base descriptors, whose payloads cross-bound the same pair and
ledger. Unit tests also reject input-ledger and pair-digest tampering.

The same isolated active pair built and independently verified Base-aware catalog
`base-lane-history-cn01-20260730-079194927346fdda`. It correctly retained zero post-Base daily
partitions, declared `through_date=2026-07-30`, and bound content digest
`sha256:079194927346fdda5aef58b98c2be1cca28932602dd43e8d7eb526e0e187fc8a`.
This proves the empty-tail transition shape without changing either deployed lane catalog.

## Open gates

- Rebuild from the same reviewed producer commit to prove deterministic package bytes on repeat.
- Transfer, install, and verify checksum-identical packages on OCI, CN01, and Hermes before any
  Base descriptor advances.
- Rebuild both lane logical catalogs against the new Base while preserving their exact daily
  lineage and all canonical/ADS/consumer pointers.
- Record clean-path restore, interruption, timing, free-space, transfer, and node resource evidence.
- After July 31 close, bind the actual final Issue #47 ODS Base and accepted July 31 generation,
  build new `dwd-base-202607-v1` and `dws-base-202607-v1`, and rerun every gate.
