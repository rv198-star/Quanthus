# P2 Final Conformance and Conditional Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted-conditional |
| Evidence date | 2026-07-28 |
| Accepted profile | `close-to-next-open-v1` |
| Accepted scope | Historical and paper research through explicit upstream Signals |
| Machine gate | `config/p2-gates.v1.json` |
| Frozen policy | `config/p2-execution-policy.research.v1.json` |
| Governing design | `docs/design/lld/02-backtest-engine.md`, `docs/design/lld/10-monitoring-attribution.md` |

## 1. Decision

P2 passes conditional acceptance for the active daily profile. P3 may start its bounded rule-only
historical vertical slice through the accepted Signal, OrderIntent, Order, matcher, unit ledger,
audit, and fixed-unit report interfaces. The engine remains an execution referee: P3 owns labels,
experts, aggregation, candidate allocation, position lifecycle, and exit decisions.

This acceptance does not activate `operator-assisted-two-window-v1`. The machine gate retains
open blockers for accepted intraday inputs/latency, non-synthetic window matching policy, and P6
email transport. Unsupported target-profile execution continues to fail explicitly; notification
delivery and manual action cannot imply a Fill.

## 2. Work-item closure

| Work item | Accepted evidence |
| --- | --- |
| `P2-ENG-001` | Stable Signal/OrderIntent/Order/event schemas, deterministic daily and composed replay |
| `P2-POLICY-001` | Frozen daily research policy plus synthetic target-window boundary policy |
| `P2-MATCH-001` | P1-derived executable/suspension/limit/missing states and conservative fills/no-fills |
| `P2-LEDGER-001` | Cent-exact balanced buy, sell, dividend, unit, and finite-pool postings |
| `P2-ORDER-001` | Cancel-buy, carry-sell, explicit intent expiry/carry, no duplicate order |
| `P2-AUDIT-001` | Atomic immutable run publication, checksums, Decision and notification persistence |
| `P2-METRIC-001` | Reconciled trade/capital-day/PnL/flow/drawdown report without compound NAV |
| `P2-OBS-001` | Accepted/rejected/shadow Decision schema and no-implied-fill notification stream |
| `P2-HYP-001` | Isolated <=20-capital-day non-official/non-aggregatable diagnostic replay |
| `P2-ACCEPT-001` | Complete daily and synthetic-window golden corpus plus conformance review |

## 3. Semantic conformance

- Approved design and implementation agree on T-close to T+1-open timing, half-open target window
  bounds, explicit OrderIntent before Order, cancel-buy/carry-sell behavior, and no email Fill.
- P1 execution facts have one fail-closed adapter; P2 does not query vendors or invent a missing
  price. Corporate actions require PIT-known inputs and explicit post-action marks.
- Unit and external-pool money reconciles exactly. Diagnostic paths and fixed-unit returns cannot
  be presented as compound or official capital-constrained attribution.
- P3 strategy responsibilities remain absent from `src/quanthus/backtest`; the maximum-hold case
  proves P2 consumes and preserves an upstream reason without deriving it.

## 4. Verification

- Final focused P2 suites: passed 42 tests.
- Gate, bootstrap, documentation, and planning contracts: passed 14 tests.
- Final post-gate `make check PYTHON=.venv/bin/python`: passed 163 tests, environment doctor,
  compilation, and planning synchronization preview.
- Targeted Ruff lint and format checks passed for all P2, gate, CLI, planning, and associated test
  changes; strict mypy passed for the 12 affected production and planning source modules.

The default system-Python `make check` was also run and failed only because that interpreter lacks
the optional `pyarrow` dependency required by three P1/ODS test modules. The repository `.venv`
contains the declared stack and is the authoritative full-check environment for this acceptance.
