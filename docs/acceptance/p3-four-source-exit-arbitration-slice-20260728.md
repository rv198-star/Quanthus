# P3 Four-Source Exit Arbitration Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Exact frozen exit-source completeness, same-review arbitration, and unavailable-evidence handling |
| Governing design | `docs/design/lld/05-position-execution.md`, `docs/design/lld/04-experts-aggregator.md` |
| Governing work | `P3-EXIT-001` |
| Governing policy | `config/p0-policy.v1.json` |
| Dependency evidence | `docs/acceptance/p3-governance-aggregation-slice-20260728.md`, `docs/acceptance/p3-position-lifecycle-slice-20260728.md` |

## 1. Decision

`P3-EXIT-001` passes its bounded arbitration slice. `src/quanthus/exit_arbitration.py` requires and
orders the four exact frozen P0 source evaluations, binds them to one positioned review, and emits
a canonical exit request or explicit hold result with complete trigger/unavailable evidence.

This decision approves no concrete trigger evaluator or numeric parameter. It does not define a
structural-break rule, stop threshold, deterioration count, maximum holding period, minimum hold,
sale eligibility, or cooldown, and it cannot create execution.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Exact four-source set from frozen P0 policy | `config/p0-policy.v1.json`, `src/quanthus/exit_arbitration.py` |
| Source-specific governance/constraint/review/calendar evidence | `tests/unit/test_exit_arbitration.py` |
| Same unit/opportunity/security/snapshot/cutoff/lifecycle review | `tests/unit/test_exit_arbitration.py` |
| Multi-trigger retention in policy order | `tests/unit/test_exit_arbitration.py` |
| Missing-data unavailable hold and no synthetic structural break | `tests/unit/test_exit_arbitration.py` |
| Input-order-independent output and digest | `tests/unit/test_exit_arbitration.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- The loaded P0 policy must contain structural break, constraint stop, consecutive deterioration,
  and maximum holding period exactly once. Every arbitration supplies all four evaluations.
- Available structural, constraint, deterioration, and holding-period evaluations respectively
  require governance, constraint, review, and calendar evidence.
- All evaluations share unit, opportunity, security, snapshot, knowledge cutoff, and a Trial or
  Dynamic lifecycle stage. Duplicates, omissions, or cross-review mixtures fail closed.
- Any triggered source requests exit. Simultaneous sources all remain visible in frozen policy
  order rather than being collapsed to one undocumented winner.
- With no trigger, all-clear produces hold. Any unavailable source produces
  hold-with-unavailable-evidence plus `alert_required=true`; it never implies structural damage.
- A triggered structural-break evaluation containing missing-data evidence fails with stable
  `missing_data_structural_break` instead of creating a sell reason.
- Input permutation cannot change result ordering, serialization, or digest; every result has
  `execution_authorized=false`.

## 4. Residual boundary

Concrete trigger evaluators remain in `P3-EXP-002/P3-POS-002/P3-POS-003` and require versioned
features, reviews, and position policy. This arbitration result is decision evidence only. Moving
from Trial/Dynamic to Exiting still requires `P3-POS-001` decision evidence, while creation,
matching, carrying, filling, and settlement remain in the accepted P2 engine.

## 5. Verification

- Focused exit arbitration suite: passed 10 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 262 tests, plus doctor, compile, and planning
  preview checks.
