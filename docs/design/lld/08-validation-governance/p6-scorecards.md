# P6-SCORE-001 Forward Scorecards and Loss Attribution

| Field | Value |
| --- | --- |
| Parent | `p6-forward-validation.md` |
| Design status | approved |
| Delivery gate | P6-SCORE-001 implementation |
| Policy | `config/forward-validation/scorecards/p6-role-scorecards.v1.json` |
| Code | `src/quanthus/forward_validation/scorecards.py`, `loss_attribution.py` |
| Result authority | none; scorecard semantics only |

## Decision

P6 reuses accepted expert/attribution/P2 evidence primitives and adds only a role-specific prospective
scorecard policy. It does not create a universal expert score. Assessment quality, risk-policy
activity, aggregation behavior, complete-strategy economics and paper-operation integrity remain
separate evidence surfaces.

The five required cards are Trend assessment, Position Risk, Aggregator, complete fixed-unit
strategy and paper operation. Every card binds one immutable source artifact, frozen registered
metrics, sample count, declared slices and limitations.

## Material loss

A material loss is either a mechanism-integrity breach or an absolute paper loss of at least 1% of
one frozen CNY 100,000 unit (CNY 1,000). This threshold is an operational review trigger, not an
investment tuning threshold.

Every material loss must have exactly one frozen attribution class: trend assessment error,
position-risk missed veto, position-risk false veto, position-risk constraint error,
aggregation/action-mapping error, execution assumption, or data-quality incident. Unclassified
material loss blocks review readiness.

## Boundaries

- Trend uses the accepted ExpertScorecard target/coverage/abstention semantics; paper return cannot
  stand in for forecast quality.
- Position Risk remains policy-only and gains no positive entry score.
- Aggregator scorecards describe consensus/disagreement, final actions and blocked-new behavior;
  they do not inherit a single expert's quality score.
- Complete-strategy economics stay fixed-unit/non-compounding and descriptive; profitability is not
  the P6 primary mechanism gate.
- Paper operation owns reconciliation, pending intents, fills, costs and fixed-unit equity; paper
  fills never become live fills.
- Cross-sectional rows never become independent evidence units; headline evidence remains grouped
  by the frozen 22-session blocks and declared regime/date slices.

## Review readiness

A later review is mechanically ready only when all five role cards exist, mature sample ledgers are
closed where due, mechanism-integrity breach count is zero and no material loss remains unclassified.
Low activity and weak/negative economics may still close P6 inconclusive or negative.

This child creates no Worker, paper account, Web view, prospective activation, notification, broker,
live capital, Stage action, P5 or sealed-partition authority.
