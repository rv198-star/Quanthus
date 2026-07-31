# META-DATA-BASE-002 DWD/DWS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-002` |
| Tracking issue | GitHub issue #48 |
| Evidence date | 2026-07-31 UTC |
| Evidence status | Phase A and final July Phase B accepted on OCI, CN01, and Hermes |
| Policies | `config/pipelines/dwd-dws-base-through-20260730.v2.json` and `config/pipelines/dwd-dws-base-202607.v1.json` |
| Final producer revision | `74278d4fefd4b44a678bfa81c3be66aaffa0ed3e` |
| Publication authority | only DWD/DWS Base descriptors and lane-history catalogs advanced |

## Verdict

Phase A and final July Phase B are accepted. OCI, CN01, and Hermes retain the byte-identical
corrective `dwd-base-through-20260730-v2` and `dws-base-through-20260730-v2` packages and now also
hold byte-identical final `dwd-base-202607-v1` and `dws-base-202607-v1` packages through
2026-07-31. Independent deep verification, deterministic rebuild, canonical whole-tree comparison,
exact-SHA descriptor advancement, idempotent lane-history catalog replay, and both node doctors
passed. All three active Base descriptor pairs contain identical bytes and cross-bind the same
final package pair and input ledger.

Only the DWD/DWS `BASE_CURRENT.json` descriptors and the CN01/Hermes lane-history catalog
descriptors advanced. Canonical, ADS, target release-set, legacy, and consumer authority pointers
were unchanged, and no `GO_FOR_CUTOVER.json` was created. The through-2026-07-30 v2 pair remains
immutable retained evidence and was not relabeled as the final Base.

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

## Retained original v1 OCI installation

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
8.6 GiB at 92%. No hidden installer staging directory remained. At the time of this retained v1
evidence, both Base roots had no `BASE_CURRENT.json`; the later corrective v2 Phase A advancement
is recorded below.

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

## Immutable v1 identity incident and corrective v2

The original v1 package ids were produced from two different clean revisions. The earlier OCI
staging and acceptance record bind the `669b70b` manifests recorded above. The v1 packages present
on all three nodes when the collision was detected instead bind `d95bdf1`: DWD manifest
`sha256:36fcd12ec2a83a9246092b203fa69fa1239667256915f6a9e8005ec01352d308` and DWS manifest
`sha256:4caa30477668217b0404ab77f2c1bd3b05c56e943590b41218f3949d5670c1f3`. Reusing one immutable
package id for different producer-bound manifests is invalid even when payload members are
semantically equivalent. Detection froze all available v1 artifacts, descriptors, staging, and
recorded evidence; no later operation wrote or deleted a v1 path.

Revision `77214e28c5a0025f7b0da9da7e3fc40480d450e8` introduced additive corrective v2 package ids and
made the default policy, catalog runner, doctor, runbook, and tests select them. GitHub was the
only repository-code distribution path, and all three node checkouts were clean at that revision
before any v2 package or descriptor mutation.

| Layer | Package | Manifest SHA-256 | Content digest | Canonical tree SHA-256 |
| --- | --- | --- | --- | --- |
| DWD | `dwd-base-through-20260730-v2` | `sha256:0f5eb81915ef3a165cea8b3656dcfb7b319038d2df9034416d1e12777b032096` | `sha256:09f07e0d318dba7e854eaeb0be6c8989ac9b78ebb633985e1e3c120b35bb3a2f` | `sha256:8c152c8a91155bed300b783059e0e954ec3daa7a85b701c713a3df98bbb0a462` |
| DWS | `dws-base-through-20260730-v2` | `sha256:4a02032ea184e4ac946babc49eafadcd279a62ffaa9480d735a0cf220afecce1` | `sha256:bdc6f7d6bd0ad94ee6a83a449c1bc10267089c60dadb446eecfe097cfdec1ac4` | `sha256:3acc4b78cdd68d807f55ad45647c7acb3ad72f743b146e7ccc703335cea0710e` |

The v2 input-ledger digest is
`sha256:88b42537ad04d486b500c7fd3cc1dddf9d8a009ee9946e74dae8f6197318fa8b`.
All four digest classes above matched on OCI, CN01, and Hermes.

## Three-node package installation

CN01 completed the write-only transfer in 38.08 seconds. Its copy-only install and embedded deep
verification completed in 1:50.91 at 1,211,728 KiB peak RSS. The installed package owner/group is
`root:quanthus-candidate-ro`, and the installer reported `descriptors_advanced=false`.

Hermes' first full write-only transfer was stopped after 9:49 with about 53 MiB transferred and no
`READY`. The already sealed Hermes v1 and local v2 payload member lists were then independently
compared: the DWD payload-list digest was
`sha256:4a251a812ebc977bb97eac7d729795810c2b8e1f59d7024e1cfc1d59c01fdd74`, and DWS was
`sha256:5aeba0ca2f7ba361f6a42cc84fb9326410fc39528ffd66b1fd8a9e0b730171c3`. A normal copy with
`--reflink=auto`, never hard links, created an unsealed v2 ingress payload owned by
`quanthus-epoch-in:quanthus-epoch-in`.

