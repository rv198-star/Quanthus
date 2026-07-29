# P3 Concentration Capacity Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Frozen static-industry and daily new-unit entry-capacity checks |
| Governing design | `docs/design/lld/05-position-execution.md`, `docs/design/lld/01-data-foundation.md` |
| Governing work | `P3-RISK-001` |
| Governing policy | `config/p0-policy.v1.json` |
| Dependency evidence | `docs/acceptance/p3-position-lifecycle-slice-20260728.md` |

## 1. Decision

`P3-RISK-001` passes its bounded entry-capacity slice. `src/quanthus/concentration.py` reads the
frozen P0 limits, validates active lifecycle and static-industry lineage evidence, and emits a
deterministic allow/reject decision before any execution artifact exists.

This decision freezes no new number. The maximum of two active units per static level-one industry
and two new units per market date already exist in `config/p0-policy.v1.json` and approved design.
The output cannot allocate a unit, reserve funds, start a diagnostic replay, or create execution.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Frozen policy values and concentration-only industry use | `config/p0-policy.v1.json`, `src/quanthus/concentration.py` |
| Trial/Dynamic/Exiting active-unit evidence | `tests/unit/test_concentration.py` |
| Industry cap and `industry_cap` reason | `tests/unit/test_concentration.py` |
| Daily reservation cap and `daily_open_cap` reason | `tests/unit/test_concentration.py` |
| Mixed mapping, occupied/reserved unit, duplicate, and date rejection | `tests/unit/test_concentration.py` |
| Input-order-independent output and digest | `tests/unit/test_concentration.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- Capacity reads positive `max_units_per_level1_industry=2` and `max_new_units_per_day=2` from the
  exact frozen P0 policy version and records its digest.
- Static industry evidence is accepted only with explicit non-PIT concentration-only usage,
  source/lineage identity, and one mapping version across the candidate and active exposures.
- Only Trial, Dynamic, and Exiting lifecycle stages count as active positioned units. The candidate
  unit must be absent from both active positions and already accepted entry reservations.
- Two existing active units in the candidate's industry reject with `industry_cap`. Two accepted
  new-unit reservations on the market date reject with `daily_open_cap`.
- If both limits are exhausted, both reasons remain visible in fixed industry-then-daily order.
- Duplicate unit/reservation evidence, mixed dates, mixed mapping versions, policy misuse, or
  non-positive limits fail closed.
- Permuting active exposures or daily reservations cannot change the decision, serialization, or
  digest; every decision has `execution_authorized=false`.

## 4. Residual boundary

The accepted result is a capacity check, not an allocation or execution reservation. Candidate
ranking remains P2, unit assignment and trial/add sizing remain `P3-POLICY-001/P3-POS-002`, and
diagnostic replay remains the accepted isolated P2 path. No market-wide position maximum is
implemented because no versioned value has been frozen. Static industry membership remains
invalid for sector experts and filters.

## 5. Verification

- Focused concentration suite: passed 10 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 251 tests, plus doctor, compile, and planning
  preview checks.
