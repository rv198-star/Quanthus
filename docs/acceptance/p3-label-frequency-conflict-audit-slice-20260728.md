# P3 Label Frequency and Conflict Audit Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Registered-result frequency, unknown/not-applicable coverage, and card-level conflict audit |
| Governing design | `docs/design/lld/03-label-feature-system.md` |
| Governing work | `P3-LABEL-003` |
| Dependency evidence | `docs/acceptance/p3-label-card-contract-slice-20260728.md` |

## 1. Decision

`P3-LABEL-003` passes its bounded infrastructure slice. `src/quanthus/label_audit.py` consumes a
strict LabelRegistry plus already-produced LabelResults and emits a canonical, digest-bearing
report of coverage, value frequency, expected-range status, and invalid card coexistence.

This decision defines no production label, expected market frequency, threshold, 12-state value,
or evaluator. Every expected range and relationship comes from the supplied versioned LabelCard;
the synthetic fixtures are contract evidence only.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Applied/unknown/not-applicable reconciliation and value counts | `src/quanthus/label_audit.py`, `tests/unit/test_label_audit.py` |
| Card digest, dimension, output, reason, and sample-scope rebinding | `tests/unit/test_label_audit.py` |
| Primary-stage single value and declared mutual exclusion | `tests/unit/test_label_audit.py` |
| Active blocking versus shadow warning semantics | `tests/unit/test_label_audit.py` |
| PIT context, duplicate rejection, input-order determinism, and digest stability | `tests/unit/test_label_audit.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- Each exact label/version/security/snapshot/cutoff tuple may appear once. A security/snapshot id
  cannot carry two knowledge cutoffs in one audit.
- Every result must resolve to its registered card and match card digest, dimension, declared
  applied value, reason code, and expected-frequency sample scope.
- Per-card totals exactly decompose into applied, unknown, and not-applicable results. Applied
  values receive deterministic counts and rates use a fixed twelve-decimal representation.
- Expected frequency means the card-level applied rate because the current LabelCard declares one
  range per card, not per output value. No hidden alternative interpretation is introduced.
- Two applied primary-stage cards in one PIT context conflict. Two applied cards also conflict when
  either card declares the other mutually exclusive; undeclared relationship targets fail closed.
- Active range failures and active-active conflicts block the report. Shadow range failures or a
  conflict involving shadow evidence remain warnings and do not block the active slice.
- Result input order cannot change report ordering, serialization, status, or digest.

## 4. Residual boundary

`P3-LABEL-002` still owns concrete tradability/stage/risk cards, evaluator formulas, thresholds,
golden cases, and evidence. `P3-LABEL-004` still owns human review and adjudication. The draft
twelve-state vocabulary remains non-governing and cannot be inferred from this audit contract.

## 5. Verification

- Focused label audit suite: passed 10 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 240 tests, plus doctor, compile, and planning
  preview checks.
