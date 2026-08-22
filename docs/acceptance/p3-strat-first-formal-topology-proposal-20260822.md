# P3-STRAT-002 First Formal Strategy Proposal

| Field | Value |
| --- | --- |
| Status | outcome-blind proposal approved by the project owner; implementation acceptance remains pending |
| Work item | `P3-STRAT-002` / Issue #149 |
| Strategy card | `candidate.p4-trend-risk@1.0.0` |
| Proposed semantic digest | `sha256:b0a37af05f14bf9cd23e4bf3fe22b546d88646dbad5696f4700dcfd7204a0716` |
| Result access | blocked |
| Governing issue | `https://github.com/rv198-star/Quanthus/issues/149` |
| Approval | `https://github.com/rv198-star/Quanthus/issues/149#issuecomment-5377943091` |

This proposal was prepared without querying a new strategy Run, intervention result, observed
portfolio metric, or sealed partition. The accepted #148 assessment result is not used to select a
threshold, weight, action mapping, or budget. Passing card-validation tests does not approve the
proposal.

## Recommendation in plain language

Create one new product-candidate identity for the first formal expert strategy. Do not attach the
topology to any of the five current candidates: their candidate populations differ from the P4
event population used to evaluate the trend expert. Do not turn the frozen P3 capability fixture
into a lineage.

The new candidate consumes every retained `p4-event.price-volume-state-transition@1.0.0`
opportunity in the accepted 2020-2021 assessment population. It has exactly two active experts:

1. `expert.trend-stability@1.0.0` is the only positive entry scorer. Stable maps to `+1`, neutral
   to `0`, fragile to `-1`, and unavailable evidence is an explicit abstention. The minimum entry
   score is `0.50`, so only stable assessments can initiate entry.
2. `expert.position-risk-review@1.0.0` never supplies positive entry authority. It may cap or block
   entry, request one held-position reduction, request an extreme-extension exit, or abstain. It is
   a policy-only heuristic and not a system guardian.

System PIT, data quality, tradability, accounting, funding, hard position, concentration, and base
position-management rules remain outside both experts and cannot be removed by an attribution
intervention.

## Exact strategy and population

| Binding | Frozen value |
| --- | --- |
| Product candidate | `candidate.p4-trend-risk@1.0.0`, reserved but not published before trusted evidence |
| Candidate generator | `p4-event.price-volume-state-transition@1.0.0`, `sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50` |
| Assessment population | `population.p4-development-candidates@1.0.0`, `sha256:a66195a194933ca3b0e3850c7fb515a28b5125eff1b29241d13f953565c9961c` |
| Split | `p4-event-date-split@1.0.0`, development only |
| Applicability | all retained P4 events from 2020-01-01 through 2021-12-31 before executability or outcome evidence |
| Last entry / execution | 2021-12-30 / 2021-12-31 |
| DWD | `dwd-base-202607-v2.1` plus correction `dwd-base-202607-v2.1-correction-20260813-001` |
| Provider | `development-direct-dwd-job-local-v1@2.0.0` |

The exact population equality is a fail-closed card check. An assessment over a broad population
cannot be reused for a narrower or differently generated strategy candidate.

## Position-risk policy

The base portfolio has five fixed CNY 100,000 units, a 30% base Trial target, whole 100-share lots,
no adds, at most one strategy reduction, and five-session cooldown. The active position-risk
expert applies these additional constraints:

| Surface | Frozen rule |
| --- | --- |
| Entry allowed | 20-session realized volatility no greater than 50% and RSI14 no greater than 75 |
| Active entry cap | maximum 20% of one unit |
| Held reduction | after at least three sessions, marked return at or below -4%, volatility above 50%, or RSI14 above 75; reduce to at most 10%, once |
| Strategy exit | volatility above 70% and RSI14 above 85 at the same review |
| Missing entry evidence | expert abstains; system missing-data guardian blocks new entry |
| Missing held evidence | expert abstains and alerts; base position management remains active |

The unchanged base position management still handles structural break, -8% constraint stop, two
consecutive deterioration reviews, and 40-session maximum hold. The expert fallback cannot remove
those rules.

These thresholds are an outcome-blind, falsifiable first heuristic. They were not copied from the
frozen P3 fixture and were not selected from a result search. A later change creates a new strategy,
expert, and mapping version rather than repairing this card in place.

## Legal fallbacks and expected changes

Only three attribution interventions are legal:

| Intervention | Frozen fallback | Expected mechanical direction; not an economic claim |
| --- | --- | --- |
| Trend expert only | replace its policy effect with explicit abstention | fewer or zero new entries and lower exposure |
| Position-risk expert only | remove only its strategy effects; preserve base policy and guardians | weakly more or larger entries and fewer strategy reductions/exits |
| Both experts | `block_new_with_base_position_management` | zero new entries; existing positions remain managed |

Return, drawdown, and interaction direction are not guaranteed. Full attribution must report
capital-constrained net-return delta, maximum drawdown, tail loss, capital use, exposure, turnover,
fees, missed opportunities, and decision-change counts. The paired uncertainty method is a
deterministic 2,000-resample bootstrap over complete non-overlapping 22-session blocks. Joint
effects remain non-additive and are not assigned to either expert.

## Resource and result boundary

Approval authorizes implementation plus at most two single-Worker January 2020 sentinel Jobs: one
bounded sentinel and one deterministic reproduction. Each Job is capped at 30 wall minutes, 6 GiB
peak RSS, and the combined published sentinel budget is 1 GiB. Budget overflow stops the work and
requires a new human decision.

This approval does not authorize the full 2020-2021 baseline plus three intervention Runs. Those
result-bearing comparisons belong to `P3-ATTR-001`, require `P3-STRAT-002` acceptance, explicit
gate resumption, and their persisted no-result plan.

## Stop and rollback

Implementation or publication stops on identity drift, population mismatch, missing/extra experts,
guardian removal, undeclared intervention, all-scoring abstention without block-new, Shadow order
authority, PIT/split violation, deterministic mismatch, budget overflow, or any sealed-partition
access attempt.

Rollback removes this exact strategy version from the admitted registry and stops new Jobs. Cards,
topology and adapter manifests, Jobs, Runs, and decisions remain immutable. Repair creates a new
version.

## Rejected alternatives

| Alternative | Outcome-blind reason rejected |
| --- | --- |
| Attach experts to one of the five current candidates | Their applicability population does not match the accepted P4 assessment population. |
| Continue the frozen P3 governed candidate | It is a capability fixture and cannot become a promotable strategy lineage. |
| Add more experts | It exceeds the personal-platform MVP intervention and evidence budget. |
| Search thresholds, weights, or combinations | It would turn the development result into an optimizer and invalidate the preregistration. |
| Run full 2020-2021 attribution inside #149 | Full economic effects are owned by the separately gated `P3-ATTR-001`. |

## Human decision

The project owner approved `candidate.p4-trend-risk@1.0.0` and semantic digest
`sha256:b0a37af05f14bf9cd23e4bf3fe22b546d88646dbad5696f4700dcfd7204a0716`.
The approval authorizes only the implementation and bounded sentinel budget above. Any semantic change
requires a new digest and approval before implementation, Worker submission, or result access.
