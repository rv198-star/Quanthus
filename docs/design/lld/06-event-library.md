# LLD 06: Event Library and Three-Class Dataset

| Field | Value |
| --- | --- |
| Phase | P4 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | not started; event semantics require a P4 proposal before code |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-EVENT-001 through FR-EVENT-004 |

## 1. Goal

Change the research unit from every market-day row to a qualified candidate event and create
a leakage-safe dataset that distinguishes positive, intermediate, failed, and unexecutable
outcomes.

## 2. Event record

Required fields include event id, security id, signal date T, candidate rule/version,
snapshot id, executable-entry status and price, isolation group, feature-window manifest,
outcome-label version, outcome, and split assignment.

Event ids are deterministic from security, T, candidate version, and snapshot id. Regenerating
an unchanged event library produces the same ids.

## 3. Candidate creation

Candidates come from a forward historical scan using a separately approved P4 event-source rule.
They must not be selected by looking backward from a later price rise. Event-source rules and
thresholds freeze before outcome comparison. The frozen P3 capability fixture proves the scan and
audit interfaces but is not the default P4 event definition; reusing any of its rules requires an
explicit P4 hypothesis and rationale.

Historical concept membership is unavailable, so official candidate rules cannot require it.
Static level-one industry may support stratification but cannot stand in for a historical
concept/sector signal.

## 4. Timing and feature window

T is the close that confirms the candidate. The M-day window is T through T-(M-1) and may use
only values whose knowledge cutoff is no later than T close. Outcome measurement begins from
the actual executable T+1 entry.

If no approved T+1 entry exists, the outcome is `not_executable`. That row is excluded from
official training and backtest evaluation and retained in a separate research partition.

## 5. Isolation and splits

- Events for one security require an approved minimum trading-day separation.
- Concentrated industry/wave samples are capped by a deterministic pre-outcome sampler.
- Split boundaries use event date and a purge/embargo at least as long as the feature/outcome
  overlap window.
- All slices of one event remain in one split.
- Development, arbitration, and blind periods follow the P0 policy.

## 6. Outcome labels

Positive, intermediate, and failure definitions use a frozen P4 card. The draft reference is
20 sessions, positive maximum favorable excursion of 15 percent without structural break,
and failure on structural break or 7 percent adverse excursion. Those values are not active
policy until the P4 proposal freezes them.

Structural break is an independent, replayable rule version and cannot call the expert under
evaluation. Overlap resolution and unavailable-entry behavior are explicit tests.

## 7. Comparative report

For each feature and label, report group distributions at T through T-(M-1), effect size,
uncertainty, multiple-testing context, and first reliable divergence date. The primary question
is which observable state separates positive from intermediate events before entry.

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P4-EVENT-001 | Define event, isolation, and split schemas | Accepted P3 replay and audit contracts | Deterministic ids and split tests |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4-EVENT-001 | No backward selection |
| P4-LABEL-001 | Freeze three-class and structural-break cards | Governance proposal | Reproducible label corpus |
| P4-REPORT-001 | Produce daily group comparison report | Event dataset | Three leading features |

## 9. Acceptance

The library spans at least three years and five industries, no year exceeds the approved share,
all labels replay identically, and at least three leading differentiators survive the declared
statistical and perturbation checks.
