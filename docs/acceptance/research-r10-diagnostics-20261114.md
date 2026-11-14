# R10 sealed-evidence diagnostic handoff

Work item: `P5-RESEARCH-R10-DIAG-001` / [#682](https://github.com/rv198-star/Quanthus/issues/682), child of #544. Governing design: [exploration governance](../design/exploration-governance.md). This report records evidence; the only candidate contract is a non-activated draft.

The authorized tranche reads sealed evidence only: original train24 plus seven definite old-May update cases; current guard8/eval24; 19 C2 trial-entry events and13 filled entries. New reference opinions, result-bearing training, market acquisition and capital replay are all zero. Bound source: `7fa3c109614280dc340787abf551076b1e4285c8`. Full private evidence archive SHA256: `ef6387e32fa4f3a46bba9abd5f191abf712e5d8e6090164bcdfe02b6863c02ac`.

## Recorded result

The31-row table retains original identities, clauses/citations, roles, per-row weights and gradient attribution. All1,868 original citations match the sealed HLC packets:1,804 belong to the31 training rows and64 to the separately quarantined UNKNOWN case. Arithmetic at100 sealed pre-step states reconciles to observed checkpoint differences, with maximum cumulative residual below5.0e-16. Each update example has24/7 times the weight of an original training example. No computed state advances an optimizer.

The32-row table preserves original labels, three UNKNOWN cases and exposed development use. Six positives were corrected and six false positives introduced. The materially_narrow clause in all31 training rows exactly matches the whole label, leaving no narrow-TRUE/whole-FALSE training contrast.

The only concrete candidate is an independently supervised, non-compensable, monotone range-width gate. A hash-bound two-case witness shows that preserving all nine training narrow positives requires a threshold above one current false positive's width. This candidate is parked under the unchanged six-FP rejection target. The broader contextual-narrowness hypothesis remains untested; original reader rationales and labels are retained.

The19-row table links exact event_id/anchor to allocation, reservations, orders/fills, exits, ledger entries and settlement. Original decisions.allocated values remain visible. Actual reserved/order/filled counts are13; the other six allocations are rejected for no_empty_unit. All13 closed cash flows match sealed roundtrips. Their exact anchors have no current reference; same-company labels from other anchors are listed separately. Product field naming is a successor consideration.

## Delivery and validation boundary

Private local package: `/Users/william/Documents/Codex/2026-11-14/jie-s/outputs/quanthus-r10-diagnostics/README.md`. Its diagnostic-summary, input manifest, three JSON/CSV tables, original UNKNOWN, minimal witness and single-candidate draft are bound by checkpoint external receipts. The recorder checks declared identities; independent audit owns semantic acceptance.

Original complete CN02/Python3.10 validation was9,363 passed/35 skipped at7eabc0ba43ca349b7ce3242b21f689ea72731130. Runtime source/scripts/tests/build inputs are unchanged through the bound base and this tranche; the original full run used841.91CPU seconds. This tranche has a600-second total CPU ceiling. New metadata receives affected checks, while a new full make check is a named pending-budget gap under local-acceptance-v1; the old run is source provenance, not reuse acceptance for the new commit. This branch stays unmerged.

The clock remains13:49:39-15:49:39UTC on2026-11-14; independent review has a30-minute cap. Diagnostic assets and a precise stop decision are the delivered value. Historical failures, exposure and cumulative costs, B/research v0, P10#679 and existing activation gates remain in force.
