# P3-EXPERT-003: First Target, Resolver, and Assessment Expert

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-EXPERT-003` |
| Design status | approved |
| Delivery gate | dependencies accepted; blocked by the exact `P3-EXPERT-003` target decision |
| Implementation readiness | outcome-blind target reuse proposal ready for human decision |
| Result access | forbidden until the target decision record is sealed |

## 1. Outcome and boundary

Freeze one independently resolvable market-data target, implement one daily technical assessment
expert and resolver, and publish its development sample ledger and assessment scorecard. The
default candidate is a trend-stability expert because its observation-time evidence and future
settlement data already exist in the admitted DWD/P2 path.

This issue proves that one expert claim is falsifiable and settleable. It does not claim portfolio
value, define the formal strategy, tune against arbitration or permanent-blind data, compare unlike
targets, or promote the expert.

## 2. Target-card reuse and human decision

`P4-LABEL-001` owns outcome and structural-break card semantics used by the event library. This
issue must inspect and reference an accepted card by id, version, and digest when its subject,
anchor, horizon, outcome, and missing/censor rules match. It must not copy the same fields into an
expert-specific card and allow them to drift.

If no P4 card has the required semantics, the first deliverable is an outcome-blind target decision
packet that records why reuse is invalid and proposes a new expert target card. The packet compares
at least a simple terminal-return target, a benchmark-relative target, and a path-aware
return/drawdown target. It contains no realized labels, base rates, scores, or sample counts.

A human must sign exactly one option and freeze:

- subject kind and eligible population;
- intended first-strategy applicability population and its immutable predicate/digest;
- T-close observation anchor and exact trading-session horizon;
- outcome type, unit, thresholds or ordinal bins;
- reference price and benchmark semantics;
- exclusions, suspension/delisting treatment, missing rules, and partition-tail censoring;
- primary metric, naive baseline, hypothesis family, slices, uncertainty method, and stop
  conditions.

No resolver, expert threshold, or result-bearing query may run before that decision artifact is
sealed. A script may validate completeness and digests but cannot choose the target.

## 3. Inputs and owned paths

Inputs are the accepted v2 protocol and Worker handler, selected P4 target-card digest or approved
non-reuse decision, an exact effective DWD development snapshot, admitted PIT feature definitions,
and the accepted trading calendar. Configuration and fixture work may be prepared while the Worker
dependency is pending, but no result-bearing DWD read or Issue acceptance may bypass it.

The issue owns:

- `config/expert-evaluations/targets/`
- `config/expert-evaluations/experts/`
- `config/expert-evaluations/specs/`
- `src/quanthus/expert_evaluation/target_cards.py`
- `src/quanthus/expert_evaluation/resolver.py`
- `src/quanthus/expert_evaluation/scoring.py`
- `src/quanthus/expert_evaluation/experts/trend_stability.py`
- `tests/unit/test_expert_target_resolver.py`
- `tests/unit/test_trend_stability_expert.py`
- `tests/acceptance/test_expert_development_scorecard.py`

The implementation may consume feature-registry and direct-DWD reader interfaces. It does not
modify `src/quanthus/event_library/**`; the P4 card is referenced through an adapter at its public
contract boundary.

## 4. Observation contract

The expert runs after the accepted T close and receives only evidence visible at its declared
`knowledge_cutoff`. It emits one typed raw assessment or an explicit abstention for every eligible
subject. Its observation contains the feature lineage and target/spec digests but no future price,
settlement state, or target score.

The first expert may use only admitted market price, volume, volatility, breadth, trading calendar,
and security-state inputs. News, financial statements, current-record company facts, and any domain
without proven historical publication semantics are unavailable. Missing required observation data
causes an abstention with a stable reason; it is not imputed from future or cross-sectional current
state.

The assessment algorithm and the policy mapping are separate configurations and versions. This
issue may implement the mapping required for later strategy composition, but the scorecard judges
the raw assessment against the selected target. Existing v1 `confidence` is not reused as a
probability.

## 5. Resolver contract

The resolver is independent: it accepts frozen observations plus future DWD/trading-calendar
evidence and never imports or calls the expert or its policy mapping. It resolves the Nth eligible
trading session from the selected target card, applies exact price/action semantics, and writes a
new `OutcomeSettlement` sidecar.

For each eligible observation it must choose exactly one state:

- `settled` with the target value and registered score;
- `pending` because the complete horizon is not mature;
- `censored` because the authorized partition ends first or the card declares terminal censoring;
- `unavailable` because required future evidence is absent or semantically inadmissible.

Suspension, delisting, no executable next open, and corporate action treatment follow the target
card and remain separately countable. They are never converted into success, neutral return, or a
correct abstention by convenience.

## 6. Scorecard construction

The development scorecard closes the complete sample ledger and reports eligibility, applicability,
abstention, settlement, pending, unavailable, not-executable, and censor counts. It reports the
selected primary metric against the frozen naive baseline, coverage and selective-abstention
behavior, temporal/regime slices, and event/time-block or clustered uncertainty appropriate to the
horizon dependence.

The scorecard contains assessment and decision-change evidence only. It does not embed portfolio
effects or reference a future attribution release; `P3-EXPERT-004` later composes both immutable
artifact families through one evidence-package manifest.

The intended first-strategy population is a mandatory predeclared slice, not a subset selected
after scores are visible. If it cannot be declared before the formal strategy card, this Issue may
prove only protocol settleability and `P3-STRAT-002` must block action use until a new evaluation
spec produces evidence for the exact strategy population.

Probability targets additionally require Brier or log loss and calibration; ordinal and continuous
targets use only their registered target-appropriate metrics. Raw accuracy is supplementary. Rows
sharing overlapping horizons, dates, securities, or market shocks are not presented as independent
sample size.

## 7. Implementation route

1. Produce and human-approve the outcome-blind target decision and immutable card reference.
2. Implement exact target-card loading and cross-project reference validation.
3. Implement the observation expert and explicit abstention reasons over a small PIT fixture.
4. Implement the independent resolver and horizon-boundary cases.
5. Build the full sample ledger and registered metric/uncertainty scorecard.
6. Submit one development-only Worker Job after all configuration digests are frozen; every DWD
   read, observation, settlement, and score calculation occurs in the isolated attempt process.
7. Publish limitations and stop-condition disposition without changing the target or expert in
   place.

## 8. Failure semantics

Target-card drift, a result query before target approval, future input in observation lineage,
cross-partition horizon access, missing publication semantics, unregistered metric, incomplete
ledger, insufficient event count, or inconsistent resolver state blocks scorecard publication.
Insufficient evidence produces `inconclusive`; it does not lower a threshold or narrow population
inside the same version.

## 9. Verification and acceptance

Tests cover PIT cutoffs, target-card digest drift, T/Nth-session boundaries, holidays, suspension,
delisting, actions, missing future bars, partition-tail censoring, deterministic observation ids,
resolver independence, ledger closure, overlap-aware uncertainty, and byte-identical semantic
artifacts across operationally distinct reruns.
Negative import tests prove the resolver cannot call the expert and the observation path cannot
open outcome material.

Acceptance artifacts are the human target decision, target/assessment/policy/spec digests, Worker
Job/Run references, leakage test report, sample ledger, development scorecard, limitations,
resource cost, and deterministic semantic-artifact checksums. The acceptance statement is limited
to assessment quality and settleability.

## 10. Rollback

Disable the expert/spec version in the registry and stop new evaluation Jobs. Preserve the target
decision, observations, settlements, and scorecard. Any change to target, horizon, population,
abstention, or algorithm creates a new version rather than rewriting the failed evidence.
