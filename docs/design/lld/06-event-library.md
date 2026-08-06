# LLD 06: Event Library and Outcome Dataset

| Field | Value |
| --- | --- |
| Phase | P4 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | `P4-EVENT-001` Stage A contracts implemented and human-approved; `P4-EVENT-002` forward scan not implemented |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-EVENT-001 through FR-EVENT-004 |

## 1. Goal

Change the research unit from every security-market-day row to a qualified candidate event and
create a leakage-safe dataset that distinguishes positive, neutral, failed, and unexecutable
outcomes. This is a research dataset, not a prediction that every security must rise or fall on
every date.

## 2. Event record

The `P4-EVENT-001` candidate record is deliberately label-free. It binds event id, security id,
signal date T, candidate rule/version/digest, exact snapshot and manifest digests, T-close
knowledge cutoff, eligibility evidence, feature-window manifest, isolation group, and split-policy
identity/digest/assignment. Executable-entry evidence is appended by `P4-EVENT-002`; label-card
identity and terminal outcome are appended by `P4-LABEL-001`. Neither later extension may alter the
candidate event id.

Event ids are deterministic from security, T, candidate rule id/version, and snapshot id.
Regenerating an unchanged event library produces the same ids. Labels, split assignment, realized
fills, future returns, and post-T features are outside the identity namespace.

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

### 3.1 First candidate proposal and activation gate

`config/p4-candidate-price-volume-transition.research.v1.json` is the complete Stage A proposal for
a direction-neutral quiet-to-active daily price-volume transition. It uses unadjusted same-session
intraday range and CNY turnover, normalizes each against its own previous 20 eligible sessions,
defines quiet as both activity ratios at or below `0.80`, and defines active as both at or above
`1.50`. The edge is `quiet(T-1) -> active(T)`. The card also freezes PIT inputs, 22-session minimum
history, listing/tradability/corporate-action exclusions, 22-session same-security isolation, and
deterministic static-level-one-industry concentration sampling. The industry mapping is the
versioned `static_non_pit_concentration_only` artifact already authorized by P0; it is never a
candidate signal or reconstructed historical membership.

The exact card digest is human-approved and bound to the approval record in GitHub Issue #73.
`CandidateRuleCard.require_stage_b_authority()` now admits Stage B only for this approved semantic
payload. A semantic or threshold edit changes the digest and requires a new rule version,
experiment identity, and renewed human approval rather than in-place tuning.

The contract implementation is under `src/quanthus/event_library/`. Exact-key parsing rejects
unknown or outcome-bearing candidate inputs and record extensions; canonical serialization verifies
the semantic digest; the event record enforces `knowledge_cutoff <= T close`; and record-set
validation rejects duplicate identities or one event assigned to multiple splits.

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

`config/p4-event-split-policy.research.v1.json` materializes the P0 date boundaries, a 22-session
purge, a 22-session embargo, exact snapshot/policy-digest binding, and development-only read
authority. Its exact digest is human-approved. Arbitration and permanent-blind assignments still
fail closed under the approved read policy, and an event whose feature or outcome slice crosses a
boundary is rejected. `P4-LABEL-001` must advance this split-policy version before activation if its
frozen maximum outcome overlap exceeds 22 sessions.

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
| P4-EVENT-001 | Freeze candidate-source hypothesis; define event, isolation, and split schemas | Accepted P3 replay and audit contracts | Exact rule-card and split-policy digests approved; deterministic contract tests and acceptance record pass |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4-EVENT-001 | No backward selection |
| P4-LABEL-001 | Freeze three-class and structural-break cards | Governance proposal | Reproducible label corpus |
| P4-REPORT-001 | Produce daily group comparison report | Event dataset | Three leading features |

## 9. Acceptance

The library spans at least three years and five industries, no year exceeds the approved share,
all labels replay identically, and at least three leading differentiators survive the declared
statistical and perturbation checks.
