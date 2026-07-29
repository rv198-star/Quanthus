# P2 Carried-Sell and Unit-Settlement Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Case | `P2-CARRIED-SELL-001` |
| Scope | Full-position sell carried until tradable, then filled and settled against a finite pool |
| Governing design | `docs/design/lld/02-backtest-engine.md` |
| Governing work | `P2-POLICY-001`, `P2-MATCH-001`, `P2-ORDER-001`, `P2-LEDGER-001`, `P2-ACCEPT-001` |

## 1. Decision

The third P2 slice passes its bounded acceptance case. One order is created from the sell signal,
carried once per supplied market session while untradable, and filled at the first executable open.
No retry creates a duplicate order.

| Session evidence | Transition | Mark behavior |
| --- | --- | --- |
| Sell blocked at limit-down | `created_to_carried` | Use the observed session close |
| Suspended with no bar | `carried_to_carried` | Carry the prior sourced mark |
| Executable open | `carried_to_filled` | Fill, release position, and close the unit |

Sell completion emits a balanced trade ledger, an explicit unit-settlement event, an external-pool
transfer ledger when money moves, and a zero-position daily snapshot. A separate empty-pool case
proves that losses are not replenished from invented funding.

## 2. Hand reconciliation

| Item | Calculation | Expected |
| --- | --- | ---: |
| Fill price | CNY 10.20 less 5 bps, rounded to CNY 0.01 | CNY 10.19 |
| Gross proceeds | CNY 10.19 x 100 shares | CNY 1,019.00 |
| Commission | max(0.03%, CNY 5.00 minimum) | CNY 5.00 |
| Stamp duty | 0.05% of gross | CNY 0.51 |
| Transfer fee | 0.001% of gross | CNY 0.01 |
| Net proceeds | gross less all sell fees | CNY 1,013.48 |
| Net realized PnL | gross less CNY 1,001.00 position cost and sell fees | CNY 12.48 |
| Cash before settlement | CNY 8,994.00 plus net proceeds | CNY 10,007.48 |
| Profit transferred to pool | cash above CNY 10,000.00 target | CNY 7.48 |
| Ending unit cash / pool | after settlement | CNY 10,000.00 / CNY 107.48 |

Both the trade entry and the unit-to-pool transfer reconcile to exactly zero. These fee values are
synthetic test inputs and are not approved production parameters.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| Unit, mark, fill, settlement, and ledger contracts | `src/quanthus/backtest/contracts.py` |
| Carry, fill, sell accounting, and pool settlement | `src/quanthus/backtest/engine.py` |
| Synthetic sell-cost policy | `tests/fixtures/p2/execution-policy.synthetic.v1.json` |
| Hand-calculated oracle | `tests/fixtures/golden/p2_carried_sell.v1.json` |
| Behavioral verification | `tests/unit/test_backtest_engine.py` |

## 4. Residual boundary

This evidence covers one full-position sell only. It does not derive limit state from P1 datasets,
model odd-lot or partial exit, accept multiple simultaneous orders, apply corporate actions,
persist an event store, or publish the complete fixed-unit performance report. Production fee
policy and target two-window matching remain blocked.

## 5. Verification

- Focused policy and engine suite: passed 15 tests.
- Ruff, formatting, and mypy on the P2 package and focused tests: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 133 tests, environment doctor,
  compilation, and planning synchronization preview.
