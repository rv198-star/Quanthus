# P3-STAGE-020 — Downside Lifecycle Proposition Pilot

Design status: approved semantic architecture; resource route closed after P024 review
Delivery gate: P020-C ready for separate capacity-limited preregistration only

Parent: `stage-third-group-portfolio-rebaseline.md`  
Machine concept: `../../config/stage-learning/stage-downside-lifecycle-concept.v1.json`  
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
| P3-STAGE-020-C | Build candidate-blind T-only review packets and seal context/nested-condition references | P3-STAGE-020-B, P3-STAGE-024 | ready for separate preregistration only; exact P024 187-row / 179-cluster panel is final resource ceiling, early STAR is a 19-row capacity-limited audit/stress stratum, no replacement/new resource read |
| P3-STAGE-020-D | Freeze finite candidate topology and independently score context / nested condition | P3-STAGE-020-C | blocked; no top-1 primary-stage compression |
| P3-STAGE-020-E | Four-party review and closeout | P3-STAGE-020-D | blocked; any preserved evaluation requires a separate later gate |

## Resource discipline

Reuse the accepted P013 Era×Board exposure-aware model, but resource/admission search is now closed by the P024 post-failure portfolio review. The exact immutable P024 187-row / 179-cluster admission-ready panel is the final P020 development resource ceiling. Seven cells contain 24 rows; early STAR contains 19 distinct within-cell identities and remains a capacity-limited semantic stress/audit stratum. The eight cross-era repeated identities are explicit clusters and never increase the independent-evidence denominator above 179. P020-C may not random-append identities, rerun admission, replace a missing/unjudgeable packet, merge/drop STAR, or open another resource stage.

## Boundaries

No P020-A threshold search, scoring, new review opinions, primary-stage output, LabelCard, preserved 2021, 2022–2024, 2025, daily, strategy or trading authority.
