# Issue 45 Daily Lane-History Catalog Automation

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | deployment and manual idempotency accepted; first success-triggered daily cycle pending |
| Deployed revision | `315c68a216fbcb95d449123788befed674a8fdcf` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-daily-history-catalog-315c68a` |
| Evidence summary SHA-256 | `c7614d72c530e75ffc6f92b2a2f7334b7c125354489569048e961c64161c3ad2` |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |

## 1. Verdict

CN01 and Hermes now load an event-driven daily lane-history refresh. A successful
`quanthus-dual-lane-candidate.service` invocation starts the independent static
`quanthus-dual-lane-history-catalog.service`; no wall-clock catalog timer was added. The service
takes a catalog-local lock, builds or reuses one immutable logical catalog, verifies its complete
base-to-candidate chain, and atomically advances only that lane's catalog `CURRENT`.

The wiring, exact installed unit bytes, node doctors, and a real idempotent refresh on each node
passed. The next scheduled candidate has not yet run under this revision, so this record does not
claim observation of the first automatic `OnSuccess` invocation.

## 2. Runtime Evidence

All three deployed checkouts and configured revisions equal `315c68a`. OCI and both node doctors
passed policy digest
`sha256:5869be346f9017f1af73375935b5ea07e0cc2e1099aa28db3d9e4741bea922ea`.
The candidate timers remained enabled and active on both nodes; the catalog service correctly
reported `static`, because it is started by the candidate unit rather than enabled independently.

| Node | Loaded `OnSuccess` | Catalog service result | Through date |
| --- | --- | --- | --- |
| CN01 | `quanthus-dual-lane-history-catalog.service` | `success`, exit 0 | 2026-07-30 |
| Hermes | `quanthus-dual-lane-history-catalog.service` | `success`, exit 0 | 2026-07-30 |

The installed candidate unit SHA-256 was
`abb1372c6fa7ad5b4b83f66f31800a8759c067848a3c8cb379bb7136894bbf4c` on both nodes. The installed
catalog unit SHA-256 was
`06af7995bf14e8394448a47f8c5e06ce740abd1edf36b7e3ad5b713632d23675` on both nodes. Each matched
its deployed repository copy, and each node's native `systemd-analyze verify` accepted both units.

## 3. Idempotency And Failure Boundary

The manual service run used the already accepted candidate set through 2026-07-30. CN01's catalog
pointer SHA-256 remained
`4f0c964aea3343242df30a646df98df769205f11176647d49a568232d2d692a3` before and after the refresh;
Hermes remained `0b325015a0832ba005570c8427d1c472e605a9ee3f1e66df840c1bb9bfc0978c`.
No duplicate catalog was created and no existing candidate or base bytes changed.

Catalog failure is independently alerted by `OnFailure`. It does not revoke a sealed candidate,
does not block OCI from evaluating that candidate, and grants no canonical, ADS, release-set, or
consumer publication authority to either lane.

Repository verification passed 431 tests, including the candidate-success wiring contract and a
repeat-build test that preserves both immutable catalog bytes and pointer bytes for unchanged
inputs.

## 4. Residual Gate

The first real candidate success under `315c68a` must still show the catalog service invocation and
a `through_date` equal to that new candidate date. This observation belongs to the next scheduled
cycle evidence and is not inferred from the loaded systemd relationship.

No physical monthly, quarterly, or annual compaction was added. No ODS, DWD/DWS, history base,
candidate, catalog, ADS, pointer, timer, log, or failed evidence was deleted or repurposed. Consumer
cutover and `GO_FOR_CUTOVER` remain blocked under Issue #45.
