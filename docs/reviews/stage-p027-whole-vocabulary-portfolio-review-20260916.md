# P3-STAGE-027 — Whole Vocabulary Portfolio Four-Party Review

Design status: accepted review  
Delivery gate: layered portfolio synthesis selected; no new result-bearing work

Machine synthesis: `../../config/stage-learning/stage-whole-vocabulary-portfolio-synthesis.v1.json`

## 1. Question

After the first-, second- and third-group experiments, should the historical `1..12` directional
vocabulary plus neutral `0` still be implemented as one flat, single-valued stage machine?

The review uses only already frozen repository evidence. No new market, reviewer, holdout, candidate,
strategy or trading surface is read.

## 2. Evidence pattern

The evidence no longer supports dimensional equivalence among the historical codes:

- `3/4` remain strong **development event-phase candidates**, but their one external qualification run
  ended at sample feasibility before semantic scoring;
- `5/6/7` are explicitly **INCONCLUSIVE_PARKED**, with continuous Facts retained instead of discrete
  candidates;
- `10/11` have an accepted, reproducible **context + nested-condition Projection Contract**, where
  `{10,11}` coexistence is legal and `primary_stage=null`;
- `0` primary-stage qualification is parked while only a narrower disorder mechanism remains
  research-only;
- `2` is a strong exploratory orderly-consolidation proposition, not an official label;
- `12/1` form a **downside context + nested stabilization condition**; Stage1 conditional gates pass,
  but Stage12 misses the frozen ambiguity ceiling and the route closes no-pass;
- `8/9` are **modifiers**, not peer primary stages; Stage8 remains research-only after resource no-pass
  and Stage9 remains blocked on a missing activity relation.

The original numbering therefore survives as vocabulary traceability, not as proof that all codes live
on one mutually exclusive axis.

## 3. Short-term trading review

The most useful short-horizon semantics are event and process assertions: breakout probe/acceptance,
stabilization, consolidation, pullback/rebuild and rebound. Their value is often precisely that they
occur *inside* a broader context. Forcing them into one persistent stage slot discards information and
creates artificial transitions.

Decision: represent event/nested conditions independently from contexts. Keep `3/4` as development
candidates, `1` as nested downside stabilization, `11` as nested rebound, `2` as exploratory structure,
and keep parked `6/7` unpromoted.

## 4. Fund / medium-horizon review

Persistent contexts are more useful for medium-horizon risk than short event names. Stage10 already
behaves this way as structural-failure context, and Stage12 was explicitly modeled as downside context.
Stage5's historical advancing-trend name is conceptually a context, but its discrete candidate failed
to qualify and should not be restored by architecture alone.

Decision: context representation is a separate layer. Evidence grade must travel with the assertion;
architecture cannot promote parked Stage5 or research-only Stage12.

## 5. Quantitative review

The strongest formal counterexample to the flat classifier is P3-STAGE-002: Stage10 and Stage11 may be
true together by construction. P020 repeats the same nested pattern for 12/1, while P3-STAGE-003/P026
move 8/9 into a modifier dimension. These are not edge cases; they invalidate mutual exclusivity as a
global assumption.

A flat `primary_stage` field would require a new arbitration/compression objective and new reference
truth that the current project has not qualified. Adding such compression now would turn an
architectural synthesis into another learned label problem.

Decision: no primary-stage compression in the next contract. Preserve same-layer ambiguity and legal
cross-layer coexistence.

## 6. Data / methodology review

The accepted Support Evolution, Trend Evolution and Structure Order Facts are the stable substrate.
Stage codes remain interpretations/projections over those facts. Parked codes must continue to expose
continuous evidence rather than disappear or be backfilled by weaker proxies.

`X` and `ABSTAIN` also cannot be mixed into the market-stage vocabulary: `X` is control flow for invalid
inference conditions, while `ABSTAIN` is epistemic uncertainty.

Decision: the next representation must separate Facts, proposition assertions, epistemic status and
control sentinel. Historical code is traceability metadata, not the storage schema's dimensional key.

## 7. Joint decision

The four-party review accepts a **layered vocabulary portfolio** and rejects the flat single-valued FSM
as the implementation architecture.

Current portfolio roles:

- context / structural-state hypotheses: `0`, `5`, `10`, `12` with different evidence grades;
- event / nested / structural conditions: `1`, `2`, `3`, `4`, `6`, `7`, `11`;
- modifiers: `8`, `9`;
- control / epistemic outputs outside the market-stage set: `X`, `ABSTAIN`.

This grouping is architectural, not a qualification upgrade. In particular:

- no historical code is currently authorized as an official peer primary stage;
- Stage10/11 are the strongest accepted development projection pair;
- Stage3/4 remain frozen development candidates with external qualification inconclusive;
- Stage2 remains strong exploratory;
- Stage1 is supported only as a nested research condition under unqualified Stage12 context;
- Stage0, Stage5/6/7, Stage8/9 and Stage12 retain their existing parked/research limitations.

The historical transition graph remains useful as explanation and hypothesis provenance but is no
longer the executable architecture.

## 8. Successor

Open `P3-STAGE-028 — Layered Stage Proposition Bundle Contract` as a schema/governance-only successor.
Its v1 representation should separate:

- context assertions;
- event/nested-condition assertions;
- modifier assertions;
- evidence grade and reason/provenance;
- ambiguity / abstention;
- control sentinel;
- continuous Fact references.

`primary_stage` must remain absent or null in v1. The contract may represent research/parked
propositions for audit, but representation alone must not activate them for official consumers.

## 9. Claim ceiling

P027 creates no new label qualification, market read, holdout evaluation, candidate search, primary
compression, LabelCard, daily inference, expert/strategy consumption or trading authority. It only
integrates frozen evidence into the architecture that future work must respect.
