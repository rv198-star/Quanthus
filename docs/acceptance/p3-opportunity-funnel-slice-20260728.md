# P3 Opportunity Funnel Reconciliation Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Fixed-stage source-event and outcome-link reconciliation |
| Governing design | `docs/design/lld/10-monitoring-attribution.md` |
| Governing work | `P3-FUNNEL-001` reconciliation contract only |
| Dependency evidence | `docs/acceptance/p2-round-trip-observability-slice-20260728.md` |

## 1. Decision

The reconciliation-contract portion of `P3-FUNNEL-001` passes. The implementation requires exact
source-event sets for candidate-to-score, score-to-veto, entry-to-add, and hold-to-exit, then
classifies every source exactly once without granting execution authority.

This decision does not claim that concrete P3 rules can yet populate the funnel. It approves no
feature, label, expert, threshold, action policy, order, fill, position transition, or notification;
every report declares `execution_authorized=false`.

## 2. Accepted behavior

- Every fixed stage is declared, including an explicit empty input set when no event entered it.
- The observed input-event ids must exactly match each declared source set. Missing, extra, or
  duplicate observations fail publication rather than changing a denominator silently.
- Advanced, stopped, and unavailable classifications partition each stage input count. Realized,
  diagnostic, and pending outcome states independently partition the same count.
- An advanced observation owns one output event. If that output is present as another observation's
  input, it must enter the next fixed stage and retain the same opportunity identity.
- Realized and diagnostic states require an explicit outcome reference. Pending states cannot imply
  one.
- Report periods are inclusive and reject observations outside their bounds. Input permutations do
  not change report identity, canonical bytes, summaries, or digest.
- The module is pure analysis and imports no execution engine, signal, order, fill, persistence, or
  notification authority.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| Typed fixed stages, dispositions, and outcome links | `src/quanthus/opportunity_funnel.py` |
| Exact source-set and per-stage count reconciliation | `tests/unit/test_opportunity_funnel.py` |
| Cross-stage identity and deterministic serialization | `tests/unit/test_opportunity_funnel.py` |
| Non-executing design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 4. Residual boundary

Concrete `DecisionEvent` production remains blocked on `P3-LABEL-002`, `P3-EXP-002`, and
`P3-AGG-002`. Historical outcome population additionally depends on the strategy replay and
diagnostic-ledger joins. This slice does not close end-to-end `P3-FUNNEL-001`, produce a dashboard,
or authorize monetary attribution.

## 5. Verification

- Focused opportunity-funnel suite: passed 9 tests.
- Focused Ruff, formatting, and strict mypy: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 289 tests, plus doctor, compile,
  and planning preview checks.
