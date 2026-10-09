# P6-R1 bounded recovery and source blocker — 2026-10-09

Owner authorized P6-R1 A/B/C. Baseline main c40d2d4c379789455146c002bb51ec62e470309a. Tracking: P6 #511, P10 #512, existing activation #209, final review #120.

## Status

A engineering implemented, with targeted negative tests. B opt-in daily-core consumer implemented for further qualification but NOT ACCEPTED because P6's exact historical predecessor chain contains an actual identity gap. C NOT RUN, no production deployment, no new activation, no official Run, no changes to old P6 jobs/ledger and no P3 changes. Final Review not due, 0/66 prospective sessions.

## A — source-path/Job control repair

The original official cycle derived a full package-id physical directory while OCI correctly stored the publisher's dated short directory. The new bounded locator validates precise market date, full semantic package id, source manifest SHA, admissible full/short path and no symlink/escape. Historical predecessor references retain exact hashes. Failed/canceled old official Jobs are not requeued with a fresh consumed_at and the same idempotency key; later market sessions cannot skip the unresolved failure. This is source repair, not production adoption.

Physical OCI proof: 2026-09-21, 09-22 and 09-23 immutable short-path manifests exist, and their SHA matches signed sync receipts; the 09-23 legacy package also passes the original deep verifier. The three original official Job failure logs each terminate on the former incorrect full physical path.

## B — versioned daily-core proof and hard block

New opt-in R1 runtime retains old exact strategy, budget, Base V2.1 and correction, frozen expert/entry assets and paper-only authority. The legacy runtime stays unchanged. R1 consumer requires schema2 daily-core-v1, OCI daily-core sync receipt, 7 governed tables, 1d-only unadjusted price_basis=none, deep SHA/READY/physical validation, and a prior-linked daily sequence. The code distinguishes the new source and its activation namespace instead of silently changing the legacy pointer.

Read-only OCI inspection: 2026-10-08 daily-core immutable manifest SHA c15a771f2072b05a376aec36c2a4dbffbcd8d54b0a8b1765dd1a265ed69d9cef, with deep verification PASS, 4,992 daily security bars and 4,992 daily status records. The core chain is 09-24, 09-28, 09-29, 09-30, 10-08 and its 09-24 initial package correctly binds the 09-23 legacy SHA 233d046d7d2300d46572565921089d638c078548368cada3caccd500db6671ee.

B must also traverse the legacy chain back to Base 2026-07-31. 2026-08-06's immutable prior explicitly refers to package dwd-daily-20260805-1e451e441164-4c90bd8 and manifest SHA de26088950e0e0b09c8fafd81f23163dcd71d517d7dae3c4dbbae3ea8d71161d, whereas OCI has a DIFFERENT 08-05 package ...1cf95a1. Earlier 08-04 prior 08-03 also mismatches actual present revision. The P6-R1 chain correctly fails closed. P10 #512 owns source restoration/provenance; P6 must not collect data, rename another revision or relax digest/PIT.

## C — bounded future activation gate, NOT RUN

The September activation decision remains immutable in the old namespace. R1 controller has a separate activation-r1 path and runtime-scoped Job accounting, but the revised exact human mandate has NOT been issued, and source B has not qualified. Before any deployment, require the actual accepted exact source closure, independent tests/review, pinned production revision, genuine disabled rehearsal, verified read-only legacy/core mounts, rollback and formal successor activation authority. The earliest R1 official sample must be one complete, deeply verifiable future market date STRICTLY AFTER its activation market date, sealed before the next-session deadline. Historic 09-21/22/23 failed Jobs and the 10-08 package are never R1 official samples. First official cycle must pass independent cold reproduction before progress can become 1/66. No notification/broker/live-capital or sealed-partition expansion.

## Verification and next gate

See focused pytest and lint command receipts in the work issue. The standard make check does not count as passing if its Python environment lacks pytest. Resume only after P10 #512 exact closure or a separately approved qualified PIT-safe source; then independently accept B before C.
