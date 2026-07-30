# Issue 45 Historical DWD/DWS Completion Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | accepted for copy-only historical completion through 2026-07-30 |
| Deployed verification revision | `1569faecbcd02f186f83a2fb884678ce6cb6bb10` |
| History-bundle builder revision | `5cfdf87e6a386da8ab572bb0d6f60f414ab78842` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-historical-dwd-dws-completion-1569fae` |
| Evidence summary SHA-256 | `0ce2c371e43b5cf6bb2431834137d27497743d8001a8f8ffa76fd060f8319e9e` |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |

## 1. Verdict

The accepted OCI P1 DWD history and its governed DWS projection now exist as the same immutable
sealed base on CN01 and Hermes. Each node also has a verified lane-local logical catalog joining
that base to four immutable daily candidates for 2026-07-27 through 2026-07-30. This completes the
declared copy-only historical DWD/DWS migration boundary through 2026-07-30.

The operation did not concatenate Parquet files or append rows to the seed. `physical_merge=false`
in both catalogs: the base and daily candidates remain separate immutable artifacts, while
`CURRENT.json` selects a new catalog envelope. The shared pre-lane seed is continuity and recovery
evidence, not independent acquisition evidence.

This is not production cutover, catalog-native ADS publication, or an unattended multi-session
qualification. The legacy OCI publisher and all target shadow timers remain enabled, and no
consumer was moved to the target release-set pointer.

## 2. Common Sealed Base

| Property | Accepted value |
| --- | --- |
| Bundle | `dual-lane-history-base-p1-20260725-v1` |
| DWD range | 1990-12-19 through 2026-07-24 |
| DWS range / rows | 2011-01-04 through 2026-07-24 / 3,778 |
| Content digest | `sha256:d894969cbb4394ca7d801ef49b6f042ea5515811ffb0b9ea15494dbe25c2785b` |
| Manifest SHA-256 | `1fa60f4c89aed7c8f80734e8d415b60a564ec6ca32fa8c07653bfd442a249f05` |
| READY file SHA-256 | `2e7eaaeadd6aad712168c1345a5f75459df8aff4ae785c8c7a10d45df6484a33` |
| Ordinary-file bytes on each node | 392,627,409 |
| Relative-path plus file-content tree SHA-256 | `fa1cf2fae93e249109de302f0ae840e1c86a2fc68791b7dc31efdecbd521cfe1` |

OCI, CN01, and Hermes produced the same file count, total ordinary-file bytes, manifest and READY
hashes, and deterministic whole-tree hash. This stronger comparison excludes a false equality
caused by filesystem-specific directory-size accounting. Both candidate nodes retain the original
write-only ingress copy as well as the root-owned sealed copy; promotion moved or deleted nothing.

## 3. Logical Catalogs

| Lane | Catalog | Manifest SHA-256 | Content digest | Daily partitions |
| --- | --- | --- | --- | --- |
| CN01 | `lane-history-cn01-20260730-738314a506f37fc7` | `516e7e61c102d74f482fa7dd69c3c3eea3e0719ff2ef2d418ddd42619b35092d` | `sha256:738314a506f37fc7b892b6ca26ba03ff492231f3a455308727a73f2404672abb` | 2026-07-27, 28, 29, 30 |
| Hermes | `lane-history-hermes-20260730-51b514b561f4f67a` | `67d391badab826c970cf9113c9b1b1f9d2c3bc3de88a62ba1bb3fa1c29946e0d` | `sha256:51b514b561f4f67a9701e5f032eb64486eff1e0e86cc2e638903a9398e99df71` | 2026-07-27, 28, 29, 30 |

Both catalog verifiers accepted exactly four candidates and a through-date of 2026-07-30. The
first candidate binds the historical base revision and META digest. Every successor binds the
canonical DWD parent sealed in its epoch and the parent date equals the preceding catalog date.
Hermes correctly follows the accepted CN01 canonical parents after 2026-07-27; it does not invent a
same-lane lineage. The superseded CN01 catalog remains retained beside the new envelope.

## 4. Deployment And Current Data

All three checkouts and configured revisions equal `1569fae`; OCI `dual-lane-doctor oci` and both
node doctors passed policy digest
`sha256:5869be346f9017f1af73375935b5ea07e0cc2e1099aa28db3d9e4741bea922ea`.
Installed target service and timer files matched their deployed repository copies byte-for-byte.

| Node | Collection / legacy state | Target state | Last relevant result |
| --- | --- | --- | --- |
| OCI | legacy timer enabled and active | epoch and arbitration timers enabled and active | epoch and arbitration `success` |
| CN01 | five ODS timers enabled; market daily active on schedule | candidate timer enabled and active | market daily and candidate `success` |
| Hermes | five ODS timers enabled; market daily active on schedule | candidate timer enabled and active | market daily and candidate `success` |

OCI's target-only release set remains
`release-set-20260730-bc696f8e9013e3a7`, manifest
`sha256:f6f120a814a47ba5385af00df74dfe0079ae426c69d2e9995fee50a1fa4454d1`.
The latest portable recent-research ADS remains
`strategy-replay-recent-research-7c99724-v3`, manifest
`sha256:d4b948b4acf58f518ec604e87876af17130d329ea3fd4cead9deacbedad2fc5b`;
its accepted detailed scope is recorded separately in
`docs/acceptance/issue-45-2026-research-ads-refresh-20260730.md`.

The legacy OCI DWD and DWS pointers both name market date 2026-07-30 and retain SHA-256 values
`7f4a0574be770a18ed80e1b1383b2b280891a5e59cd10f6ec157d08e0e4b824a` and
`c35fe820f5510e8be7d904847e99e5868a10513605f97dac76612a921d0bfa22`.
Every stage of that legacy run succeeded and its completion email was sent, but the Hermes handoff
was not SLA-qualified. The run therefore deliberately ended `succeeded_unqualified` with exit code
4 even though it published both pointers. This is not recorded as a clean qualified legacy cycle.

## 5. Preservation And Residual Gate

No ingress bundle, sealed base, old catalog, candidate, epoch, failed-attempt evidence, legacy
DWD/DWS, ADS, pointer, or timer was removed. No `GO_FOR_CUTOVER.json` exists below the target tree,
and the policy still declares `delete_enabled=false`.

The two catalogs are point-in-time envelopes through 2026-07-30. Later daily candidates require a
new immutable catalog envelope until catalog-native publication is delivered. A later clean
unattended session, independent restore and failure drills, full candidate-family qualification,
consumer migration, writer fencing, retention decisions, and explicit `GO_FOR_CUTOVER` remain open
under Issue #45.
