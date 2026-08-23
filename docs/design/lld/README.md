# Low-Level Design Map

The LLD tree is organized by system responsibility rather than delivery phase. Load the HLD, this
index, and one active branch; expand to linked siblings only for a concrete interface dependency.

## Accepted P3 Foundation And Current P4 Route

1. `../hld.md`: global invariants and active daily sequence.
2. `06-event-library.md`: current P4 event, outcome, isolation, and split design.
3. `03-label-feature-system.md`: accepted P3 vocabulary and feature-lineage substrate.
4. `04-experts-aggregator.md`: accepted typed opinions, vetoes, constraints, and decisions; load
   `04-experts-aggregator/expert-strategy-layer.md` for the approved additive v2 evaluation,
   lifecycle, formal-strategy MVP, and stop-condition design.
5. `05-position-execution.md`: accepted fixed-unit strategy lifecycle and exits.
6. `02-backtest-engine.md`: accepted daily execution/accounting interface and the
   `02-backtest-engine/strategy-replay.md` P3 orchestration child; load exact evidence only when
   changing that boundary.
7. `01-data-foundation.md` and its `01-data-foundation/strategy-replay-ads.md` child for offline
   replay research bundles or `01-data-foundation/operational-ads-base.md` for the OCI historical
   Base. Use `01-data-foundation/dwd-domain-model.md` for the product-bounded seven-domain catalog,
   `01-data-foundation/ods-dwd-data-dictionary.md` for ODS/DWD fields and physical inventory, and
   `01-data-foundation/ods-to-dwd-lineage.md` for source mappings, build DAG, and recurring coverage,
   DWD objects, DWS dimensions/facts, grains, time semantics, or ODS coverage; `10-monitoring-attribution.md`
   only for the exact audit path.

For user-facing terminology and strategy-policy discussion, start with the non-governing
`../p3-strategy-semantics-checklist.md` and expand only the LLD branch for the item being decided.
For an approved candidate implementation, start with its card under `../strategies/` and then load
only the LLD modules named by that card.

For strategy-attribution protocol work, load `10-monitoring-attribution.md` and its
`10-monitoring-attribution/strategy-attribution.md` child. The parent owns audit and reporting
boundaries; the child owns the strategy-adapter boundary, attribution questions, interventions,
outcome-blind planning, resumable Pipeline, evidence validation, atomic publication, and read-only
explanation views. Load `../../architecture/strategy-attribution-publication.md` when changing the
publisher-to-browser trust boundary. Strategy topology remains native and may be coarser than the
current candidate/expert path. Its first formal runtime is accepted as a bounded post-P3 increment;
recurring dashboard work remains deferred.

For Platform Kernel extraction, load `11-platform-kernel.md`, ADR 0010, and its machine-readable
gate. Load the non-governing QuantDinger audit only to verify upstream facts. Do not load P1-P7
module internals unless a named application port crosses that boundary.

## Module tree

| Node | Phase | Current role | Load when |
| --- | --- | --- | --- |
| `01-data-foundation.md` | P1 | Accepted-conditional input foundation | Data, snapshots, tradability, ODS/DWD/DWS, or PIT behavior changes |
| `02-backtest-engine.md` | P2/P3 | Accepted-conditional daily referee plus integrated bounded strategy replay; target intraday activation blocked | Events, orders, fills, matching, ledgers, costs, or deterministic replay |
| `03-label-feature-system.md` | P3 | Accepted capability substrate; formal vocabularies are proposal-driven | Labels, features, state vocabularies, or feature lineage |
| `04-experts-aggregator.md` | P3 | Accepted v1 capability substrate; Expert Strategy Layer MVP v2 evaluation design approved and implementation open | Expert contracts, vetoes, constraints, scores, aggregation, evaluation, scorecards, or expert lifecycle |
| `05-position-execution.md` | P3 | Accepted capability substrate | Strategy position lifecycle, sizing, exits, or cooldown |
| `06-event-library.md` | P4 | Event, label, report, job-local reuse, and exact scan optimization accepted; peak-memory reduction remains open | Event cohorts, outcomes, event-window definitions, or Event batch performance |
| `07-research-scoring.md` | P5 | Deferred | Statistical/ML research and promotion |
| `08-validation-governance.md` | P6 policy | Approved future governance | Arbitration, blind data, change budgets, or model promotion |
| `09-roadmap.md` | All | Delivery sequence | Phase gates, milestones, or stop rules |
| `10-monitoring-attribution.md` | P2+ | Accepted event/funnel contracts, synthetic attribution closure, and first formal-strategy attribution release; recurring reports remain proposal-triggered | Decision or notification logs, hypothetical paths, funnels, or attribution |
| `11-platform-kernel.md` | PK, orthogonal | PK1-PK4, Replay Worker, P4 Event Worker, and PK6 accepted; first-cutover Authoring not applicable | Authentication, API composition, jobs/workers, platform persistence, Web shell, QuantDinger extraction, or module recovery |

Detailed child files are added beneath a same-named module directory only after one module becomes
too large to load usefully as a single node. The parent LLD remains the stable entry point and owns
the cross-child interface.
