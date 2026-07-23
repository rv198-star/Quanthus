# LLD 08: Validation and Expert Evolution Governance

| Field | Value |
| --- | --- |
| Phase | Starts after P3 baseline; simulation in P6 |
| Status | Approved policy baseline |
| Governing requirements | FR-GOV-001/002, NFR-004 |

## 1. Goal

Make rule evolution evidence-driven and make repeated historical tuning visible. Validation
discipline has higher priority than a favorable performance number.

## 2. Partitions

- Development: 2012-2021; iterative work is allowed and logged.
- Arbitration: 2022-2024; revealed on the quarterly schedule, then rolled into development.
- Permanent blind: 2025; reserved for major promotion decisions and never ordinary tuning.
- Simulation: 2026 onward; continuously accumulated under frozen behavior.

Reports state which partitions were accessed. A result without arbitration remains a research
result even if it is profitable.

## 3. Change workflow

1. Proposal: cite attribution evidence and the observed failure mode.
2. Written expectation: name cases that should change and controls that should not.
3. Development replay: verify mechanism and side effects.
4. Shadow trial: record proposed actions without execution for a predeclared trigger count.
5. Promote or rollback: apply the predeclared criteria and preserve both versions.

Construction mode permits logged baseline iteration. Once a policy enters shadow or simulation,
parameter and structure quotas apply and every change uses this workflow.

## 4. Scorecards

Scoring experts are evaluated for marginal value, score monotonicity, lead time, and signal
independence. Vetoes use recall, precision, and portfolio-constrained veto cost rather than raw
profit. Constraints use activation rate and matched risk-adjusted outcome. Aggregator reports
compare consensus and disagreement and break results out by market regime.

Losses receive explicit error attribution: wrong expert, missed veto, wrong constraint,
execution assumption, or data-quality event.

## 5. Change budget

After baseline freeze, one expert may receive at most two parameter changes per quarter and the
system at most two structural expert changes per year unless a documented incident forces an
emergency fix. Exceeding the budget downgrades that period's research credibility.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P6-GOV-001 | Implement proposal and expectation record | P3 audit | Immutable proposal artifact |
| P6-GOV-002 | Implement shadow lifecycle and trigger-count gate | P3 shadow log | Promote/rollback rehearsal |
| P6-SCORE-001 | Implement expert and aggregator scorecards | Attribution data | Quarterly report fixture |
| P6-DATA-001 | Enforce partition access and reveal logging | Run manifest | Access-control audit |
| P6-REVIEW-001 | Run first quarterly arbitration review | Simulation | Signed decision package |

## 7. Acceptance

The scorecard, failure ranking, and proposal form complete one full quarterly cycle; at least
one proposal reaches promote or rollback through shadow; and the arbitration reveal is logged
without tuning against its result.
