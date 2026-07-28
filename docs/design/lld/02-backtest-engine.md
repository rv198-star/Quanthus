# LLD 02: Event-Driven Backtest Engine

| Field | Value |
| --- | --- |
| Phase | P2 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | not-started |
| Gate source | `config/p1-gates.v1.json` |
| Governing requirements | FR-BT-001 through FR-BT-006, FR-AUDIT-001/002 |

## 1. Goal

Act as a conservative and deterministic referee. Strategy code emits proposals; the engine
owns time, orders, fills, costs, capital, positions, and audit events.

### Scope and ownership boundary

P2 owns the dated event loop, order lifecycle, conservative matching, fill/cost calculation,
accounting ledger, run manifest, deterministic metrics, and golden replay. It consumes explicit
signals or proposals and must not implement P3 labels, experts, aggregation, strategy exits,
trial/add policy, concentration policy, or cooldown behavior. Those later decisions may call the
same engine but cannot be embedded in its matcher or ledger.

The Pre-P2 conformance audit records that no P2 matcher, order, fill, or ledger implementation
exists at its evidence cutoff. `src/quanthus/contracts.py` contains shared `SnapshotRef`,
`CandidateSignal`, and `RunManifest` contracts only; the operational data-pipeline runner under
`src/quanthus/pipelines/` is not the trading event engine. P2 must establish one canonical
production ownership boundary under `src/quanthus` and must not create alternate notebook, script,
or test-only engines.

Evidence: `docs/acceptance/pre-p2-conformance-20260728.md`.

Load `10-monitoring-attribution.md` for P2 decision/shadow-log and hypothetical-ledger contracts.
Load `05-position-execution.md` only to keep its P3 strategy-position lifecycle outside P2
accounting ownership.

## 2. Core event types

| Event | Required identity and payload |
| --- | --- |
| Signal | signal id, T date, security, action, unit, size, reason, decision version |
| Order | order id, signal id, eligible session, side, quantity, state, policy version |
| Order status | transition, time, reason code, source snapshot |
| Fill | order id, price, quantity, costs, slippage rule, market state |
| Ledger entry | unit, cash/position/external-pool delta, fill/event reference |
| Daily snapshot | unit balances, positions, pending sells, marked value, manifest |

All ids are stable under replay. Append-only event records are the accounting source of truth.

## 3. Daily processing

1. Apply corporate actions and session tradability.
2. Process carried sells and T-open eligible orders.
3. Emit fill, cancel, reject, or carry events.
4. Settle unit and external-pool ledger entries.
5. Mark positions and publish the daily ledger snapshot.
6. Consume the T-close decision snapshot.
7. Rank signals and create T+1 eligible orders.

No T-close signal may fill at the T close.

## 4. Matching policy

- Buy fill reference: next executable open plus versioned buy slippage and costs.
- Sell fill reference: next executable open minus versioned sell slippage and costs.
- An unfilled buy is cancelled that session and reconsidered only after a new close decision.
- An unfilled sell is carried until tradable and records exactly one status event per session.
- Orders use whole A-share lots for entry; exit odd-lot behavior must be an explicit exchange rule.
- Partial fills are outside the first engine version and must be represented as a rejected
  unsupported mode rather than silently rounded.

Fees, taxes, slippage, lot rules, and limit regimes are versioned inputs. Their values are not
hard-coded in strategy modules.

## 5. Capital units

Each unit has one active event and one security at a time. Valid lifecycle states are empty,
trial, dynamic, exiting, and closed. Profit transfers to the finite external pool and loss is
replenished at close settlement if funds exist. If the pool is empty, the unit continues with
reduced notional and records the funding event.

Performance reports separate trade return, unit-capital-day return, total PnL, external cash
flow, and cash-flow-adjusted drawdown. They do not present a fixed-unit run as compound NAV.

## 6. Determinism

- Same-day candidates sort by aggregate score descending, then security code ascending.
- State transitions use explicit precedence and reason codes.
- Decimal/currency rounding rules are centralized and tested.
- Input snapshots and policy versions are immutable during a run.
- Repeated execution produces identical ordered events and ledger balances.

## 7. Audit and counterfactual hooks

The engine records accepted and rejected signals from day one. Rejected signals feed a
diagnostic hypothetical ledger, but official risk/opportunity amounts come from a separate
capital-constrained strategy rerun using the same engine.

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P2-ENG-001 | Define event schemas and deterministic event loop | P1 acceptance | Replayable synthetic session |
| P2-POLICY-001 | Freeze fees, slippage, lot, and matching parameters | P1 acceptance | Versioned execution policy |
| P2-MATCH-001 | Implement open matching and tradability handling | P2-ENG-001 | Limit/suspension golden cases |
| P2-LEDGER-001 | Implement unit and external-pool double-entry ledger | P2-ENG-001 | Exact balance reconciliation |
| P2-ORDER-001 | Implement cancel-buy and carry-sell lifecycle | Matcher | No duplicate or lost order events |
| P2-AUDIT-001 | Implement run manifest and decision/hypothetical logs | Event schemas | Full provenance query |
| P2-METRIC-001 | Implement fixed-unit performance reports | Ledger | Hand-calculated report fixture |
| P2-ACCEPT-001 | Build five-plus golden trade scenarios | All P2 work | Cent-exact and deterministic rerun |

## 9. Acceptance

The minimum corpus covers a normal trade, unfilled limit-up buy, carried limit-down sell,
suspension, cash shortage, same-day candidate tie, corporate action, and maximum-hold exit.
Every output is traceable to its signal and snapshot and reconciles to the expected ledger.
