# Issue 45 First Scheduled Dual-Lane Cycle

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Market date | 2026-07-30 |
| Evidence status | completed after operator recovery; not a clean unattended qualification session |
| Cycle revision | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` |
| Corrective revision | `7c99724c641286dbff57be6479fdcafabfd13d3f` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-first-scheduled-cycle-a9a4a85` |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |

## 1. Verdict

The first scheduled shadow cycle produced both lane-local 2026-07-30 DWD/DWS candidates and one
complete OCI target release set. The 16:30 epoch and both 19:50 candidate services started from
their timers; OCI's 20:00 arbitration then pulled both candidates, selected CN01 under the frozen
primary rule, and published canonical acceptance, catalog, operational ADS, release-set `READY`,
publication/CAS evidence, and the target-only `release-set_CURRENT.json`.

The cycle required operator recovery before either candidate could build. It is accepted as
recovery and end-to-end publication evidence, not as a clean unattended multi-session sample. The
legacy OCI publisher remained enabled and authoritative, no consumer moved to the target pointer,
and no data was deleted.

## 2. ODS Pointer Incident And Fix

Both nodes had complete market releases before the candidate cutoff. A later
`calendar-daily-events` release nevertheless replaced the shared date-local `daily/CURRENT.json`.
The candidate contract correctly rejected that descriptor because it lacked the required market
daily artifacts. The services stayed inside their bounded ten-minute retry window.

The displaced calendar descriptor was retained on each node, then `CURRENT.json` was atomically
restored to the already verified market release. No ODS release was moved, edited, or deleted. The
waiting services resumed automatically and wrote candidates at 19:52. Corrective revision
`7c99724c` restricts the pointer writer to `market-daily-core`; calendar releases remain immutable
evidence but cannot replace the market candidate selection. The focused lane/dual-lane suite passed
81 tests and the repository suite passed 426 tests before deployment. All three nodes now run the
corrective revision and pass their applicable deployment doctors.

## 3. Candidate Evidence

| Property | CN01 primary | Hermes standby |
| --- | --- | --- |
| ODS manifest | `sha256:32db7afaba979b3356fafae8390d7507a5d1b98b90f873112cb8a1ddff1282f8` | `sha256:293afec851a0a9702d1c68ec7da3473162b33114316cfb0e695b44a34ccc14e9` |
| Candidate manifest | `sha256:23f4ef56d7e9a3f525511730a4c24290aceb858dbf2efdc08f149559a962b9f2` | `sha256:b572698d92a87a9e1c86e02a13187d9e812a6b5eb076a52e0cde63e6db4e38a2` |
| Business digest | `sha256:2709e194a8a11daf028d559e47e6a0123fb4dfa5f3d480eb4a799753d6ddee16` | same |
| Hard gates | all passed | all passed |
| Node doctor | passed | passed |

Both candidates bind epoch `epoch-20260730-a9a4a85721d8-f694ea249073` and epoch manifest
`sha256:832bf0e566db61e48a93cd571ca80d025db3e53f5dc2a702bac9e8f899c0fb3a`.
Their candidate content digests differ because lane and ODS lineage differ; their normalized
business content is identical.

## 4. OCI Publication

| Artifact | Accepted identity |
| --- | --- |
| Selected generation | `generation-20260730-cn01-bc696f8e9013e3a7` |
| Operational ADS manifest | `sha256:036bd6522ee2af565ee862adab8dd5ff17f918a943a856c3cc6d6bae7a103070` |
| Release set | `release-set-20260730-bc696f8e9013e3a7` |
| Release-set manifest | `sha256:f6f120a814a47ba5385af00df74dfe0079ae426c69d2e9995fee50a1fa4454d1` |
| Target pointer before | `5efb31b09bf7e83f94445abc577857f4f01a29b73aec7bb96360f6be52d98bfb` |
| Target pointer after | `87c5ecc2c29fbeefaff1cc29711d15c6c69fa59daed8b691515ffd05b878d8d3` |

The legacy DWD and DWS pointer hashes remained respectively
`7f4a0574be770a18ed80e1b1383b2b280891a5e59cd10f6ec157d08e0e4b824a` and
`c35fe820f5510e8be7d904847e99e5868a10513605f97dac76612a921d0bfa22`
during target arbitration. The evidence root retains copied manifests, READY markers, pointer
bytes, all four service journals, ODS recovery hashes, a machine-readable summary, and a checksum
ledger.

## 5. Residual Gate

At least one later open session must complete without operator repair before the scheduled path is
called unattended-qualified. Independent restore, failure drills, multi-session comparison,
consumer migration, writer fencing, and `GO_FOR_CUTOVER` remain open. This record authorizes no
cleanup, live consumer switch, or legacy timer change.
