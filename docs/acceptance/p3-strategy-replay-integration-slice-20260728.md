# P3 Strategy Replay Integration Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded integration slice passed |
| Evidence date | 2026-07-28 |
| Scope | Typed P1 frame adaptation, carried-sell company actions, and immutable replay packaging |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-REPLAY-001` |
| Dependency evidence | `docs/acceptance/p3-strategy-replay-slice-20260728.md` |

## 1. Decision

The integration increment passes its bounded acceptance cases. The replay path can now consume
already-resolved P1 daily execution evidence through a strict typed adapter, process a company
action after a sell has begun carrying without creating another order, and atomically publish its
analysis evidence linked to the exact P2 run event stream.

This accepts replay plumbing, not a strategy. It defines no feature, label, expert, action
threshold, position percentage, holding duration, cooldown, or expected return.

## 2. Accepted behavior

- P1 evidence must match the declared T+1 execution date and contain unique securities. It reuses
  the accepted conservative P1-to-P2 session mapper and is sorted deterministically.
- Strategy callbacks still receive only the T snapshot, decision time, and immutable unit state;
  T+1 evidence and sessions remain runner-owned.
- Cash dividends and bonus shares during a carried sell apply before the eligible open. A bonus
  share adjustment is explicit in the existing order's status stream, and the eventual fill uses
  the adjusted full-position quantity under the same order id.
- `persist_strategy_replay` atomically publishes replay decisions, equity, trades, metrics, and
  final state. Its manifest binds counts and payload checksum to the immutable P2 run manifest and
  event-stream checksums. Tampering fails load.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| P1 typed adapter and replay runner | `src/quanthus/backtest/strategy_replay.py` |
| Incremental company-action/order interleaving | `src/quanthus/backtest/engine.py` |
| Atomic replay package and checksum linkage | `src/quanthus/backtest/audit.py` |
| Behavioral verification | `tests/unit/test_strategy_replay.py` |

## 4. Residual boundary

There is still no generic arbitrary-feature Parquet query layer or concrete P3 `DecisionEvent`
producer. Multi-unit strategy execution, add/reduce actions, concrete policy, opportunity funnel,
and multi-year acceptance remain open. The reservation competition accepted in LLD 05 has no
execution authority and is not a substitute for multi-unit replay.

## 5. Verification

- Focused replay, P2 engine/replay, and audit suite: passed 34 tests.
- Focused Ruff, formatting, and diff checks: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 279 tests.
