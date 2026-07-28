# Low-Level Design Map

The LLD tree is organized by system responsibility rather than delivery phase. Load the HLD, this
index, and one active branch; expand to linked siblings only for a concrete interface dependency.

## Current P2 route

1. `../hld.md`: global invariants and daily sequence.
2. `02-backtest-engine.md`: P2 ownership, event loop, matching, ledger, and golden acceptance.
3. `10-monitoring-attribution.md`: P2 decision/shadow-log and hypothetical-ledger contracts.
4. `01-data-foundation.md`: snapshot and tradability inputs consumed by P2.
5. `05-position-execution.md` only to keep the future P3 strategy-position state machine outside
   the P2 accounting engine.

## Module tree

| Node | Phase | Current role | Load when |
| --- | --- | --- | --- |
| `01-data-foundation.md` | P1 | Accepted-conditional input foundation | Data, snapshots, tradability, ODS/DWD/DWS, or PIT behavior changes |
| `02-backtest-engine.md` | P2 | Open; implementation not started | Events, orders, fills, matching, ledgers, costs, or deterministic replay |
| `03-label-feature-system.md` | P3 | Design baseline; delivery blocked | Labels, features, state vocabularies, or feature lineage |
| `04-experts-aggregator.md` | P3 | Design baseline; delivery blocked | Expert contracts, vetoes, constraints, scores, or aggregation |
| `05-position-execution.md` | P3 | Design baseline; delivery blocked | Strategy position lifecycle, sizing, exits, or cooldown |
| `06-event-library.md` | P4 | Deferred | Event cohorts, outcomes, or event-window definitions |
| `07-research-scoring.md` | P5 | Deferred | Statistical/ML research and promotion |
| `08-validation-governance.md` | P6 policy | Approved future governance | Arbitration, blind data, change budgets, or model promotion |
| `09-roadmap.md` | All | Delivery sequence | Phase gates, milestones, or stop rules |
| `10-monitoring-attribution.md` | P2/P3 | P2 instrumentation open | Decision logs, hypothetical paths, funnels, or attribution |

Detailed child files are added beneath a same-named module directory only after one module becomes
too large to load usefully as a single node. The parent LLD remains the stable entry point and owns
the cross-child interface.
