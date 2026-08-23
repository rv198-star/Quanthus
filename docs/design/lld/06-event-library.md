# LLD 06: Event Library and Outcome Dataset

| Field | Value |
| --- | --- |
| Phase | P4 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | `P4-EVENT-001`, `P4-EVENT-002`, `P4-LABEL-001`, `P4-REPORT-001`, and `PERF-BATCH-001` accepted through deterministic production A/B evidence; the job-local reuse A/B built one Direct-DWD projection and two scope-bound TMP Feature Panels per Job while reproducing all three governed package trees byte identically |
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
The physical reader requests one fixed Parquet column order and converts each row
directly from its positional tuple. The ADM writer already seals rows in scan-key order, so the
reader avoids a second full-market sort and instead validates that physical order while streaming;
any regression fails closed. The immutable static-industry membership map is indexed once per scan,
not rebuilt for every decoded row. For each listing series, static validity and exact range/
turnover values are computed once per row; two sorted 20-session windows maintain the exact
rolling medians by deterministic insertion and removal. Full 22-session evidence-digest tuples are
constructed only after all exact thresholds pass. This changes scan complexity and allocation,
not `Fraction` arithmetic, PIT checks, reason precedence, trigger membership, or evidence identity.
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

`P4-LABEL-001` has two human-approved outcome-blind semantic digests. Approval is bound to
`https://github.com/rv198-star/Quanthus/issues/75#issuecomment-5366610770`; the cards are active for
the authorized 2012-2021 development label-corpus build, while arbitration and permanent-blind
reads remain blocked.

The approved outcome card is
`p4-event.outcome-first-passage@1.0.0`, digest
`sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca`.
It anchors at the accepted actual executable T+1 open and observes exactly 20 market sessions,
counting the entry session as session one. A hypothetical one-share economic-value path applies
accepted cash-dividend and bonus-share actions before each session's threshold checks. Transaction
costs are excluded because the card labels the price path rather than a realizable strategy trade.
The favorable threshold is +15% and the adverse threshold is -7%. The first terminal session wins;
if favorable and adverse thresholds both cross on one session, or a structural break and favorable
threshold occur on one session, `failure` wins conservatively. If no terminal condition occurs by
session 20 the settled result is `neutral`. A partition tail that cannot supply all 20 sessions is
`censored`; semantically inadmissible future evidence is `unavailable`. Neither state is coerced
into the three-class corpus. Suspension consumes a market-session slot but does not create a price
threshold without a valid bar.

The approved independent structural-break card is
`p4-event.structural-break-trend@1.0.0`, digest
`sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc`.
It does not call any expert or strategy action mapper. At each post-entry market-session close it
triggers when `(total_return_index < sma60 and sma20 <= sma60)` using the accepted
corporate-action-normalized total-return semantics, or when an authoritative listing termination
becomes effective. Suspension alone is not a structural break. Missing required structural evidence
produces `unavailable`, not failure or neutral.

The approved cards keep the 22-session split overlap unchanged because the 20-session outcome
window fits within it. Synthetic counterexamples freeze overlap ordering, neutral settlement,
not-executable inheritance, censoring, and unavailable-evidence behavior.

`src/quanthus/event_library/label_corpus.py` now implements the authorized development build as an
immutable sidecar to a sealed Event Library. It reuses `BaselineFeaturePanel.build` and
`config/p3-baseline-features.research.v1.json` for the accepted corporate-action-normalized
`total_return_index`/SMA20/SMA60 semantics rather than creating a parallel structural-feature
implementation. Threshold evidence uses the event's actual T+1 executable open and a hypothetical
one-share economic-value path. A new holding does not receive an action effective before its entry
open; accepted cash/bonus actions strictly after entry update the path before that session's
threshold checks. Suspension consumes a session but does not fabricate price or structural
evidence. Missing required non-suspension evidence becomes `unavailable`.

