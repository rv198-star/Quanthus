# P3-STAGE-028 Layered Stage Proposition Bundle — four-party review

Date: 2026-09-16  
Disposition: **PASS_FIRST_MILESTONE_STAGE_PROPOSITION_MVP**

## Scope reviewed

The review covers only the P028 v1 representation/governance milestone:

- `config/stage-learning/stage-proposition-bundle.v1.json`;
- `src/quanthus/stage_proposition_bundle.py`;
- `config/stage-learning/stage-proposition-bundle-examples.v1.json`;
- `docs/design/stage-proposition-bundle.md`;
- unit/contract verification.

No new market, reviewer, holdout, candidate-scoring, strategy or trading read is in scope.

## Short-term trading lens

The new representation is materially more usable than a forced one-code stage output. A trader can see
simultaneously that the market is in a structural-failure context and that a rebound condition is
active, rather than losing one of those facts to a Top-1 compression. The same mechanism can later
carry event and modifier propositions without pretending every historical code is equally qualified.

The important guardrail is preserved: an attractive research modifier such as Stage8 can be visible in
audit but is automatically excluded from the governed development-shadow consumer view. This avoids
turning an intuitive trading narrative into authority by representation alone.

**Assessment:** PASS.

## Fund-manager lens

The context/condition split fits medium-horizon portfolio reasoning better than the old flat FSM.
Persistent structural condition, transient event and high-side modifier can have different holding
periods and different confidence/evidence histories. The evidence-grade field is especially useful
because it prevents a consumer from treating a parked or research proposition as equivalent to the
accepted Stage10/11 development projection.

Neutral market code 0, epistemic abstention and data-control X are also kept separate, which avoids a
portfolio system interpreting uncertainty or invalid data as a market regime.

**Assessment:** PASS.

## Quantitative-method lens

The v1 contract is deliberately representational rather than predictive. Stable proposition ids,
strict catalog metadata, exact parent dependencies, deterministic ordering, Fact closure and canonical
serialization make it suitable for reproducible downstream experiments. The output does not expose a
probability or score that could be mistaken for market truth.

The most important methodological property is that authority is not caller-controlled: evidence grade,
layer and maximum surface are frozen by the catalog. Same-layer multiple TRUE propositions require
AMBIGUOUS status; cross-layer coexistence remains legal. `primary_stage` is hard-null.

**Assessment:** PASS.

## Data/governance lens

Every assertion must declare the exact Fact references and provenance it consumes, while the top-level
Fact set must equal the assertion union. This gives a closed representation lineage instead of a loose
label payload. Stage4 and Stage10/11 additionally require the relevant temporal/confirmation provenance.

The `development_shadow` surface is explicitly non-production and admits only the accepted Stage10/11
development projection. Production expert, strategy and trading authority remain false. No new DWD or
protected-partition access was required to build or test P028.

**Assessment:** PASS.

## Joint decision

P028 meets the agreed phase goal: Quanthus now has a concrete, executable representation that can carry
layered stock-stage propositions into a bounded expert-facing development view without requiring a
flat primary-stage classifier.

The milestone is therefore accepted as:

`PASS_FIRST_MILESTONE_STAGE_PROPOSITION_MVP`

This does **not** mean the historical stage vocabulary is fully qualified. It means the project has a
usable architecture and executable contract around the evidence that already exists.

## Stop rule

P028 completion is a deliberate phase boundary. No P029 or new per-code research route is opened
automatically. The next action is product/value review of the delivered MVP: inspect the Bundle shape,
expert-development view and current proposition coverage, then separately decide whether the next
phase should deepen a missing proposition, add real runtime generation, perform external validation,
or connect a bounded expert workflow.
