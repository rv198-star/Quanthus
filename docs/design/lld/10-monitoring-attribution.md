# LLD 10: Monitoring and Contribution Attribution

| Field | Value |
| --- | --- |
| Phase | Instrumentation in P2; reports after P3 |
| Status | Approved baseline |
| Governing requirements | FR-AUDIT-001 through FR-AUDIT-004 |

## 1. Goal

Measure where risk was avoided, where opportunities were captured or missed, and how profit
quality changed. Monitoring is an event contract, not a dashboard added after the fact.

## 2. North-star metrics

| Metric | Official interpretation |
| --- | --- |
| Risk avoided | Capital-constrained baseline minus policy-removed rerun loss |
| Opportunity captured | Executed and adequately sized true opportunities under fixed capital |
| Profit growth | Cost-adjusted PnL quality, including base/add contribution and capital-day use |

Diagnostic children include veto outcomes, exit timeliness, constrained exposure, capture
rate, false-veto cost, cash/cooldown misses, add sufficiency, payoff ratio, and hold efficiency.

## 3. Decision log

One record exists for every candidate and held-position review. It includes time, security,
unit, lifecycle stage, complete expert outputs, aggregate score, vetoes/constraints, proposed
and final action, reason codes, snapshot, policy versions, and linked order ids. Shadow outputs
use the same schema and are explicitly non-executing.

## 4. Hypothetical ledger

Rejected and near-threshold signals are followed under the frozen baseline entry/exit policy
for at most 20 sessions. These isolated paths diagnose a decision but ignore competition for
capital and must never be summed as official risk/opportunity money.

Official monetary attribution reruns the full strategy under identical capital and execution
constraints with one module removed or neutralized. The reported value is the delta from the
versioned baseline. Module deltas are not additive because interactions exist; material claims
receive pairwise reruns or an explicit interaction caveat.

## 5. Opportunity funnel

Fixed stages are candidate to score, score to veto, entry to add, and hold to exit. Each stage
reconciles input/output counts and joins realized or diagnostic outcomes. A funnel that does
not reconcile to the event total fails publication.

## 6. Reporting cuts

Reports support month/quarter, market regime, lifecycle stage, expert/aggregator/position rule,
and policy version. The first dashboard row always has exactly the three north stars. Funnel,
contribution matrix, and failure ranking appear below it.

## 7. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P2-OBS-001 | Define decision/shadow log schema | P2 events | Accepted/rejected trace fixture |
| P2-HYP-001 | Implement diagnostic hypothetical ledger | Matcher/exit policy | 20-session path fixture |
| P3-FUNNEL-001 | Implement reconciled opportunity funnel | P3 vertical slice | Count reconciliation |
| P3-ATTR-001 | Implement constrained remove/neutralize reruns | Trusted engine | Reproducible contribution matrix |
| P3-DASH-001 | Produce three-layer monitoring report | Funnel/attribution | Monthly report fixture |

## 8. Acceptance

Every fill and rejection resolves to a complete decision log; monthly north stars are
reproducible; the first contribution matrix reruns from a manifest; and all funnel stages
reconcile exactly.
