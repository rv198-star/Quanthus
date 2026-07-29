# P2 Candidate-Ordering Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Case | `P2-CANDIDATE-TIE-001` |
| Scope | Deterministic ordering of same-snapshot buy candidates |
| Governing design | `docs/design/lld/02-backtest-engine.md` |
| Governing work | `P2-ENG-001`, `P2-AUDIT-001`, `P2-ACCEPT-001` |

## 1. Decision

The fourth P2 slice passes. Candidate signals now carry a finite decimal `aggregate_score` and are
sorted by score descending, then security code ascending. Reversing the input sequence produces
the same ordered signal ids and security codes.

The ranking contract fails before output when a batch mixes side, snapshot, market date, execution
profile, or window, or repeats a signal id or security code. This prevents input collection order
from becoming hidden execution policy.

## 2. Boundary

P2 owns deterministic candidate order because order processing and available-capital outcomes must
be replayable. P3 still owns industry caps, the daily new-unit limit, position-state eligibility,
and assignment of ranked candidates to empty capital units. This slice does not pre-implement
those strategy decisions.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| Signal score and batch invariants | `src/quanthus/backtest/contracts.py` |
| Hand-calculated tie oracle | `tests/fixtures/golden/p2_candidate_tie.v1.json` |
| Permutation and duplicate tests | `tests/unit/test_backtest_engine.py` |

## 4. Verification

- Focused policy and engine suite: passed 17 tests.
- Ruff and mypy on the P2 package and focused tests: passed.
- Full repository check: passed 140 tests.