The existing static `event-worker` remains the only P4 worker and still admits only
`quanthus.p4-event-scan.v1`. Its governed input now also binds the outcome card, structural-break
card, and P3 feature registry. One lease-governed `run-p4-labeled-event-scan` child first rebuilds
the Event Library and then publishes `label-corpus/{labels.jsonl,summary.json,manifest.json,READY}`.
Run manifest schema `quanthus.p4-event-run.v2` binds both independent package digests and rejects
recovery unless both verifiers pass, event counts match, and inherited `not_executable` counts
reconcile. Production A/B acceptance now proves identical governed input reproduces the same six
outcome-state counts and identical Event/Label content and manifest digests; evidence is recorded in
`docs/acceptance/p4-event-label-corpus-production-ab-20260821.md`.

## 7. Comparative report

`P4-REPORT-001` freezes `p4-event.daily-group-comparison@1.0.0`, semantic digest
`sha256:4b7e6f931b06615940a9a72a5bd05f800be40f825716093e04637ab5fd8134a0`, before the
real development report is read. Its primary question is which observable pre-entry feature path
most reproducibly separates `positive` from `neutral`; `failure` remains a full contextual group
rather than the primary ranking target. `not_executable`, `unavailable`, and `censored` remain in
candidate-to-report attrition and cannot be silently coerced into the three-class comparison.

The report examines T through T-20 and reuses the accepted P3 feature panel rather than inventing
new price semantics. Seven dimensionless comparison features are frozen: price/SMA20 minus one,
SMA20/SMA60 minus one, momentum20, total-return price/prior-high20 minus one, RSI14, volume ratio20,
and realized volatility20. For each feature, lag, and outcome it emits sample count, mean, standard
deviation, quartiles, and median. Pairwise comparisons emit Hedges g, mean difference, a normal
95-percent confidence interval for that difference, and two-sided normal-approximation p-values.
Benjamini-Hochberg FDR is applied independently to the complete 147-test matrix for each contrast.
Publication aggregation fixes DuckDB to one execution thread and preserved insertion order so
floating-point reductions have one reproducible evaluation order; this changes no statistical
threshold or feature semantics.

Leading-feature selection is predeclared rather than chosen after inspection. A primary
`positive_vs_neutral` feature must have an FDR-significant peak with at least 500 observations per
group, a first reliable divergence consisting of two consecutive same-direction FDR-significant
lags, the same effect direction in both deterministic event-id hash halves with at least 200
observations per group/half, and a same-direction adjacent lag with q <= 0.10. Surviving features
rank by absolute peak Hedges g and stable feature id; exactly the first three become the leading
features. This ranking is descriptive research evidence, not a trading rule or promotion claim.

`src/quanthus/event_library/comparison_report.py` materializes
`comparison-report/{group-distributions.jsonl,pairwise-effects.jsonl,feature-ranking.jsonl,summary.json,manifest.json,READY}`.
The existing Event Worker remains the only P4 worker. Its governed input now additionally binds the
report policy; one `run-p4-reported-event-scan` child builds Event Library, Label Corpus, and
Comparison Report under one lease. Run manifest schema `quanthus.p4-event-run.v3` binds all three
independent package identities and rejects recovery if their counts, code revision, or verifiers
disagree.

The composite child opens one verified Direct-DWD session for the complete Job and passes that
session to all three builders. After Event Library publication, it materializes two immutable,
scope-bound Job-local `TMP` Feature Panels: Label Corpus consumes the execution-scope panel through
`2021-12-31`, while Comparison Report consumes the decision-scope panel through `2021-12-30`.
Each is built exactly once from the shared provider projection and removed after its last consumer.

The two panels cannot be represented as one derived view under the current evaluator. Its floating
window reductions make the time-range closure part of feature-value identity: a production probe
found millions of bit-level differences on the overlapping rows when one future session was added,
even though counts, classifications, rankings, and signs were unchanged. Reusing the execution
panel for Report therefore changes the accepted Report content digest. Provider, evaluator, schema,
registry, data, scope, or selection mismatch fails closed; a scope mismatch requires a separate TMP
materialization rather than stale reuse.

