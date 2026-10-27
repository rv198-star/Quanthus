# Stock Stage Vocabulary Portfolio

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted for architecture/governance only |
| Work item | `P3-STAGE-027` |
| Machine synthesis | `../../config/stage-learning/stage-whole-vocabulary-portfolio-synthesis.v1.json` |
| Review | `../reviews/stage-p027-whole-vocabulary-portfolio-review-20260916.md` |
| Supersedes | flat single-valued implementation interpretation of `stock-stage-state-machine.md` |
| Successor | `P3-STAGE-028` layered Stage proposition bundle contract |

## 1. Decision

The historical stock-stage vocabulary is no longer modeled as twelve mutually exclusive peer stages.
The name "12-state model" continues to refer to historical directional codes `1..12`; neutral market
code `0` remains separate. Those identifiers are preserved for traceability, but later evidence has
shown that they occupy different semantic layers.

The implementation architecture is therefore a **layered proposition portfolio over continuous Facts**:

1. **context / structural-state assertions** — persistent background or structural conditions;
2. **event / nested-condition assertions** — observations that can occur inside a context and can turn
   on/off without implying a primary-stage transition;
3. **modifiers** — orthogonal descriptive/risk annotations over another context;
4. **epistemic/control outputs** — `ABSTAIN` and `X`, which are not market stages.

Cross-layer coexistence is legal. Same-layer ambiguity is preserved. No `primary_stage` compression is
part of this design.

## 2. Why the flat model is superseded

Three accepted project results directly contradict global mutual exclusivity:

- P3-STAGE-002 formalizes Stage10 as persistent structural-failure **context** and Stage11 as a nested
  rebound **condition**; `{10,11}` may be true together and `primary_stage` stays null.
- P020 models Stage12 as downside **context** and Stage1 as nested deceleration/stabilization; Stage1's
  conditional evidence can be useful even though the Stage12 reference route ultimately no-passes.
- P3-STAGE-003/P026 reclassify Stage8/9 as high-side **modifiers**, not peer primary states.

The remaining codes also have heterogeneous evidence grades: 3/4 are development event-phase
candidates, 5/6/7 are parked with continuous Facts retained, 2 is strong exploratory, and Stage0
primary qualification is parked.

Continuing to force one slot would therefore discard information and create a new arbitration problem
without qualified reference truth.

## 3. Portfolio matrix

| Code | Historical name | Layer / role | Current disposition |
| --- | --- | --- | --- |
| `0` | unordered oscillation | neutral structural context hypothesis | primary qualification parked; narrow disorder mechanism research-only |
| `1` | stabilizing after decline | nested condition under downside context | supported research condition; not standalone qualified |
| `2` | accumulation/consolidation | structural/order condition | strong exploratory proposition; not official |
| `3` | breakout probe | event condition | frozen development candidate; external qualification inconclusive |
| `4` | breakout acceptance | temporal condition after Stage3 episode | frozen development candidate; external qualification inconclusive |
| `5` | advancing trend | context hypothesis | INCONCLUSIVE_PARKED; continuous Facts only |
| `6` | pullback test | nested/process condition hypothesis | INCONCLUSIVE_PARKED; continuous Facts only |
| `7` | structural rebuild | repair condition hypothesis | INCONCLUSIVE_PARKED; continuous Facts only |
| `8` | accelerating top | upside acceleration/extension modifier | research-only; resource qualification not opened |
| `9` | high-level divergence/distribution | high-side price-volume divergence modifier | parked pending minimal activity relation |
| `10` | structural failure | persistent structural-failure context | accepted development Projection Contract; not official LabelCard |
| `11` | post-break rebound | nested condition under Stage10 | accepted development Projection Contract; not official LabelCard |
| `12` | declining trend | persistent downside context | research-only context; reference route no-pass |

No row in this table is an official peer-primary Stage qualification. The role and evidence grade are
separate concepts: architecture may retain a useful semantic proposition without granting consumer
authority.

## 4. Continuous Facts remain authoritative

The stable substrate is the accepted Facts layer, including Support Evolution, Trend Evolution and
Structure Order. Stage propositions interpret those Facts under versioned semantic contracts.

