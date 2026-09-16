# P3-STAGE-028 — Layered Stage Proposition Bundle Contract

Design status: draft successor scope  
Delivery gate: ready for schema/governance preregistration only

Parent: `stage-whole-vocabulary-portfolio-synthesis.md`  
Governing architecture: `../design/stock-stage-vocabulary-portfolio.md`

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
