# P3 Shadow Divergence Contract Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Same-review shadow identity validation and deterministic output-divergence records |
| Governing design | `docs/design/lld/04-experts-aggregator.md`, `docs/design/lld/10-monitoring-attribution.md` |
| Governing work | `P3-SHADOW-001` comparison contract only |
| Dependency evidence | `docs/acceptance/p2-round-trip-observability-slice-20260728.md` |

## 1. Decision

The comparison-contract portion of `P3-SHADOW-001` passes. `src/quanthus/shadow_divergence.py`
binds active or rejected and shadow `DecisionEvent` values to one exact review and produces
canonical, digest-bearing divergence records for already-produced decisions.

This decision does not claim that P3 can yet produce a concrete shadow decision. It approves no
expert, score weighting, action threshold, state label, order, fill, or persistence side effect;
every comparison record has `execution_authorized=false`.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Baseline/shadow disposition separation | `src/quanthus/shadow_divergence.py`, `tests/unit/test_shadow_divergence.py` |
| Same run/snapshot/time/profile/window/security/unit/lifecycle review identity | `tests/unit/test_shadow_divergence.py` |
| Action, score, expert, veto, constraint, reason, and policy deltas | `tests/unit/test_shadow_divergence.py` |
| Canonical batch ordering, nested mapping stability, and stable digest | `tests/unit/test_shadow_divergence.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- The baseline must be accepted or rejected; every comparison target must have shadow disposition,
  no Signal id, and no linked order id.
- Both decisions must share run, snapshot, decision time, profile/window, security, unit, and
  lifecycle stage. A mismatched review fails closed rather than becoming a cross-context delta.
- Output changes cover proposed/final action, aggregate score, expert payloads, vetoes, constraints,
  and reason codes. Policy-version changes are reported separately from behavior divergence.
- One baseline may compare with multiple uniquely identified shadows. Input order and nested mapping
  order cannot change record order, comparison id, serialization, or digest.
- Equal decision outputs are not labeled divergent merely because one record is shadow-only.
- The comparison is pure: it creates no `DecisionEvent`, Signal, OrderIntent, Order, Fill,
  notification, lifecycle transition, or persisted file.

## 4. Residual boundary

Concrete shadow decision production remains open under `P3-SHADOW-001` and depends on
`P3-EXP-002` plus `P3-AGG-002`. P2 continues to own immutable `DecisionEvent` persistence and to
forbid execution links on shadow records. P6 still owns promotion lifecycle and trigger-count
gates; this comparison contract does not promote anything.

## 5. Verification

- Focused shadow divergence suite: passed 9 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 229 tests, plus doctor, compile, and planning
  preview checks.
