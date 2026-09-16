# P3-EXPERT-006: Stage10/11 Semantic Consumption Discovery

| Field | Value |
| --- | --- |
| Work item | `P3-EXPERT-006` |
| Design status | approved |
| Delivery gate | accepted / closed after one frozen research-only run |
| Parent | `../expert-strategy-layer.md` |
| Direction review | `../../../../reviews/p3-expert-stage-consumption-direction-review-20260917.md` |
| Governing preregistration | `../../../../../config/expert-strategies/stage10-11-consumption-discovery.v1.json` |
| Machine result | `../../../../../config/expert-strategies/stage10-11-consumption-discovery-result.v1.json` |
| Acceptance | `../../../../acceptance/p3-expert-stage-consumption-20260917.md` |
| Result review | `../../../../reviews/p3-expert-stage-consumption-result-review-20260917.md` |
| Result | `SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN` |
| Stage authority | existing accepted development Stage10/11 projection only |
| Data authority | exact P029 retrospective population; no new market/DWD/protected reads |

## Outcome and boundary

This post-MVP increment implements the owner-approved expert-meeting decision to consume the existing
Stage10/11 semantic surface before expanding the historical Stage vocabulary. It does not reopen
`stock-stage-research-phase-1`, does not create P034, and does not change the accepted Expert Strategy
Layer MVP topology.

The product question is whether four expert-consumer roles can use the existing Stage10/11 surface as
bounded advisory context and whether repeated consumption exposes one concrete missing semantic family.
Continuous Facts remain the factual and computational authority. The Stage surface is additive,
retrospective on the frozen P029 population, development-only, and has no score, veto, constraint,
action, strategy, execution, or trading authority.

## Inputs and owned paths

Owned paths:

- `config/expert-strategies/stage10-11-consumption-discovery.v1.json` — frozen population, role,
  response, gap-trigger, budget, stop and claim contracts;
- `src/quanthus/expert_strategy/stage_semantic_consumption.py` — deterministic four-role packet
  artifact preparation and deep verification;
- `tests/unit/test_stage_expert_consumption.py` — semantic and Worker dispatch contracts;
- this design and `docs/work-items/expert-stage-semantic-consumption.md`;
- final result/review/acceptance files created only after all four role outputs are sealed.

Read-only bindings include the phase-1 closeout, P029 result, P033 result, direction review and the
approved Expert Strategy Layer design. Their exact SHA-256 values are frozen by the preregistration.

## Implementation route

1. Reconstruct the exact P029 ten-observation replay from the same immutable accepted F9 artifacts.
   The reconstruction must report zero new market reads and zero DWD provider opens.
2. Preserve each observation's authoritative Continuous Facts and the complete P028/P029
   `development_shadow` Stage10/11 surface, including proposition kind, evidence grade, Fact refs and
   provenance. Do not reduce the surface to a top-1 state.
3. Prepare exactly four immutable role packets: short-term/event-driven, fund/midterm-risk,
   quant-research and data-governance. Every packet contains the same ten observations.
4. Publish the packet artifact through the existing Event Worker fixed-config research path. This is
   artifact preparation only; the Worker does not call the evaluator.
5. Run four isolated evaluator sessions with one exact provider/model identity. Each role returns one
   structured answer per observation. Other-role outputs and the semantic key remain hidden until all
   four outputs are sealed.
6. Score only semantic understanding, bounded advisory sufficiency, authority-boundary safety and the
   preregistered missing-semantic-family trigger. Do not score returns or future outcomes.
7. Run a four-role result review. Even a triggered gap is only a decision input for a separately
   preregistered future scope; it never opens Stage research automatically.

The fixed gap trigger requires the same eligible non-`none` family to be selected by at least two
independent roles across at least three distinct observation identities. `other` requires explicit
manual review and cannot auto-trigger a successor.

## Failure semantics

Fail closed before evaluator exposure if the exact P029 population, any bound authority digest,
Stage10/11 semantics, packet count, role set or zero-read boundary differs.

After any evaluator output is visible, do not change the model/provider, Stage semantics, response
fields, metrics, gap threshold or claim ceiling. There are zero result-bearing retries per role.
Transport interruption may be recovered only when the same evaluator thread proves an exact
append-only breakpoint and no completed observation is replayed; otherwise the experiment is
invalid and closes without replacement.

Stop and reconvene before continuing if implementation would require new Stage codes, historical PIT
source archaeology, protected partitions, action-changing expert mappings, aggregator/position/P2
changes, or prediction/economic/production/trading claims.

## Verification and acceptance

Before the run:

- fixed request accepts only exact repository bindings and zero-read P029 population;
- packet generation is deterministic under input reordering;
- all four packets preserve full Stage proposition provenance/evidence grade and `primary_stage=null`;
- Event Worker admits only the exact governed run config;
- focused tests and Ruff pass;
- full `make check` passes on the frozen execution revision.

Run acceptance requires exactly four role packets, ten observations per role, 40 role-observation
evaluations, one provider/model identity, no result-bearing retry, zero new market/DWD/future/protected
reads, and a result disposition computed from the frozen rule. No acceptance outcome grants action or
production authority.

## Result

The frozen run produced 40 role-observation evaluations with 0 semantic-understanding errors, 40/40
bounded-sufficiency yes, 0 boundary-unsafe judgments and no eligible missing-semantic-family
selection. The preregistered decision is therefore
`SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN`.

Four independent post-result role reviews accepted the result and recommended keeping the stock-stage
phase-1 closed. A separate four-role transport-equivalence audit accepted the evaluator delivery as
information-equivalent with an explicit audit note; it did not claim byte-identical artifact
transport. No production, PIT, prediction, economic, strategy, execution or trading authority was
created.

## Rollback

This increment is an additive research sidecar. Rollback removes the P3-EXPERT-006 run-config
admission and packet-preparation module/Worker dispatch. Accepted P3 expert protocols, strategy cards,
expert outputs, aggregator semantics, Stage10/11 projections, Facts, P2 and all prior immutable Run
artifacts remain byte-identical and authoritative.
