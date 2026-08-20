# LLD 06: Event Library and Outcome Dataset

| Field | Value |
| --- | --- |
| Phase | P4 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | `P4-EVENT-001` accepted; `P4-EVENT-002` V2.1/correction source authority, thin P4 Research ADM, checksum-bound 259-security historical industry supplement, development-only economic-availability projection, streaming forward scan, reconciliation, and P1/P2-backed T+1 entry partition implemented; production A/B acceptance remains open |
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
deterministic static-level-one-industry concentration sampling. The industry mapping remains the
versioned `static_non_pit_concentration_only` contract already authorized by P0; the physical P4
path materializes that static grouping from the current DWD V2.1 company-industry classification
onto governed DWD `security_id` values. It is never a candidate signal or reconstructed historical
membership.

The exact card digest is human-approved and bound to the approval record in GitHub Issue #73.
`CandidateRuleCard.require_stage_b_authority()` now admits Stage B only for this approved semantic
payload. A semantic or threshold edit changes the digest and requires a new rule version,
experiment identity, and renewed human approval rather than in-place tuning.

The event contract implementation is under `src/quanthus/event_library/`. The small upstream ADM
contract is under `src/quanthus/research_adm.py` and `config/p4-research-adm.v1.json`. Exact-key
parsing rejects unknown or outcome-bearing candidate inputs and record extensions; canonical
serialization verifies semantic digests; the event record enforces `knowledge_cutoff <= T close`;
and record-set validation rejects duplicate identities or one event assigned to multiple splits.

### 3.2 Forward-scan source and first implementation slice

`config/p4-event-scan-source.research.v1.json` now binds the accepted
`dwd-base-202607-v2.1` plus `dwd-base-202607-v2.1-correction-20260813-001` and reuses the exact
research-only direct-DWD contract in `config/replay-data-provider.development.v2.json`. The source
contract is development-only and requires the immutable Base/correction identities. Source-policy
2.1 projects static concentration membership from `dwd_company_industry_membership` and
`dwd_industry_node` onto governed DWD securities, instead of treating the P3 1,687-security replay
mapping as a P4 full-market identity authority. The old dual-lane DWD/DWS, operational or portable
ADS, release sets, and mutable `CURRENT` pointers remain forbidden. Post-2021 Daily increments are
not inputs to the initial 2012-2021 event scan.

V2.1 closes the Base-remediation work but intentionally does not claim that its reconstructed
historical `knowledge_from` values were published with the same content on the original dates. P4
therefore keeps raw DWD `knowledge_from` as immutable lineage and adds the separately versioned
`p4-economic-availability-development-v1` projection for the authorized development partition.
The mapping is table-specific: daily bars and calendars become knowable at session close; listing
and security identity must already be effective at that close; corrected tradability becomes
knowable at session close; adjustment factors are used only as a conservative effective-date
candidate exclusion; and T+1 price-limit evidence remains an execution-open input. The projection
must never rewrite the Base or discard raw knowledge lineage.

This is a retrospective development-research authority, not historical publication proof. Its
machine contract keeps `per_decision_publication_proven=false` and explicitly forbids arbitration,
permanent-blind, simulation, governance admission, and execution authorization. The frozen
`p4-event.price-volume-state-transition@1.0.0` rule card and its approved digest are unchanged: no
threshold, feature, split, isolation, or concentration semantic is modified by the source recovery.
Any future projection that changes that exact logical candidate-input contract requires a new rule
version and renewed approval.

`src/quanthus/event_library/scan.py` implements the outcome-blind chronological transform over
strict typed daily inputs. Activity ratios use exact rational arithmetic, so candidate membership
has no hidden floating-point or rounding policy. The production API streams one stable listing
series at a time and retains only raw triggers globally, avoiding a full-market Python row materialization.
It permits non-overlapping relisting intervals for one security while keeping cooldown/isolation at
the security level. Static industry membership is intentionally applied only after raw-trigger
formation for concentration control and is keyed by the same governed DWD `security_id` used by
event identity. A physical scan now fails closed if any isolated raw trigger lacks static-industry
coverage; missing membership cannot silently turn a valid full-market trigger set into an empty or
partial event library. Before the expensive forward scan, the physical path also audits industry
coverage over the complete projected candidate-security universe and records a bounded
`static_industry_coverage_audit.json`. Missing securities are classified by DWD source failure and
by whether they still have a current listing; any full-universe gap fails closed before trigger
scanning. The implementation reconciles evaluated rows, raw triggers, same-security isolation,
deterministic concentration sampling, split rejection, and retained candidate events with zero
unexplained loss. It does not accept returns, labels, fills, or post-T features.

`src/quanthus/event_library/physical_scan.py` now implements the bounded physical path. It reuses
the accepted Direct-DWD job-local provider, carries DWD internal security/listing/company identity,
retains exact Decimal OHLC/turnover alongside the legacy replay columns, preserves raw DWD
knowledge/source lineage, and applies correction-001 tradability. Before event scanning it seals a
thin `P4 Research ADM` under `adm/`: `candidate_daily.parquet`, `static_industry_mapping.json`,
`static_industry_source_receipt.json`, `static_industry_coverage_audit.json`, and `adm.json`.
Current DWD industry membership remains the first mapping authority; the 259 historical-only
securities are filled only by ADM 1.1's checksum-bound fixed-date BaoStock `证监会行业分类`
supplement. Its complete selected source content must reproduce
`sha256:20b51d52070b8976f9f8989ff0f54124aea098ca749167bedbddbed947c3196b`
before scanning continues. The ADM is application data only: it is not
DWD, generic DWS, operational ADS, or an execution authority. Event identity binds the exact ADM
snapshot manifest. The Event Library then adds events, entry partition, summary, manifest, and READY
around that ADM. `run-p4-event-scan` builds the combined immutable Run package and
`verify-p4-event-scan` independently verifies its physical inventory and digests.

The T+1 partition reuses the exact `P1DailyExecutionEvidence` constructed by the historical reader
and the same P1-to-P2 buy-open adapter and rejection mapping used by `BacktestEngine`, including the
accepted `NON_TRADING_OBSERVED` cancellation state. Candidate and source identities remain separate:
`CandidateEntryEvidence.candidate_snapshot_id` must equal the event's exact ADM snapshot, while the
original P1 execution-date `evidence.snapshot.snapshot_id` is preserved as source lineage and
published as `entry_source_snapshot_id`. A distinct source snapshot is therefore valid; relabelling
P1 evidence as the ADM snapshot is forbidden. It classifies executable versus `not_executable`
without creating an order or fill and without changing the candidate event id.
The remaining #74 acceptance work runs through the statically admitted
`quanthus.p4-event-scan.v1` Event Worker (`queue=event`) rather than granting DevSpace direct DWD
access. The Worker receives the protected DWD Base/correction and frozen research references as
read-only mounts, writes only `/data/event-runs`, and seals one immutable Event Run per Job. Two
Jobs with identical governed `input_ref` and different idempotency keys must reproduce identical
candidate counts, event identities, manifest SHA-256, and content digest; neither is permission to
open a stronger PIT partition.

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
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4-EVENT-001 | V2.1/correction source and development availability projection accepted as implementation authority; pure scan and entry partition implemented; physical event projection and full development corpus pending |
| P4-LABEL-001 | Freeze three-class and structural-break cards | Governance proposal | Reproducible label corpus |
| P4-REPORT-001 | Produce daily group comparison report | Event dataset | Three leading features |

## 9. Acceptance

The library spans at least three years and five industries, no year exceeds the approved share,
all labels replay identically, and at least three leading differentiators survive the declared
statistical and perturbation checks.