A later retry incorrectly passed a host-absolute remote root to the `rrsync` jail. It was stopped
after 21:25.52 and had created only an unsealed nested `epoch-incoming/srv/...` partial, including
108,396,544 bytes of one large Parquet member. The exact nested tree was deleted after proving it
contained no `READY`; the intended v2 payload directory was unchanged. With the correct jail-root
`.` path, each v2 manifest was written and hash-checked, then each 72-byte `READY` was written last.
No nested directory or `.rsync-partial` remained.

Hermes installed the sealed pair in 2:49.65 at 1,215,828 KiB peak RSS and reported
`descriptors_advanced=false`. An independent installed-pair verification completed in 45.72 seconds
at 1,210,560 KiB peak RSS and returned the exact v2 manifest, content, and ledger digests above.

## Deterministic rebuild and isolated restore

An independent clean rebuild from the frozen ledger and producer revision `77214e2` completed in
46.57 seconds at 1,275,388 KiB peak RSS under
`/tmp/quanthus-issue48-rebuild-v2-77214e2`. Both manifests, both content digests, both canonical
tree digests, and a recursive file comparison were identical to the authoritative staging at
`/tmp/quanthus-issue48-authoritative-v2-77214e2`.

The rebuilt pair was then installed into isolated
`/tmp/quanthus-issue48-restore-v2-77214e2/{dwd,dws}` roots. The clean install completed in 56.92
seconds at 1,221,468 KiB peak RSS. Replaying the same installer completed in 47.01 seconds at
1,219,412 KiB peak RSS, returned the same package paths and ledger, and again reported
`descriptors_advanced=false`. Recursive comparison against the rebuild was empty, and neither
isolated root contained `BASE_CURRENT.json`.

## Descriptor advancement

Before advancement, all three nodes had identical retained-v1 descriptor SHA-256 values:

| Layer | Old descriptor SHA-256 | New v2 descriptor SHA-256 |
| --- | --- | --- |
| DWD | `sha256:5b6dd59386183a7a971d2dcc086f7534768b06b28ea6401ae880e8256ecfc0a2` | `sha256:a33928eb2de3db3b41d307814f9125d26f1b45c216869e157234560d63fcf4a9` |
| DWS | `sha256:7397fe3c9ab3f59549cde16c62d770db379df85d3624a638905d551b32c692ba` | `sha256:1422689f2291f6ee35fc00f790ed9c777344df23e5dac760a698638bfedef7ea` |

One retry without the required `sha256:` precondition prefix was rejected before writing; CN01
also rejected a runtime missing the P1 dependencies. Re-running from the qualified runtime with
full digest preconditions succeeded on all three nodes. The resulting descriptor bytes match
across nodes and cross-bind pair digest
`sha256:2780c285bbedc73b85a6979aaea868e6a3d44351cbdad4313588fca91863f230`.

## Base-aware lane catalogs

CN01 and Hermes node environments now bind code revision `77214e2` and the v2 policy. Both files
remain `root:root` mode `0640`. Each node ran `run-dual-lane-history-catalog` twice and then passed
`dual-lane-doctor node`.

| Lane | Catalog id | Manifest SHA-256 | Content digest | CURRENT SHA-256 |
| --- | --- | --- | --- | --- |
| CN01 | `base-lane-history-cn01-20260730-a0b4a55b969e4e2e` | `sha256:2df4a129d76a26922145f89e106c9e34b61549064b6f005d3354dd2c3fd39987` | `sha256:a0b4a55b969e4e2e80e7b19b6ec8284c64c5dff106b315e487dfb87f265df904` | `sha256:9a0c5e44e368e64333563de8fd7d5aba70d94adb349674421ab2c078446400fb` |
| Hermes | `base-lane-history-hermes-20260730-52f1bbcf17aaf429` | `sha256:f3e369bebe1d4cabaff1be6489b510c635f319f8c7461b6d8d8127dbace6c2ad` | `sha256:52f1bbcf17aaf42932dcf2d0be59e4624f2cf81ee5a8c3419089df32a69f1810` | `sha256:8054add4d838915c53c64b9a40696c3f7efe4ebea9b852888d346baf7c09cf02` |

Each catalog has `candidate_count=0` and `through_date=2026-07-30`: the four accepted daily
generations are absorbed into the Base, and no post-Base partition was fabricated. CN01's first
and replay runs were 2:06.81 / 2:04.07 with peak RSS 1,260,688 / 1,239,792 KiB. Hermes' runs were
3:13.83 / 3:10.41 with peak RSS 1,300,492 / 1,296,680 KiB. Catalog ids, manifests, contents, and
CURRENT bytes were unchanged by the second run.