Both TMP panels live only below the attempt staging root. Neither is the governed P4 Research ADM
nor a cross-Job cache. Standalone Event, Label, and Report commands retain independent
provider/panel construction, and formal cold-run A/B acceptance continues to rebuild each Job
independently. A non-governing `runtime-metrics.json` receipt records per-stage elapsed time, row
and byte counts, build/reuse disposition, total elapsed time, and peak RSS. The Worker requires one
provider projection and exactly two scope-bound panel materializations before success and exposes
the receipt in bounded Job result metadata; runtime values do not enter Event, Label, or Report
content identities.

The composite child also emits a replace-atomic, bounded `progress.json` below the attempt staging
root. Internal Event stages distinguish ADM materialization, projected-row decode, exact
per-security scan, global isolation/concentration finalization, T+1 entry binding, package
publication, and verification. Cumulative decoded rows against projected rows drive candidate-scan
progress; timing counters remain operational telemetry. The Event Worker polls this receipt and
generation-fences every PostgreSQL update. The receipt is not copied into a sealed Run, and neither
its percentage nor its timing fields define Job lifecycle authority or governed package identity.
The first optimization remains one scan process and records `worker_count=1`; process parallelism
stays deferred until its production benchmark demonstrates a remaining need.

The Event Library and P3 feature panel intentionally use different security identity namespaces.
`EventRecord.identity.security_id` remains the governed DWD identity, while the accepted direct-DWD
replay adapter keys the P3 feature panel by `listing_code`. The comparison adapter therefore derives
an explicit `panel_security_id` from each Event Library row's `listing_code` before joining T..T-20
feature rows; it never assumes the governed DWD event identity equals the replay-panel key. The
first production Report Run exposed this boundary by failing closed at T-row alignment; remediation
is recorded in `docs/acceptance/p4-event-comparison-report-identity-remediation-20260822.md` and does
not change the frozen report-policy digest.

Production A/B acceptance now proves two independent Jobs with the same Worker revision and governed
input reproduce identical Event/Label identities, byte-identical report content/manifest digests,
the same leading-feature ids, and a true report acceptance flag. Evidence is recorded in
`docs/acceptance/p4-event-comparison-report-production-ab-20260823.md`.

The independent `PERF-BATCH-001` production A/B additionally proves the complete job-local reuse
contract: each Job materialized one provider projection and exactly two scope-bound TMP Feature
Panels, then reproduced every file in all three governed package trees byte identically. Matched
recent production wall time improved by 3.92 percent, but Event scanning still consumed about 95.9
percent of receipt wall time and peak RSS remained above 16 GiB. The reuse boundary is accepted;
Issue #195 separately owns material Event-scan speed and memory optimization. Evidence is recorded
in `docs/acceptance/perf-batch-001-production-ab-20260823.md`.

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P4-EVENT-001 | Freeze candidate-source hypothesis; define event, isolation, and split schemas | Accepted P3 replay and audit contracts | Exact rule-card and split-policy digests approved; deterministic contract tests and acceptance record pass |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4-EVENT-001 | Accepted production A/B replay: 36,820 candidates, 36,022 executable, 798 not-executable, identical manifest/content digests |
| P4-LABEL-001 | Freeze three-class and structural-break cards | Governance proposal | Accepted production A/B corpus: 36,820 total, 34,641 settled three-class, identical Event/Label content and manifest digests |
| P4-REPORT-001 | Produce daily group comparison report | Event dataset | Accepted production A/B: byte-identical report content/manifest identities and the same three leading features |
| PERF-BATCH-001 | Reuse deterministic P4 prerequisites within one composite Job and publish runtime telemetry | P4-REPORT-001 | Accepted production A/B: one provider projection and two scope-bound TMP Feature Panels per Job, three byte-identical governed package trees, and 3.92 percent improvement against matched recent production Jobs |
| PERF-EVENT-001 | Accelerate exact Event scanning and expose internal stage progress | PERF-BATCH-001 | Reference-equivalent scan; same-revision production A/B preserves all accepted package identities while recording lower wall time, peak RSS, and internal stage timings |

## 9. Acceptance

The library spans at least three years and five industries, no year exceeds the approved share,
all labels replay identically, and at least three leading differentiators survive the declared
statistical and perturbation checks.
