# P2 Integrated Replay and Observability Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Cases | `P2-ROUND-TRIP-REPLAY-001`, `P2-P1-EXECUTION-ADAPTER-001`, `P2-DECISION-LOG-001` |
| Scope | Frozen daily research policy, P1 execution adapter, integrated round trip, Decision/Shadow records, and bounded diagnostic replay |
| Governing design | `docs/design/lld/02-backtest-engine.md`, `docs/design/lld/10-monitoring-attribution.md` |
| Governing work | `P2-ENG-001`, `P2-POLICY-001`, `P2-MATCH-001`, `P2-AUDIT-001`, `P2-OBS-001`, `P2-HYP-001`, `P2-ACCEPT-001` |

## 1. Integrated replay decision

The round-trip orchestrator consumes explicit upstream buy and sell Signals plus already
PIT-resolved market sessions and corporate actions. It does not select a security, calculate a
maximum hold, or implement P3 lifecycle rules. It composes the same accepted buy, sell, ledger,
corporate-action, fixed-unit metric, and audit components into one deterministic event stream.

The golden path buys 100 shares, receives CNY 20.00 cash dividend, and sells at CNY 10.19. Its 18
ordered events include distinct Signal, OrderIntent, Order, Fill, ledger, action, settlement, and
snapshot records. Ending unit cash is CNY 10,000.00, the finite external pool receives CNY 27.48,
and total PnL reconciles to CNY 27.48. The fixed-unit report publishes 2.731610% trade return,
0.091600% unit-capital-day return, zero drawdown in this path, and no compound NAV.

## 2. Policy and P1 boundary

`config/p2-execution-policy.research.v1.json` is frozen for historical and paper research only. It
versions 5 bps side-specific slippage, 0.03% commission with CNY 5.00 minimum, seller-side 0.05%
stamp duty, 0.001% transfer fee, whole-lot entry, and unsupported partial fills. The policy is not a
claim about a future broker account and must be recalibrated before P7 reconciliation.

The P1 adapter maps six exact evidence classes. A normal valid row remains executable; suspension
discards vendor placeholder prices; an open at the upper or lower limit becomes a directional
buy/sell block; invalid and zero-liquidity rows become missing execution evidence. Strategy code
does not derive these states.

## 3. Decision and diagnostic boundary

Accepted, rejected, and shadow decisions retain complete opaque expert outputs, score, vetoes,
constraints, proposed/final action, reasons, snapshot, policy versions, and linked orders. An
accepted record must resolve to its persisted Signal and exact orders. Rejected and shadow records
cannot link an order, and immutable replay cannot replace changed evidence.

The isolated hypothetical variant requires a rejected or near-threshold decision id and a frozen
baseline version. It is capped at 20 capital days and records `capital_constrained=false` and
`aggregatable_as_official=false`. It therefore diagnoses one missed/rejected path without claiming
official risk-avoided or opportunity-captured money.

## 4. Evidence map

| Concern | Evidence |
| --- | --- |
| Frozen daily policy | `config/p2-execution-policy.research.v1.json`, `src/quanthus/backtest/policy.py` |
| P1 daily execution mapping | `src/quanthus/backtest/inputs.py`, `tests/fixtures/golden/p2_p1_execution_adapter.v1.json` |
| Round-trip and diagnostic orchestration | `src/quanthus/backtest/replay.py`, `tests/fixtures/golden/p2_round_trip_replay.v1.json` |
| Decision/Shadow schema and persistence | `src/quanthus/backtest/contracts.py`, `src/quanthus/backtest/audit.py`, `tests/fixtures/golden/p2_decision_log.v1.json` |
| Behavioral verification | `tests/unit/test_backtest_policy.py`, `tests/unit/test_backtest_inputs.py`, `tests/unit/test_backtest_replay.py`, `tests/unit/test_backtest_decisions.py` |

## 5. Residual boundary

The accepted round trip is a single capital unit with explicit upstream decisions. Candidate-to-
unit allocation, industry/day caps, position lifecycle, strategy exits, complete multi-year P3
replay, and capital-constrained attribution remain P3. Target two-window matching, live intraday
inputs, email transport, and actual-operation reports remain under their explicit activation and
P6/P7 gates.

## 6. Verification

- Focused P2 suite: passed 42 tests.
- Ruff, formatting, mypy, JSON validation, and whitespace checks: passed.
- Preliminary full repository check with `.venv`: passed 162 tests.