## Preserved authority boundary

The following OCI pointer bytes were recorded immediately before descriptor advancement and again
after catalog replay. Every SHA-256 value was unchanged:

| Authority pointer | SHA-256 |
| --- | --- |
| Target release set | `sha256:87c5ecc2c29fbeefaff1cc29711d15c6c69fa59daed8b691515ffd05b878d8d3` |
| Canonical publisher lease | `sha256:cf54ea5549b800f10f6b3d536bd5c2aa8116dc25c5be00a298e39283903b8f39` |
| Legacy DWD consumer | `sha256:7f4a0574be770a18ed80e1b1383b2b280891a5e59cd10f6ec157d08e0e4b824a` |
| Legacy DWS consumer | `sha256:c35fe820f5510e8be7d904847e99e5868a10513605f97dac76612a921d0bfa22` |
| Retained legacy history | `sha256:4fabe7ce8779310c3f1176b7ea388769c00a1d41df386b1f88d509bd57ea3721` |

No canonical package, ADS package, release set, legacy data, consumer pointer, schedule, or source
artifact was deleted, moved, overwritten, or repurposed. `GO_FOR_CUTOVER.json` remains absent.
Repository doctor, compile, 437 tests, and planning synchronization passed from the reviewed P1
runtime after the evidence update.

## Final July Phase B Acceptance

The final input ledger binds `ods-base-202607-v1` at manifest
`sha256:553665772eb9746dcbca3318ae15f1c9b421f351c6e734f8e475ee8309a71d4c` and content
`sha256:91fb3be70e7b80cb22eaa800c00bb267a9a89202b83a14614c31b9d7a2f5d4d1`, canonical catalog
`catalog-772e7c591fd9edb87c94`, and accepted July 31 generation
`generation-20260731-hermes-061a62cfbf797045`. Its logical digest is
`sha256:d1fff8127c55e4a9dd6137d42fabfab9432deeff26c3a561dafe346fd488ff84`; the frozen ledger file is
`sha256:dbfe9ac0b6eb72ad074904861a5673c141169d03772d86ff7ecca8025714cd38`.

| Layer | Package | Manifest SHA-256 | Content digest | Canonical tree SHA-256 |
| --- | --- | --- | --- | --- |
| DWD | `dwd-base-202607-v1` | `sha256:e220b8a60f50112f6d2c4fbae7248c810c7ab5461fc926dcd86990e4260de0fb` | `sha256:98a158a686c24770b3136585e22817612acf68973ccf330dece4acb35bcebeb7` | `sha256:3bfaf116c3655d90c48d7230345ef6ead4bef21788bbdbb878a0b8fc5c3b0b17` |
| DWS | `dws-base-202607-v1` | `sha256:515840db7ddca8364ba8ef5fe8a3379bd09c4e848856704c9ea620599245169e` | `sha256:36d0644f46e6bf5e3426abca3af063fe682a9e6661713c6aa4d39171e8f67ee0` | `sha256:651a519f037d9f40e9ac639f5f2b9440a33d8afa804e6c5457c153d5b4ac287b` |

The first build and an independent rebuild produced the same manifests, content digests, and
canonical trees. The pair was installed without descriptors on OCI, transferred with `READY`
last through attempt `final-202607-v1-20260731T193919Z`, and independently installed and deeply
verified on CN01 and Hermes. All three nodes then returned the digest set above before any pointer
advanced.

All three descriptor pairs advanced from the exact retained-v2 SHA preconditions. The final DWD
and DWS descriptor SHA-256 values are respectively
`sha256:a103432acf484e6089115bd532c0bdeea78113257f91c2b19f16f5048641805e` and
`sha256:72ba2b6022a0bbe1779c7834238b78ffe65c5c8d6eb3bdb12f5a59c3c1a5a7d3`; both cross-bind pair
digest `sha256:7c44f82ab1b2a9a3bfc57ccc6d7aa560bf7d6b836d7f31c28f3b56ec55b53a27`.

CN01 and Hermes rebuilt and replayed their Base-aware catalogs with `candidate_count=0` and
`through_date=2026-07-31`. CN01 selected
`base-lane-history-cn01-20260731-2943321f11e14419` with CURRENT SHA-256
`sha256:c3de1e64a7145f6a99bb5f5fc2e975f3e68fffa1fafe5dcc32ecc2d2c3f135af`; Hermes selected
`base-lane-history-hermes-20260731-d866fa02b44b76da` with CURRENT SHA-256
`sha256:af95b84a2bdac0fa94f61e6c59ab695fce7fd22883bfe3d94e7cbc4cf67f7cd1`. Second runs preserved
both ids and pointer bytes. Both `dual-lane-doctor node` runs passed at deployed revision
`74278d4fefd4b44a678bfa81c3be66aaffa0ed3e`.
