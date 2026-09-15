# P3-STAGE-020-C blind-reference four-party portfolio review

Date: 2026-09-16  
Review type: same-origin simulated four-perspective portfolio review  
Result: `CLOSE_P020_C_NO_PASS_RETAIN_1_12_RESEARCH_SEMANTICS_AND_RETURN_TO_HIGH_SIDE_PORTFOLIO_ROUTE`

Machine result: `../../config/stage-learning/stage-downside-lifecycle-reference-result.v1.json`  
Frozen reference contract: `../../config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json`  
Frozen seal/aggregation contract: `../../config/stage-learning/stage-downside-lifecycle-reference-seal.v1.json`

## Evidence reviewed

P020-C used the exact immutable P024 development ceiling: 187 rows / 179 economic-identity clusters,
with early STAR retained as a 19-row capacity-limited audit/stress stratum. The sole packet Job built
187/187 candidate-blind, future-blind T-only Trend Evolution packets. No candidate, threshold,
security identity, cluster identity, future row or protected partition was visible to either reviewer.

Two isolated same-origin roles (`short_term`, `fund`) reviewed the same frozen packets and were sealed
into separate immutable ledgers before the resolver could combine them. The final aggregate result is:

- Stage12 row resolution: 78 TRUE, 74 FALSE, 35 AMBIGUOUS;
- Stage12 clusters: 149 definite, 76 positive, 73 negative, 30 unjudgeable;
- Stage12 unjudgeable rate: `30/179 = 16.76%`, above the frozen 15% ceiling;
- every Era × Board cell contains definite Stage12 references;
- Stage1 within resolved Stage12 context: 76 applicable clusters, 71 definite, 30 positive,
  41 negative and 5 unjudgeable;
- Stage1 unjudgeable applicable rate: `5/76 = 6.58%`;
- all frozen gates pass except `maximum_stage12_unjudgeable_cluster_rate`;
- P020-D preregistration, candidate scoring, resource reopen and protected reads remain unauthorized.

The fund transport contained one non-semantic serialization defect: array positions 149 and 150 were
emitted in reverse physical order while both rows retained their correct unique explicit `index` and
`case_id`. The submission transport was deterministically sorted by that explicit index before seal;
no opinion value, reason or case binding changed.

## Short-term trading perspective

**Decision: retain the 1/12 semantic architecture, but do not force the Stage12 context over the
remaining transition zone.**

The result is not a concept collapse. Stage12 has 149 definite economic-identity clusters with almost
perfect positive/negative balance and representation in every Era × Board cell. Stage1 is also
recognizable inside agreed downside contexts. For short-horizon interpretation, however, the 35
ambiguous rows are exactly where a recent reversal/deceleration can conflict with the medium-horizon
downside state. Converting those cases to a hard context merely to cross the 15% gate would erase a
real market transition boundary.

**Veto:** do not recode ambiguous transition rows, do not add a third deciding reviewer after seeing
the result, and do not change 15% to match 16.76%.

## Fund-management perspective

**Decision: keep Stage12 as research context rather than a qualified portfolio state.**

A portfolio-state vocabulary needs stable interpretation across horizon perspectives. The balanced
76 positive / 73 negative definite clusters show that the concept is not trivially one-sided, but the
remaining roughly one-sixth context disagreement is material for allocation and risk interpretation.
Stage1's stronger nested sufficiency is useful evidence, yet it cannot become a standalone state when
its own contract says the condition is meaningful only inside an agreed downside context.

**Veto:** no standalone Stage1 promotion, no prevalence inference from the development panel, and no
claim that the Stage12 gate was effectively passed because the miss was numerically small.

## Quantitative-methodology perspective

**Decision: preserve the exact no-pass and stop local optimization.**

The frozen reference contract was intentionally established before opinions. It required at most 15%
unjudgeable Stage12 clusters. The observed `30/179` fails that criterion while every other gate passes.
That makes the result especially informative: the remaining blocker is semantic cross-perspective
agreement, not sample capacity, positive/negative scarcity, board/era coverage, or Stage1 reference
sufficiency. Lowering the gate, collecting selective extra opinions or opening replacement resources
would be post-result optimization.

If 1/12 is revisited in a later research generation, the revisit must be justified by materially new
information such as a new fact family, genuinely independent reference source or redesigned semantic
question. Reusing the same panel to tune wording/thresholds until the 15% gate passes is prohibited.

**Veto:** no P020-D candidate topology, no same-panel semantic repair loop, no P025 resource repair.

## Data/governance perspective

**Decision: close P020-C, keep protected surfaces closed, and return to the third-group portfolio.**

The resource/admission line already ended at P024. P020-C consumed its final semantic-development
ceiling exactly as frozen and produced immutable reviewer ledgers. No operational reason remains to
open more data for this branch. Preserved 2021, 2022-2024 and 2025 remain unread, and no daily,
strategy or trading authority is created.

The parent P019 portfolio still has an untested subgroup: high-side `8/9`, already reclassified by
P3-STAGE-003 as modifiers rather than peer primary states. The accepted Trend Evolution Fact Family
already supplies the directional/velocity substrate needed to preregister a Stage8
`upside acceleration / extension` modifier. Stage9 remains blocked until a minimal high-side activity
relation is separately justified; it must not be inferred from price-only evidence.

**Veto:** do not reopen 1/12 resources and do not bundle Stage9 into a price-only Stage8 experiment.

## Joint decision

The four perspectives agree:

```text
P3-STAGE-020-C: CLOSED_BLIND_REFERENCE_INSUFFICIENT_NO_RESOURCE_REOPEN_REVIEWED
P3-STAGE-020-D: NOT_OPENED
P3-STAGE-025: NOT_OPENED_AS_RESOURCE_REPAIR
Stage12: RETAIN_RESEARCH_CONTEXT_NOT_QUALIFIED
Stage1:  RETAIN_SUPPORTED_NESTED_CONDITION_NOT_STANDALONE_QUALIFIED
Next portfolio route: P3-STAGE-026 HIGH_SIDE_MODIFIERS_8_9
```

The next route is **not** permission to search Stage8/9 thresholds immediately. P3-STAGE-026 must
first separately preregister the high-side semantic architecture and exposure/resource boundary. Its
first bounded question is whether accepted Trend Evolution Facts can support an advancing-context +
upside-acceleration/extension modifier reference experiment. Stage9 stays parked until a minimal
price × activity relation has its own justification and fact contract.

## Claim ceiling

This review does not qualify Stage12 or Stage1, does not reject either market concept as nonexistent,
and does not authorize candidate scoring, preserved evaluation, daily inference, strategy use or
trading. It establishes that the current two-role T-only development protocol did not meet the frozen
Stage12 reference-sufficiency gate, while Stage1 showed sufficient nested reference structure inside
agreed downside contexts. The active third-group research budget therefore moves on rather than
optimizing this local miss.
