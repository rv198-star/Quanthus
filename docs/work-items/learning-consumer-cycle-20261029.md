# Existing-consumer delivery and decision selection — 2026-10-29

| Field | Value |
| --- | --- |
| Design status | approved — Owner activated the proposed steps 1–3 on 2026-10-29 |
| Delivery gate | [bounded engineering acceptance](../acceptance/learning-consumer-cycle-20261029.md); no new learning candidate |
| Parent | [Three-batch plan](exploration-next-20261029.md), [learning MVP](learning-system-mvp.md) |
| Work items | #540 coordination; #545 existing consumer; #546 asset delivery |
| Governing design | [Exploration requirements](../design/exploration-governance.md), [asset boundary](../design/learning-assets-v1.md) |
| Runtime | `cn02:/srv/quanthus-base22-data/jobs/learning-consumer-cycle-20261029T120743Z` |

## Current recovery checkpoint

The existing expert consumer has a runnable [calling guide](../operations/learning-assets-consumer.md).
The guide uses current ordinary code, the frozen strategy registry and synthetic inputs.
It preserves the pending model's abstention, missing-data entry block, position safety and
complete output identities. No new runner or production adapter was needed.

Existing diagnosis does **not** justify another comparison. Step 3 terminates at its explicit
condition check: `NOT_RUN_CONDITION_NOT_MET`. This is successful execution of the selection
rule, not a completed scientific comparison. Head2, the current 3/4 route, volume and held
threshold retries remain parked. No new training, market-data read, collection, threshold
change, economic replay or P6 activation follows.

## Steps 1–3 and the current gap table

| Step / need | Evidence and actual delivery | Disposition / remaining condition |
| --- | --- | --- |
| 1. A usable existing consumer | `FormalStrategyRunner.evaluate_entry` / `evaluate_held` and the existing aggregator already implement the contract. A scoped documentation search found no runnable combined calling example. The new guide is executed verbatim in an isolated Python process; all four safety/use cases pass. | Deliver the guide and existing APIs. No demonstrated runtime interface gap, so no new production code. A real new caller must bind its own admitted inputs. |
| 2a. Further held-position protection | Reuse the complete 8,393-review / 538-episode census: all 535 recorded hard exits have exits; 16 structural proposals, 15 safety overlaps and one changed issuer/episode/date. Unknown structure remains unknown. | No repeatable uncovered action is established on this denominator. Keep the mapping parked; do not infer that every held decision is optimal. |
| 2b. Entry or allocation improvement | The old 790-case entry diagnostic establishes dispersed decision differences, not value. The later fixed March use gate failed. Entry/allocation/fill alternatives are not observed in the held trace. | No new independently justified question is selected. Do not recycle consumed samples or convert unobserved alternatives into a confirmed business defect. |
| 3. Conditional minimum comparison | No question passed step 2. | Comparison not run; its budget remains unspent. A specific observed need, permissible alternative and qualified input/reference scope are the reopening conditions. |

The interface example is engineering validation, not step 3's market/decision-value comparison.
The earlier six-model reader smoke and census were reused as evidence rather than rerun or
claimed as new progress. Three candidate needs were reviewed; this is not a claim to have
searched every possible project use.

## Evidence identities and cost

The frozen selection and current decision are stored in this job's
`evidence/selection-inputs.frozen.json` and `evidence/decision-selection.json`.

- Retained census: `37fc093b4f1a65eb083975d398973e77d611e10c5f65e2389962db54f61d0b72`.
- Retained disposition: `6a104d8b75a331c0f3d57d13db9fbb733e46b256bf73351bc4898cb895e114a8`.
- Repository evidence is pinned to `caa54653ab8a02d0197e887db094533f6c672eff`:
  [entry diagnostic](../acceptance/learning-entry-diagnostic-20261029.md),
  [later March review](../acceptance/learning-march-astra-review-20261029.md) and
  [complete held diagnosis](learning-delivery-20261029.md).
- The example receipt binds the document, exact code block, stdout hash and actual elapsed
  time. Output contains synthetic values and ordinary policy/evidence identities only.
- The entire new job has a 256 MiB write ceiling, zero new source requests and the existing
  8 GiB free-space floor. The untriggered comparison consumes no market-comparison budget.
  Recorded wall time is not a complete model-token or historic campaign cost measurement;
  historical unknown costs remain unknown.

## Next decision

Use the supported guide for existing research calls. Park new learning experiments until an
actual caller supplies one concrete missed decision or required interface with qualified
evidence. Normal project work may proceed under its own contracts. There is no automatically
selected next experiment, permanent polling loop or request for new data in this handoff.

This batch completes usable engineering delivery and bounded task selection. Overall learned
quality, product value and admission remain at their prior grades; the 12+X blueprint does not
gain scientific completion from documentation or synthetic checks.
