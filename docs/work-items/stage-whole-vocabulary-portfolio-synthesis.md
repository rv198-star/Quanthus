# P3-STAGE-027 — Whole Vocabulary Portfolio Synthesis

Design status: approved  
Delivery gate: closed `PASS_LAYERED_VOCABULARY_PORTFOLIO_SYNTHESIS`

Parent: `stock-stage-historical-learning.md`  
Design: `../design/stock-stage-vocabulary-portfolio.md`  
Machine result: `../../config/stage-learning/stage-whole-vocabulary-portfolio-synthesis.v1.json`  
Review: `../reviews/stage-p027-whole-vocabulary-portfolio-review-20260916.md`

## Goal

Integrate all frozen stage-learning evidence into one architecture decision without creating new
result-bearing evidence. P027 answers what the historical vocabulary *is now* after qualification,
parking, dimensional reclassification and nested-proposition work.

## Inputs

P027 consumes only already-frozen evidence from:

- first-group Stage3/4 development candidate and consumed external-qualification closeout;
- second-group 5/6/7/10/11 mixed closeout;
- accepted Stage10/11 Projection Contract;
- third-group P019 portfolio rebaseline;
- P020 Stage12/1 reference closeout;
- P026 Stage8/9 resource closeout.

No new DWD, market, reviewer, holdout, candidate, future, strategy or trading data is read.

## Decisions

1. Historical directional codes `1..12` plus neutral `0` remain traceability vocabulary, not thirteen
   peer output slots.
2. The flat single-valued FSM is superseded as implementation architecture.
3. Future representation is layered into contexts/structural states, event/nested conditions and
   modifiers over continuous Facts.
4. `X` remains control flow; `ABSTAIN` remains epistemic status; neither is a market stage.
5. No historical code is currently an officially qualified peer primary stage.
6. Evidence maturity remains explicit and cannot be upgraded by architecture synthesis.
7. `primary_stage` compression remains a separate, unopened problem.

## Work sequence

| Step | Work | Exit |
| --- | --- | --- |
| Inventory | Bind frozen first/second/third-group evidence and account for 0 plus 1..12 | all historical market codes accounted for |
| Classify | Assign evidence-bound role/disposition without changing prior outcomes | context/condition/modifier/parked matrix frozen |
| Review | Four-party short-term/fund/quant/data architecture review | flat peer-stage assumption rejected; coexistence rules agreed |
| Freeze | Publish machine synthesis and layered portfolio design | P027 governance result immutable |
| Verify | Contract/traceability/full-repo checks | P027 closed; only P028 schema/governance successor ready |

## Successor

Open `P3-STAGE-028 — Layered Stage Proposition Bundle Contract`.

P028 may define the schema and consumer-governance boundary for a bundle of context, condition and
modifier assertions with evidence grades, ambiguity/abstention and Fact references. Its first version
must keep `primary_stage` absent/null and must not activate research/parked propositions merely by
representing them.

## Boundaries

P027 does not qualify/reject any code beyond the existing evidence, reopen parked routes, change
thresholds, read 2021/2022-2024/2025, generate reviewer opinions, score candidates, publish LabelCards,
run daily inference, feed strategy/expert production, or trade.
