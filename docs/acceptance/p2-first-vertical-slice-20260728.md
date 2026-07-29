# P2 First Vertical-Slice Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Case | `P2-NORMAL-BUY-001` |
| Scope | T-close signal through next-session simulated buy and daily ledger snapshot |
| Governing design | `docs/design/lld/02-backtest-engine.md` |
| Governing work | `P2-ENG-001`, `P2-POLICY-001`, `P2-MATCH-001`, `P2-LEDGER-001`, `P2-AUDIT-001`, `P2-ACCEPT-001` |

## 1. Decision

The first P2 vertical slice passes its bounded acceptance case. The canonical implementation owner
is `src/quanthus/backtest/`: a T-close buy signal can create a next-session order, simulate one
whole-lot fill, produce a balanced ledger entry and closing snapshot, and serialize the complete
result deterministically.

This is implementation progress, not P2 phase acceptance. The policy is explicitly
`synthetic-test-only`, so the result cannot be labeled official and none of its fee or window
values are production decisions.

## 2. Evidence map

| Concern | Evidence |
| --- | --- |
| Policy/profile validation | `src/quanthus/backtest/policy.py`, `tests/unit/test_backtest_policy.py` |
| Event and accounting contracts | `src/quanthus/backtest/contracts.py` |
| Deterministic referee | `src/quanthus/backtest/engine.py`, `tests/unit/test_backtest_engine.py` |
| Synthetic policy | `tests/fixtures/p2/execution-policy.synthetic.v1.json` |
| Hand-calculated oracle | `tests/fixtures/golden/p2_normal_buy.v1.json` |

The policy parser requires one active profile and validates that a two-window profile has exactly
two unique, ordered, non-overlapping windows. Execution of that target mode still fails explicitly;
only the active `close-to-next-open-v1` normal-buy path is implemented.

## 3. Hand reconciliation

| Item | Calculation | Expected |
| --- | --- | ---: |
| Fill price | CNY 10.00 open plus 5 bps, rounded to CNY 0.01 | CNY 10.01 |
| Gross position cost | CNY 10.01 x 100 shares | CNY 1,001.00 |
| Commission | max(0.03%, CNY 5.00 minimum) | CNY 5.00 |
| Ending cash | CNY 10,000.00 - CNY 1,001.00 - CNY 5.00 | CNY 8,994.00 |
| Closing market value | CNY 10.20 x 100 shares | CNY 1,020.00 |
| Total equity | CNY 8,994.00 + CNY 1,020.00 | CNY 10,014.00 |

The monetary postings are cash CNY -1,006.00, position cost CNY 1,001.00, and commission expense
CNY 5.00. Their sum is exactly zero.

## 4. Behavioral checks

- A daily-close signal cannot fill on the same market date.
- A valid run emits ordered signal, order, status, fill, ledger, and snapshot events.
- Repeating identical inputs yields byte-identical output and stable unique event ids.
- A non-whole-lot entry is rejected without changing cash.
- A synthetic policy cannot authorize an official run.
- A structurally valid two-window profile remains an explicit unsupported execution mode.

## 5. Residual boundary

This evidence does not cover production policy freeze, sell or carry transitions, exchange limits,
suspensions, partial fills, corporate actions, external-pool transfers, multiple-candidate ordering,
persistent audit storage, fixed-unit performance reports, notifications, or operator-reported actual
operations. The full P2 acceptance gate remains open until the corpus in LLD 02 is complete.

## 6. Verification

- Focused policy and engine suite: passed 9 tests.
- Ruff on the P2 package and focused tests: passed.
- Mypy on `src/quanthus/backtest`: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 127 tests, environment doctor,
  compilation, and planning synchronization preview.
