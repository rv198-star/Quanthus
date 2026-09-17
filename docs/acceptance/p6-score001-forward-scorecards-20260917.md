# P6-SCORE-001 Forward Scorecards Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted / closed |
| Work item | `P6-SCORE-001` |
| Predecessor | accepted `P6-DATA-001` |
| Policy | `config/forward-validation/scorecards/p6-role-scorecards.v1.json` |
| Design | `docs/design/lld/08-validation-governance/p6-scorecards.md` |
| Code | `src/quanthus/forward_validation/scorecards.py`, `loss_attribution.py` |
| Official prospective result read | none |

## Accepted outcome

P6 now has pre-result role-specific scorecard and material-loss semantics. The implementation reuses
accepted ExpertScorecard, attribution and P2 evidence boundaries rather than creating a universal
expert leaderboard.

Required scorecards remain separate for Trend assessment, Position Risk, Aggregator, complete
fixed-unit strategy and paper operation. Frozen primary metrics cannot be omitted or replaced by an
unregistered result after observations arrive; slices are drawn only from the preregistered common
set and every card publishes limitations beside its metrics.

The material-loss review trigger is CNY 1,000 (1% of one frozen CNY 100,000 unit), while any
mechanism-integrity breach is material regardless of PnL. Every material loss must be assigned one
predeclared root class, and an unclassified material loss blocks review readiness.

## Verification

Focused entry/data/scorecard tests passed 20/20 before this acceptance. Tests prove role separation,
required primary metrics, registered slices, the frozen material threshold, mechanism-integrity
override, mandatory loss classification and all-role/closed-ledger review readiness.

Repository-wide `make check` is required after current-state documentation/planning updates before
the successor implementation is accepted.

No Worker, paper runtime, Web route or prospective cycle was executed by this child. Arbitration
2022-2024 and permanent blind 2025 remain sealed.

## Authority after closure

```text
P6-GOV-001: ACCEPTED
P6-DATA-001: ACCEPTED
P6-SCORE-001: ACCEPTED
P6-GOV-002: dependency-ready
P6-PAPER-001 and later: BLOCKED
P6-ACTIVATE-001: NOT OPENED
```

`P6-GOV-002` may now implement the disabled composite Forward Worker. This acceptance does not
activate it or grant paper/notification/broker/live authority.
