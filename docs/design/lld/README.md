# Low-Level Design Map

The LLD tree is organized by system responsibility rather than delivery phase. Load the HLD, this
index, and one active branch; expand to linked siblings only for a concrete interface dependency.

## Current P3 route

1. `../hld.md`: global invariants and active daily sequence.
2. `03-label-feature-system.md`: rule vocabulary and feature lineage.
3. `04-experts-aggregator.md`: typed opinions, vetoes, constraints, and decision records.
4. `05-position-execution.md`: fixed-unit strategy lifecycle and exits.
5. `02-backtest-engine.md`: accepted daily execution/accounting interface and the
   `02-backtest-engine/strategy-replay.md` P3 orchestration child; load exact evidence only when
   changing that boundary.
6. `01-data-foundation.md` and `10-monitoring-attribution.md` only for the exact input/audit path.

For user-facing terminology and strategy-policy discussion, start with the non-governing
`../p3-strategy-semantics-checklist.md` and expand only the LLD branch for the item being decided.
For an approved candidate implementation, start with its card under `../strategies/` and then load
only the LLD modules named by that card.

## Module tree

| Node | Phase | Current role | Load when |
| --- | --- | --- | --- |
| `01-data-foundation.md` | P1 | Accepted-conditional input foundation | Data, snapshots, tradability, ODS/DWD/DWS, or PIT behavior changes |
| `02-backtest-engine.md` | P2/P3 | Accepted-conditional daily referee plus integrated bounded strategy replay; target intraday activation blocked | Events, orders, fills, matching, ledgers, costs, or deterministic replay |
| `03-label-feature-system.md` | P3 | Rule-only implementation open | Labels, features, state vocabularies, or feature lineage |
| `04-experts-aggregator.md` | P3 | Rule-only implementation open | Expert contracts, vetoes, constraints, scores, or aggregation |
| `05-position-execution.md` | P3 | Rule-only implementation open | Strategy position lifecycle, sizing, exits, or cooldown |
| `06-event-library.md` | P4 | Deferred | Event cohorts, outcomes, or event-window definitions |
| `07-research-scoring.md` | P5 | Deferred | Statistical/ML research and promotion |
| `08-validation-governance.md` | P6 policy | Approved future governance | Arbitration, blind data, change budgets, or model promotion |
| `09-roadmap.md` | All | Delivery sequence | Phase gates, milestones, or stop rules |
| `10-monitoring-attribution.md` | P2/P3 | Accepted P2 event contracts and bounded P3 funnel reconciliation; populated reports and attribution open | Decision or notification logs, hypothetical paths, funnels, or attribution |

Detailed child files are added beneath a same-named module directory only after one module becomes
too large to load usefully as a single node. The parent LLD remains the stable entry point and owns
the cross-child interface.
