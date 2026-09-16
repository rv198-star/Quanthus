# P3-EXPERT-006 — Stage10/11 expert consumption discovery

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted / closed |
| Parent | Expert Strategy Layer post-MVP increment |
| Governing design | `../design/lld/04-experts-aggregator/expert-strategy-layer/stage-semantic-consumption.md` |
| Preregistration | `../../config/expert-strategies/stage10-11-consumption-discovery.v1.json` |
| Direction review | `../reviews/p3-expert-stage-consumption-direction-review-20260917.md` |
| Result review | `../reviews/p3-expert-stage-consumption-result-review-20260917.md` |
| Acceptance | `../acceptance/p3-expert-stage-consumption-20260917.md` |
| Machine result | `../../config/expert-strategies/stage10-11-consumption-discovery-result.v1.json` |
| Result | `SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN` |
| Authority | development-only semantic consumption discovery; no action authority |

## Goal

Consume the existing accepted Stage10/11 development semantic surface through four Quanthus expert
roles before considering any new Stage-code research. The run must determine whether the current
surface is sufficient for bounded advisory interpretation and whether one concrete missing semantic
family repeatedly appears across roles and observations.

This is not a new Stage qualification, prediction experiment, economic test, Shadow activation,
strategy promotion or trading study.

## Frozen scope

- exact P029 ten-observation retrospective population only;
- four roles: short-term/event-driven, fund/midterm-risk, quant-research, data-governance;
- one immutable packet per role, same ten observations in every packet;
- authoritative Continuous Facts plus complete Stage10/11 `development_shadow` proposition surface;
- zero new market/DWD/future/protected reads;
- four isolated evaluator sessions using one exact model/provider identity;
- semantic understanding, advisory sufficiency, boundary safety and missing-semantic-family discovery
  only.

Stage10/11 thresholds, hierarchy, evidence grade and `primary_stage=null` are frozen. The packet may
not request buy/sell/position recommendations.

## Gap decision rule

A concrete downstream semantic-gap family is identified only when the same eligible non-`none`
family is selected by at least two independent roles across at least three distinct observation
identities. Eligible families are breakout lifecycle, downside persistence/stabilization, orderly
consolidation/structure, and high-side activity divergence. `other` requires explicit manual review.

If no gap reaches the frozen trigger and bounded sufficiency/safety gates pass, Stage research remains
closed. Any identified gap is only evidence for later separate preregistration; it does not open a
successor automatically.

## Stop conditions

Stop and reconvene if the run would require new Stage codes, semantic changes, source/time-authority
changes, historical PIT archaeology, protected data, action-changing expert mappings, aggregator/P2
changes, or prediction/economic/production/trading claims. After the first result is visible, the
model/provider, questions, metrics, thresholds and claim ceiling are immutable.

## Verification and closeout

The pre-result revision passed deterministic packet tests, fixed Event Worker dispatch tests, Ruff
and full `make check`. The exact final packet-preparation run produced four role packets over the ten
frozen observations with zero new market/DWD/future/protected reads.

Four isolated consumer sessions produced 0/40 semantic errors, 40/40 bounded-sufficiency yes, 0/40
boundary-unsafe judgments and zero missing-semantic-family selections. No preregistered gap trigger
fired. Four independent result reviews accepted `ACCEPT_SUFFICIENT_CLOSE_NO_STAGE_REOPEN` 4/4.

Because the evaluator host consumed an information-equivalent local projection of the governed
Worker-status visible payload rather than byte-mounting the production role-packet file, a separate
four-role methodology review was performed. It accepted
`ACCEPT_TRANSPORT_EQUIVALENT_WITH_AUDIT_NOTE` 4/4; the semantic key remained hidden and no rerun was
required.

P3-EXPERT-006 is therefore closed. Stage10/11 remain optional development advisory semantics over
authoritative Continuous Facts. `stock-stage-research-phase-1` remains closed and no Stage successor
is automatically opened.
