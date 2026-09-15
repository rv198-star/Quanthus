# P3-STAGE-020 — Downside Lifecycle Proposition Pilot

Design status: approved semantic architecture; active qualification route closed after P020-C no-pass
Delivery gate: closed research semantics; P020-D not opened

Parent: `stage-third-group-portfolio-rebaseline.md`  
Machine concept: `../../config/stage-learning/stage-downside-lifecycle-concept.v1.json`  
P020-C contract: `../../config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json`  
P020-C route: `stage-downside-lifecycle-blind-reference.md`  
P020-C result: `../../config/stage-learning/stage-downside-lifecycle-reference-result.v1.json`  
P020-C review: `../reviews/stage-p020c-blind-reference-four-party-review-20260916.md`  
Fact substrate: `../design/structure-trend-evolution-facts.md`

## Goal

Return to the P3-STAGE-003 third-group plan after parking active Stage-0 optimization. Build a bounded `1/12` development route over the accepted Trend Evolution Fact Family without forcing a single primary stage.

## Semantic architecture

- **Stage-12 relation:** `downside_context` — persistent downside direction/context using only as-of direction, efficiency, return/velocity and drawdown evidence.
- **Stage-1 relation:** `deceleration_stabilization` — nested inside a downside context; uses loss of downside velocity, range position and rebound/stabilization evidence without claiming a durable bottom.
- `downside_context=TRUE` and `deceleration_stabilization=TRUE` is legal coexistence.
- A nested stabilization assertion without downside context is not Stage 1; resolve as not-applicable/unknown.
- Both false is legal; abstain/unjudgeable is preserved.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-020-A | Freeze proposition/nesting/reference semantics | P3-STAGE-019 | closed semantic freeze; no market/result access |
| P3-STAGE-020-B | Freeze Era×Board exposure/admission capacity and exact development resource plan | P3-STAGE-020-A | closed `CLOSED_FRESH_WINDOW_CAPACITY_SHORTFALL_BEFORE_ADMISSION`; exact R1 artifact shows both STAR cells have zero remaining metadata windows under the frozen rule that excludes every P014 admission-read window; no admission bars or Stage1/12 semantics were read; `../../config/stage-learning/stage-downside-lifecycle-resource-result.v1.json` |
| P3-STAGE-020-C | Build candidate-blind T-only review packets and seal context/nested-condition references | P3-STAGE-020-B, P3-STAGE-024 | closed `CLOSED_BLIND_REFERENCE_INSUFFICIENT_NO_RESOURCE_REOPEN_REVIEWED`; sole failed gate is Stage12 unjudgeable clusters `30/179 > 15%`, all Stage1 gates pass, no gate/resource repair |
| P3-STAGE-020-D | Freeze finite candidate topology and independently score context / nested condition | P3-STAGE-020-C | closed not-opened; P020-C reference sufficiency did not pass |
| P3-STAGE-020-E | Candidate-route review / preserved gate | P3-STAGE-020-D | closed not-opened; closeout occurred directly at P020-C under the frozen failure policy |

## Resource discipline

Reuse the accepted P013 Era×Board exposure-aware model, but resource/admission search is now closed by the P024 post-failure portfolio review. The exact immutable P024 187-row / 179-cluster admission-ready panel is the final P020 development resource ceiling. Seven cells contain 24 rows; early STAR contains 19 distinct within-cell identities and remains a capacity-limited semantic stress/audit stratum. The eight cross-era repeated identities are explicit clusters and never increase the independent-evidence denominator above 179. P020-C may not random-append identities, rerun admission, replace a missing/unjudgeable packet, merge/drop STAR, or open another resource stage.

## Portfolio closeout

Stage12 is retained as `RETAIN_RESEARCH_CONTEXT_NOT_QUALIFIED`. Stage1 is retained as
`RETAIN_SUPPORTED_NESTED_CONDITION_NOT_STANDALONE_QUALIFIED`: its blind-reference gate passes inside
resolved downside contexts, but it cannot be promoted independently of the failed Stage12 parent
context. Active 1/12 qualification is parked rather than iterated on the same panel. The parent P019
third-group portfolio proceeds next to a separately frozen P3-STAGE-026 high-side-modifier route;
Stage8 may use accepted Trend Evolution Facts, while Stage9 remains blocked pending a separately
justified minimal high-side activity relation.

## Boundaries

No P020 threshold search, scoring, new review opinions, primary-stage output, LabelCard, preserved
2021, 2022–2024, 2025, daily, strategy or trading authority. The 15% gate is not lowered and P025 is
not opened as a resource-repair loop.
