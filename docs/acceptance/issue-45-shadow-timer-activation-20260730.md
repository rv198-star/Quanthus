# Issue 45 Dual-Lane Shadow Timer Activation

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | shadow timers activated; first scheduled cycle pending |
| Activation boundary | bounded shadow operation only |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |
| Deployed revision | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` |
| Governing design | `docs/architecture/adr-0005-dual-lane-canonical-and-ads-publication.md` |

## 1. Scope

This record covers activation of the four systemd timers that exercise the approved dual-lane
daily path: OCI epoch publication, one candidate build on each acquisition node, and OCI
arbitration. It follows the completed manual rehearsal and portable ADS deployment evidence.

This is not production cutover or first-cycle acceptance. The legacy OCI full-ODS pull/build timer
remains enabled and authoritative, consumers remain on their existing pointers, target publication
remains below its separate root, and no existing data or evidence was deleted.

## 2. Preflight

OCI, CN01, and Hermes all ran the deployed `dual-lane-doctor` successfully against policy digest
`sha256:5869be346f9017f1af73375935b5ea07e0cc2e1099aa28db3d9e4741bea922ea`. All three nodes were on
revision `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` with the expected dedicated runtimes and role
bindings.

The installed candidate timer and service on both producer nodes matched their deployed repository
copies. The installed OCI epoch and arbitration timers and services also matched the deployed
repository copies. CN01 was bound to its fixed-primary ODS release root and Hermes to its standby
ODS release root.

Hermes host-loopback listeners `127.0.0.1:2222` and `127.0.0.1:2223` were not accepted as evidence
by themselves. The read-only schedule command returned a complete report, the management channel
executed remote commands, and the Hermes node doctor passed before activation.

## 3. Activated Timers

| Node | Timer | Activated at UTC | First scheduled trigger |
| --- | --- | --- | --- |
| OCI | `quanthus-dual-lane-epoch.timer` | 2026-07-30 07:47:17 | 16:30 Asia/Shanghai |
| CN01 | `quanthus-dual-lane-candidate.timer` | 2026-07-30 07:46:50 | 19:50 Asia/Shanghai |
| Hermes | `quanthus-dual-lane-candidate.timer` | 2026-07-30 07:46:24 | 19:50 Asia/Shanghai |
| OCI | `quanthus-dual-lane-arbitration.timer` | 2026-07-30 07:47:17 | 20:00 Asia/Shanghai |

All four units reported `enabled`, `active`, and `waiting` after activation. Their timers declare
`Persistent=false`, so enabling them did not replay a missed historical trigger. The corresponding
oneshot services remained inactive before their first scheduled invocation.

CN01 and Hermes ODS collection timers remained enabled. At activation, the next market-daily ODS
jobs preceded the 19:50 candidate cutoff, and both candidate services retained their configured
input wait rather than treating an absent ODS release as empty input.

## 4. Unchanged Authority And Guards

OCI's `quanthus-t-close-plus-5h.timer` remained enabled and active. The shadow activation did not
disable, restart, or repoint the legacy publisher. No `GO_FOR_CUTOVER.json` existed below the target
root.

The consumer-visible legacy pointer checksums at post-activation verification were:

| Pointer | SHA-256 |
| --- | --- |
| `daily_market_CURRENT.json` | `ae38e0338b35a678969a1839e80ddadafcbde9696af0b65117aa7fa9f6f5bf7d` |
| `daily_market_summary_CURRENT.json` | `f73caef44abecabf8185bc495066b740b2ba6215652b0822522ab927dfbd112e` |

The existing target-only `release-set_CURRENT.json` remained at
`5efb31b09bf7e83f94445abc577857f4f01a29b73aec7bb96360f6be52d98bfb`. Its existence is rehearsal
evidence and does not make it a consumer authority.

## 5. Pending Acceptance

The first scheduled 2026-07-30 cycle remains pending. It must separately prove:

- exact epoch publication to both nodes at or after 16:30;
- complete local ODS and one independently valid DWD/DWS candidate from each node;
- OCI pull, lane-local gates, frozen primary/standby arbitration, operational ADS, and atomic target
  release-set publication;
- unchanged legacy consumer pointers during target publication; and
- durable service journals, manifests, reason codes, alerts, and resource evidence.

Timer activation alone does not close multi-session qualification, failure drills, restore
evidence, consumer migration, writer fencing, or the production cutover gate.

The machine-readable activation summary is retained at
`/home/ubuntu/data/quanthus/acceptance/issue-45-shadow-timer-activation-a9a4a85/activation-summary.json`.