When a discrete code is parked or inconclusive, its continuous evidence remains available to research
and experts under the corresponding Fact authority. A parked label must not cause the underlying
measurements to be deleted, replaced with a weaker proxy, or silently converted to a negative label.

## 5. Epistemic and control separation

`ABSTAIN` means usable inputs do not support a sufficiently definite assertion. It is not neutral
market state `0`.

`X` means ordinary stage inference is invalid/frozen because of data or market-condition controls. It
is not structural damage and is not an ordinary lifecycle transition.

Any future representation must keep these meanings separate from market propositions.

## 6. Relationship and coexistence rules

- Context and nested-condition propositions may coexist.
- Modifiers may coexist with contexts and conditions.
- Historical numbering does not impose exclusivity or precedence.
- A nested condition does not independently qualify when its parent context is unqualified unless a
  separate contract explicitly establishes a standalone meaning.
- Later recovery/reclaim does not rewrite an earlier valid context observation.
- Same-layer conflict or insufficient evidence remains ambiguity/ABSTAIN, not a forced winner.
- No code becomes active merely because the schema can represent it.

## 7. Evidence grades

The next representation should carry a bounded evidence grade rather than hiding maturity differences.
At minimum it must distinguish:

- accepted development projection;
- frozen development candidate;
- strong exploratory proposition;
- research-only / reference-no-pass;
- parked / continuous-Facts-only;
- blocked on a missing Fact relation.

These grades are governance metadata, not confidence probabilities and not trading scores.

## 8. Successor contract

`P3-STAGE-028` should formalize a **Layered Stage Proposition Bundle** schema. A proposed bundle contains:

```text
knowledge_cutoff
control_state
context_assertions[]
condition_assertions[]
modifier_assertions[]
epistemic_status
fact_refs[]
primary_stage = null / absent
```

Each assertion should include a stable proposition id, optional historical stage code, truth/ambiguity,
evidence grade, reason/provenance and version. Research/parked propositions may be represented in audit
or shadow surfaces, but cannot become official consumer inputs without their own authority.

P028 is schema/governance work first. It does not itself reopen any label experiment or market read.

## 9. Claim ceiling

This design supersedes only the **flat single-valued implementation architecture**. It does not erase
historical vocabulary, invalidate prior experiments, qualify a new Stage, choose a primary stage,
authorize LabelCards, open preserved partitions, start daily inference, or grant expert/strategy/trading
consumption.

## 10. Demand-led continuation

The Owner's 2026-10-27 continuation uses four bounded batches, linked through
[the current module ledger](../work-items/learning-blueprint-progress.md):

1. Reuse the retained Head2 seven-feature research asset, the nine-feature rejection, accepted
   development 10/11 projections, layered representation and comparison interfaces. Rechecking
   compatibility does not count as new scientific acceptance or justify refitting an old candidate.
2. Assess input, exposure and independent-reference feasibility for the frozen 3/4 candidate.
   Preserve both anchor scales and the pre-T history requirement. Source windows and reference
   meanings are purpose-specific: a Head2 current20 reference cannot become a 3/4 reference, and
   the consumed 2022–2024 arbitration cannot become fresh. An input proposal does not activate it.
3. Only after the candidate's quality and use-specific gates pass, test one concrete consumer.
   Existing 10/11 development views remain reusable at their accepted grade; representation
   compatibility is not decision usefulness. P3 comparison and targeted P6 simulation are needed
   only when a proposed use actually changes trading decisions and their own gates permit it.
4. Keep other states demand-led. A parked discrete label does not remove its continuous Facts.
   Record the missing hypothesis, reference or relation and a concrete unlock trigger before any
   further investment; do not open a parallel campaign to qualify all historical codes.

P5 owns sample design, reference meaning and consumer disposition. A bounded P10 request owns new
shared-source qualification. Neither issue closure nor a tracking checkpoint transfers that authority.
The first execution findings are [recorded separately](../acceptance/state-portfolio-batches-20261027.md);
the generated ledger remains the single current gap view.
