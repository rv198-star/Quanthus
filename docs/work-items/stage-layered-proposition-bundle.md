# P3-STAGE-028 — Layered Stage Proposition Bundle Contract

Design status: approved
Delivery gate: accepted first-milestone MVP; phase boundary reached

Parent: `stage-whole-vocabulary-portfolio-synthesis.md`
Governing architecture: `../design/stock-stage-vocabulary-portfolio.md`
Design: `../design/stage-proposition-bundle.md`
Machine contract: `../../config/stage-learning/stage-proposition-bundle.v1.json`
Machine result: `../../config/stage-learning/stage-proposition-bundle-result.v1.json`
Acceptance: `../acceptance/stage-proposition-bundle-mvp-20260916.md`
Review: `../reviews/stage-p028-layered-bundle-four-party-review-20260916.md`

## Goal

Replace the abandoned flat `primary_stage` implementation assumption with a versioned representation
contract that can carry evidence-bound market propositions without promoting their qualification level.

The first version should represent, separately:

- context / structural-state assertions;
- event / nested-condition assertions;
- modifiers;
- evidence grade and reason/provenance;
- ambiguity / abstention;
- control sentinel;
- references to authoritative continuous Facts.

`primary_stage` must be absent or null in v1.

## Non-goals

P028 does not rerun any Stage experiment, qualify a parked/research code, learn thresholds, arbitrate a
Top-1 stage, open a holdout, publish a LabelCard, enable daily inference, feed production experts or
strategy, or trade.

## First gate

Before implementation, separately freeze:

1. assertion schema and stable proposition ids;
2. evidence-grade enum and authority semantics;
3. coexistence / same-layer ambiguity rules;
4. `ABSTAIN` versus neutral `0` versus control `X` separation;
5. which already accepted projections may appear on governed development/shadow surfaces;
6. consumer claim ceiling and explicit prohibition on representation-as-promotion.

No market data is required for this first gate.

## Delivered result

The v1 contract and implementation are complete. All historical market codes 0..12 map to stable
proposition ids with P027 evidence grades and authority frozen in the catalog. Bundle construction and
deep validation enforce context/condition/modifier layering, parent and provenance requirements,
Fact-reference closure, same-layer ambiguity, neutral/ABSTAIN/X separation, deterministic
serialization and `primary_stage=null`.

The non-production `development_shadow` consumer view admits only the accepted Stage10/11 development
projection and can represent Stage10 context + Stage11 nested rebound simultaneously. Research and
parked propositions remain audit-only and are filtered rather than promoted.

Five executable contract examples cover nested 10/11, neutral 0, ABSTAIN, X and an audit-only Stage5/8
research combination.

## Phase boundary

P028 satisfies the agreed **individual-stock stage recognition MVP phase-1** goal at the
representation/governance level. It does not automatically open P029 or another per-code experiment.
The next action is to inspect this delivered MVP and decide a new phase objective before any additional
market read, validation, runtime generation or expert integration is authorized.
