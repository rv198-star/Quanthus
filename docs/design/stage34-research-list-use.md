# State 3/4: bounded breakout review-list use

| Field | Value |
| --- | --- |
| Design status | approved for bounded purpose and feasibility work by Owner 2026-10-28 continuation |
| Delivery gate | blocked; no research consumer, production or P6 admission |
| Parent | [Layered state portfolio](stock-stage-vocabulary-portfolio.md) |
| Work items | META-LEARN-001 / #540; P5-STATE-002 / #542; P5-STATE-003 / #543; P3-EXPERT-007 / #545 |
| Versioned purpose | `config/research/learning-mvp/stage34-breakout-list-use.v1.json` |
| Code | `stage_learning/prefix_qualification.py`, `stage_learning/qualification.py` |
| Contracts | `tests/contract/test_stage34_breakout_list_use.py`, `tests/unit/test_stage_qualification_capacity.py`, `tests/unit/test_stage_prefix_qualification.py` |
| Evidence | [2026-10-28 bounded feasibility result](../acceptance/stage34-purpose-feasibility-20261028.md) |

This is one research-use design, not a replacement for the frozen Stage candidate, old P5 request,
old semantic gate or consumed result. Prior development findings inform this prospective design;
they are not retroactively preregistered evidence. No new sample is activated by writing the purpose.

## Decision and actual consumer action

Test one concrete action: choose at most five T-close breakout episode alerts per day for a research
analyst to inspect. The intended benefit is fewer incorrect alerts at the same review cost while
retaining useful probe/acceptance cases. A label that changes no selected alert has established no
incremental value in this use. This is neither a trading rule nor a claim of measured human time saved.

Compare only the unchanged R1 and unchanged R0. Both receive identical admitted T-or-earlier daily
prefixes, 20/60-scale facts, activity information, context identities and missing-data denominator.
There is no facts-deprived or deliberately weakened control. Both may nominate at most five cases
per day, at one review unit per case. Priority is Stage4 then Stage3, followed by a fixed hash order
within each assertion. UNKNOWN stays in the denominator without becoming a negative or forced sale.

Report selected definite positives, definite-negative alerts, missed positives, ambiguous unique
assertions and UNKNOWN for both arms. Improvement requires fewer false alerts without losing
positive cases, or more positive cases without extra false alerts, at the same cost. A tie does not
establish benefit. Report issuer/related-episode dependence; cross-scale cases are not independent
votes. No future price, return, settlement or strategy result is an outcome here.

## Distinct recognition, purpose and execution boundaries

`score_if_sufficient` reports recognition `quality_passed`. A positive result can set
`consumer_request_eligible`, meaning only that it may accompany a separate purpose request.
The quality function receives no purpose admission and always returns
`consumer_test_authorized=false` and `production_or_P6_admitted=false`.

Actual research consumption needs a separately admitted purpose bound to complete source, sample,
reference, comparison, code/configuration and budget identities. Neither this design nor numerical
capacity creates that receipt. The original 100 definite Stage3 / 100 definite Stage4 / 50 ambiguous
minimums and all other recognition gates remain unchanged. Their sum, 250, is only a necessary
unique-episode lower bound, not semantic sufficiency or a complete sample target.

The deterministic synthetic panel remains useful for rule-shadow development and reproduction.
Its persona count or a new process is not independent semantic evidence. A real use comparison
requires separately attributable, candidate-blind judgements whose only truth is not the threshold
rules being compared. No such new reference or admission is supplied by this versioned design.

## Capacity before expensive work

`qualification_capacity_preflight` diagnoses allocation before reference generation or predictions.
It reports each stratum's unique episodes, prior-exposure exclusions, occupation by earlier strata,
remaining capacity and selected count. It never publishes a new sample or modifies the legacy
selector. The proposed rare-first order is mature-held, reclaim, started, early-held, loss; existing
caps remain unchanged and no cross-stratum backfill is permitted. It is a prospective allocation
diagnostic, not permission to resample the old result after seeing labels.

Exclude each previously reviewed episode across all dates; issuer/related-episode dependence still
requires separate treatment. For a new purpose, scale20 needs at least20 and scale60 at least60
**prior** contiguous admitted observations, excluding T. Count restarts on listing/coordinate change,
a gap or an unavailable attempt. Missing counts fail closed. A reviewer packet can include at most60
prior observations plus T; a shorter packet must report its actual history rather than claiming61
observations. These checks do not invent pre-IPO history or drop the60 scale.

Project a previously admitted prefix once per diagnostic Job and share it across capacity scenarios.
Every reuse binds source, code, configuration and prior-exposure identities. Do not repeat native
Base extraction or generate another panel when capacity already fails.

## Budget and branch disposition

Steps1/2 share the original150-minute window beginning2026-10-27T17:13:13Z, ending19:43:13Z;
interruption does not reset elapsed time. Only if source, capacity, independent-reference, recognition
and exact purpose-admission gates all pass may one comparison use up to120additional minutes.
The new-acquisition budget stays zero, new writes are at most256MiB and the free-space floor8GiB.

When prerequisites fail, retain a precise NO_GO, the reusable engineering, purpose design and
qualified continuous Facts. Stop only this new label/use experiment. Do not lower old thresholds,
reactivate old arbitration, hunt protected archives, mechanically supplement the old128 or make
this optional state head a global product gate. Shared-source work remains with P10; any later
source qualification must specify exact scope and budget before market-row access.
