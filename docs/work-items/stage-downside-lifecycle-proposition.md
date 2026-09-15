# P3-STAGE-020 — Downside Lifecycle Proposition Pilot

Design status: approved for semantic freeze  
Delivery gate: open for metadata/admission planning only

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
| P3-STAGE-020-B | Freeze Era×Board exposure/admission capacity and exact development resource plan | P3-STAGE-020-A | ready `PASS_ENGINEERING_READY_FOR_ONE_DOWNSIDE_LIFECYCLE_RESOURCE_JOB`; one admission-only Job, no semantic review yet; `../acceptance/stage-downside-lifecycle-resource-engineering-20260915.md` |
| P3-STAGE-020-C | Build candidate-blind T-only review packets and seal context/nested-condition references | P3-STAGE-020-B | blocked until B passes |
| P3-STAGE-020-D | Freeze finite candidate topology and independently score context / nested condition | P3-STAGE-020-C | blocked; no top-1 primary-stage compression |
| P3-STAGE-020-E | Four-party review and closeout | P3-STAGE-020-D | blocked; any preserved evaluation requires a separate later gate |

## Resource discipline

Reuse the accepted P013 Era×Board exposure-aware model. Do not random-append identities, do not consume reserved/preselected identities merely by discovery, and perform deterministic 61-session admission before semantic review. Candidate or reference results must never influence resource replacement.

## Boundaries

No P020-A threshold search, scoring, new review opinions, primary-stage output, LabelCard, preserved 2021, 2022–2024, 2025, daily, strategy or trading authority.
