# P3 Strategy Historical Replay Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Strategy-neutral one-unit daily replay, multi-trade accounting, and performance |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-REPLAY-001` |
| Dependency evidence | `docs/acceptance/p2-conformance-acceptance-20260728.md` |

## 1. Decision

`P3-REPLAY-001` passes its bounded research slice. `src/quanthus/backtest/strategy_replay.py`
evaluates a versioned strategy over consecutive T-close PIT contexts, converts actionable outputs
to P2 signals, advances the same fixed-capital unit, and emits canonical decisions, P2 events,
daily system equity, closed trades, and performance metrics.

The decision accepts a replay substrate, not a strategy. It freezes no label, expert, score/action
mapping, entry threshold, position percentage, holding duration, cooldown, or return expectation.
The scripted golden strategy is acceptance input only.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Strategy cannot receive T+1 session prices or states | `StrategyReplayContext`, `tests/unit/test_strategy_replay.py` |
| Consecutive state preserves fees, external pool, actions, and marks | `src/quanthus/backtest/engine.py`, two-trade golden case |
| All actionable decisions use P2 Signal/OrderIntent/Order/Fill | `src/quanthus/backtest/strategy_replay.py`, persisted event-stream assertion |
| Carried sell retains one order and skips new strategy decisions | carried-exit unit case |
| Metrics reconcile two trades and the daily equity curve | `tests/fixtures/golden/p3_strategy_replay.v1.json` |
| Identical inputs produce byte-identical result | repeated-run canonical-byte assertion |
| Unfrozen add behavior fails explicitly | add-attempt unit case |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- A frame binds one T-close PIT snapshot to its later execution date, while only the snapshot,
  decision time, and current immutable unit state enter the strategy callback.
- `no_action`, empty-unit `enter`, and positioned full `exit` are the only accepted strategy
  actions. An add attempt fails before a second order can exist.
- A filled entry, held-position mark, cash dividend, exit, unit settlement, and later entry preserve
  one cumulative P2 accounting state and finite external pool.
- An untradable sell carries through P2 with one Order and one status/mark per session. The runner
  does not ask the strategy for a conflicting decision while the unit is exiting.
- System equity includes the fixed unit plus external pool. Profits are not silently redeployed,
  and every report declares `compound_nav=false`.
- The report distinguishes total return, fixed-unit capital-day return, maximum drawdown,
  annualized volatility, zero-risk-free Sharpe, win/loss statistics, payoff ratio, profit factor,
  turnover, fees, exposure, and average holding sessions. Undefined ratios remain null.
- The combined P2 event stream can be persisted by the accepted immutable audit store.

## 4. External implementation boundary

Open-source event-driven and vectorized backtest projects may be used as design references and
independent metric checks. Their default data, adjustment, fill, account, and return assumptions
do not replace Quanthus contracts. No external engine may create official fill or performance facts
without first proving conformance to the same P1/P2 evidence and fixed-unit accounting semantics.

## 5. Residual boundary

The slice is one-unit and research-only. It does not yet provide a unified full-history P1 feature
query adapter, candidate competition across units, add/reduce actions, position-policy parameters,
full P3 `DecisionEvent` production, or opportunity attribution. A corporate action that arises
after a sell has already begun carrying fails explicitly pending incremental P2 interleaving.

Concrete policy remains with `P3-LABEL-002`, `P3-EXP-002`, `P3-AGG-002`, `P3-POLICY-001`, and
`P3-POS-002/003`. The multi-year acceptance remains `P3-ACCEPT-001` and cannot pass from the
synthetic golden case.

## 6. Verification

- Focused strategy replay suite: passed 6 tests.
- Existing focused P2 engine/replay suite: passed 22 tests.
- Focused Ruff and strict mypy: passed.
- Final full repository conformance run: passed 269 tests, plus doctor, compile, and planning
  preview checks.
