# LLD 06: Event Library and Outcome Dataset

| Field | Value |
| --- | --- |
| Phase | P4 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | not started; event semantics require a P4 proposal before code |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-EVENT-001 through FR-EVENT-004 |

## 1. Goal

Change the research unit from every security-market-day row to a qualified candidate event and
create a leakage-safe dataset that distinguishes positive, neutral, failed, and unexecutable
outcomes. This is a research dataset, not a prediction that every security must rise or fall on
every date.

## 2. Event record

Required fields include event id, security id, signal date T, candidate rule/version,
snapshot id, executable-entry status and price, isolation group, feature-window manifest,
outcome-label version, outcome, and split assignment.

Event ids are deterministic from security, T, candidate version, and snapshot id. Regenerating
an unchanged event library produces the same ids.

## 3. Candidate creation

A full-market daily scan evaluates every eligible security at each T close, but it does not turn
every evaluated row into an event. A row becomes a candidate only when one separately approved P4
event-source rule changes from not-triggered to triggered at T using information knowable by T
close. Examples of possible rule families include a price/volume state transition, a disclosure,
or a tradability-state transition. An example is not active policy until its hypothesis, inputs,
thresholds, cooldown, and version are frozen.

The scan therefore has three distinct counts which must reconcile:

1. `evaluated_rows`: eligible security-date rows inspected;
2. `candidate_events`: rows where the frozen trigger fires after isolation/deduplication; and
3. `labeled_events`: candidates assigned one of the four terminal outcomes.

Candidates come from a forward historical scan. They must not be selected by looking backward
from a later price rise, by ranking on a future return, or by tuning the trigger after outcome
comparison. Event-source rules and thresholds freeze before labels are generated. The frozen P3
capability fixture proves scan and audit interfaces but is not the default P4 event definition;
reusing any of its rules requires an explicit P4 hypothesis and rationale.

Historical concept membership is unavailable, so official candidate rules cannot require it.
Static level-one industry may support stratification but cannot stand in for a historical
concept/sector signal.

## 4. Timing and feature window

T is the close that confirms the candidate. The M-day window is T through T-(M-1) and may use
only values whose knowledge cutoff is no later than T close. Outcome measurement begins from
the actual executable T+1 entry.

The candidate's intended entry is governed independently from its later outcome. At T+1 the
matcher checks listing validity, session state, suspension, price limits, lot/price availability,
and required input completeness. If the declared entry cannot be executed under the frozen
policy, the terminal outcome is `not_executable`. That row is retained for opportunity-cost and
selection-bias analysis but excluded from official model training and executable backtest results.

## 5. Isolation and splits

- Events for one security require an approved minimum trading-day separation.
- Concentrated industry/wave samples are capped by a deterministic pre-outcome sampler.
- Split boundaries use event date and a purge/embargo at least as long as the feature/outcome
  overlap window.
- All slices of one event remain in one split.
- Development, arbitration, and blind periods follow the P0 policy.

## 6. Outcome labels

Classification is applied in this fixed order so that categories are mutually exclusive:

1. `not_executable`: the frozen T+1 entry cannot be executed or the required entry evidence is
   absent;
2. `failure`: after an executable entry, a frozen adverse threshold or independent structural
   break occurs within the observation window;
3. `positive`: after an executable entry, the frozen favorable threshold is reached without the
   failure condition winning under the card's overlap rule; and
4. `neutral`: the entry is executable, but neither positive nor failure conditions occur before
   the fixed window ends.

`Neutral` means "the tested event did not develop decisively within this window". It does not
mean zero return, missing data, or a weak failure. `Failure` is also not simply every negative
return: it requires the declared adverse or structural condition. `Not_executable` describes the
entry opportunity, not the company's quality or the signal's later direction.

Positive, neutral, and failure definitions use a frozen P4 label card. The draft reference is 20
sessions, positive maximum favorable excursion of 15 percent without structural break, and
failure on structural break or 7 percent adverse excursion. Those values are not active policy
until the P4 proposal freezes them. The card must also declare price basis, corporate-action
treatment, intraday ordering when thresholds overlap, end-of-window behavior, and incomplete-tail
handling.

Structural break is an independent, replayable rule version and cannot call the expert under
evaluation. Overlap resolution and unavailable-entry behavior are explicit tests.

## 7. Comparative report

For each feature and label, report group distributions at T through T-(M-1), effect size,
uncertainty, multiple-testing context, and first reliable divergence date. The primary question
is which observable state separates positive from neutral events before entry. The report also
shows candidate-to-executable attrition so that apparently strong results cannot hide a large
unexecutable group.

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P4-EVENT-001 | Freeze candidate-source hypothesis; define event, isolation, and split schemas | Accepted P3 replay and audit contracts | Approved rule card, deterministic ids, and split tests |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4-EVENT-001 | No backward selection |
| P4-LABEL-001 | Freeze three-class and structural-break cards | Governance proposal | Reproducible label corpus |
| P4-REPORT-001 | Produce daily group comparison report | Event dataset | Three leading features |

## 9. Acceptance

The library spans at least three years and five industries, no year exceeds the approved share,
all labels replay identically, and at least three leading differentiators survive the declared
statistical and perturbation checks.
