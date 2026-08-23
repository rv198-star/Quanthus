# P3-ATTR-001: Bounded Formal-Strategy Attribution

| Field | Value |
| --- | --- |
| Parent | `../10-monitoring-attribution.md` |
| Expert-layer parent | `../04-experts-aggregator/expert-strategy-layer.md` |
| Work item | `P3-ATTR-001` |
| Design status | approved |
| Delivery gate | accepted by `docs/acceptance/p3-attr-formal-production-20260823.md` |
| Implementation readiness | delivered through the PostgreSQL Worker and immutable formal release |

## 1. Outcome and boundary

Adapt the first formal expert strategy to the accepted attribution planner, Pipeline, verifier, and
publisher. Reproduce the native baseline, execute the two predeclared single-expert fallbacks and
their required joint fallback through the complete capital-constrained P2 path, and publish bounded
effects with interaction limitations.

This issue explains sensitivity to legal expert-policy interventions. It does not allocate causal
profit ownership, add effects together, search expert combinations, compute Shapley values, tune
weights, or claim that a simulated counterfactual was observed in the market.

## 2. Inputs and owned paths

Inputs are the sealed formal strategy card, native topology and attribution-adapter digests,
baseline Run, two legal single interventions, required joint intervention, exact DWD/split/execution
bindings, and outcome-blind questions signed before results.

The issue owns or extends:

- `src/quanthus/strategy_attribution_planning.py` only when a proven generic contract gap exists
- `src/quanthus/strategy_attribution_pipeline.py` only when the accepted execution port needs the
  formal adapter
- `tests/unit/test_formal_expert_attribution_adapter.py`
- `tests/acceptance/test_formal_expert_attribution_pipeline.py`
- immutable attribution plans, workspaces, receipts, and published releases under the existing
  artifact root

`src/quanthus/expert_strategy/attribution_adapter.py` is a sealed input owned by `P3-STRAT-002`,
not a shared implementation path. This Issue reads and verifies its digest. If execution exposes an
adapter defect, attribution stops and `P3-STRAT-002` produces a new strategy/adapter version before
this gate is resumed; result-bearing code cannot be patched inside the experiment.

Synthetic demo semantics remain unchanged. A formal release uses
`evidence_source_kind=formal_strategy_evidence` and can never be resealed from a synthetic
workspace.

## 3. Adapter contract

The strategy-owned adapter exposes the exact topology digest, components, capabilities, questions,
and legal interventions through the existing `StrategyAttributionAdapter` contract. It declares
both strategy experts as components and system guardians as fixed, non-intervenable context.

For each component it binds the expert, assessment, policy mapping, evidence route, decision
surface, dependencies, and one legal fallback. The joint spec aligns the two intervention ids and
digests by position and freezes `block_new_with_base_position_management` when both experts abstain.

The adapter cannot inspect experiment results while declaring questions or capabilities. Planning
must complete and persist before the Pipeline gains access to result-bearing Run inputs.

## 4. Predeclared question set

The formal strategy card supplies exactly three economic questions:

1. How does the complete strategy change when the trend expert policy effect uses its abstention
   fallback?
2. How does it change when the position-risk strategy effects use their declared fallback while
   system guardians and base position management remain active?
3. Does applying both fallbacks produce a material interaction beyond the two isolated effects?

Each question freezes the primary metric, required secondary risk/cost metrics, materiality rule,
uncertainty/stability requirements, and expected direction before development results are read.
Questions that the adapter cannot execute are marked unavailable with a typed reason; the planner
does not invent an alternate intervention.

## 5. Replay and comparison contract

The first Pipeline node reruns the unmodified formal strategy with the exact native bindings. Its
decision, fill, ledger, and performance digests must reproduce the sealed baseline before any
intervention node may run.

All intervention Runs reuse the same DWD snapshot, universe, candidate order, capital, matcher,
costs, settlement, execution assumptions, and seeds. Only the declared expert fallback changes.
Every Run executes the complete portfolio and capital path; per-security diagnostic money and
partial ledger edits are not official effects.

Effects report at least return, maximum drawdown, tail loss, exposure and capital use, turnover,
fees, missed opportunity, decision-change counts, and the card's primary metric. The joint result
is reported beside, not distributed among, the single effects. Overlapping decisions and common
market periods remain dependent observations.

## 6. Worker and publication flow

`quanthus.expert-evaluation.v1` with `operation=bounded_intervention` executes the persisted plan.
The existing checkpointed Pipeline owns restart; the PostgreSQL Job owns lease and cancellation;
the immutable attribution workspace owns analytical truth. A cancellation or lease loss cannot
publish a partial release.

The existing deep verifier checks baseline reproduction, adapter/topology bindings, plan closure,
receipts, metric definitions, parent artifacts, and all checksums. `READY` is written last and the
catalog sees only the atomically published release.

## 7. Implementation route

1. Verify `P3-STRAT-002` acceptance, explicitly resume the deferred gate, and seal the three
   outcome-blind question records before opening baseline results.
2. Load the strategy-owned adapter by digest, persist the accepted planner request, and reject any
   attempt to edit adapter or topology inputs in the experiment workspace.
3. Submit baseline reproduction through the Worker and block all children until every native
   decision, fill, ledger, and performance binding matches.
4. Submit the two single and one joint intervention nodes through the same complete P2 route and
   collect their immutable receipts.
5. Run deep verification, publish the formal release atomically, and record interaction,
   simulator, dependence, and resource limitations without allocating profit ownership.

## 8. Failure semantics

Baseline mismatch stops every child experiment. Adapter edit or topology drift, undeclared fallback,
missing joint spec, changed replay context, incomplete P2 ledger, missing receipt, metric registry
drift, question/result leakage, partial workspace, and checksum mismatch block publication.

A harmful, negligible, or inconclusive effect is a valid result when evidence is complete. It does
not trigger automatic remapping. An unavailable required question blocks this issue rather than
being omitted from the report.

## 9. Verification and acceptance

Tests prove outcome-blind planning, exact baseline reproduction, immutable context across paired
Runs, sealed-adapter reuse, intervention closure, fixed guardians, required joint execution,
non-additive presentation, lease/cancel resume, deep verification, atomic publication, and
deterministic semantic workspace bytes. Negative tests cover every drift and partial-publication
path.

Acceptance artifacts are the signed question set, adapter capability manifest, persisted plan,
four complete P2 Runs (baseline, two singles, one joint), verification receipts, published
workspace/release digests, interaction statement, and resource cost. This evidence may inform a
human lifecycle decision but cannot make it.

## 10. Rollback

Remove the formal adapter version from the admitted registry and stop new attribution Jobs. Keep
the baseline and every published workspace immutable. Candidate detail may continue showing the
last trusted release with its version and limitations, or show unavailable if the binding is no
longer admitted; it must not silently substitute synthetic evidence.
