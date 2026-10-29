# Reusable learning assets: delivery boundary v1

| Field | Value |
| --- | --- |
| Design status | approved for the Owner's bounded 2026-10-29 delivery |
| Delivery gate | engineering checks and main-target integration; no consumer activation |
| Parent | [Learning MVP](../work-items/learning-system-mvp.md) / #540, #546 |
| Current disposition | [Delivery and decision diagnosis](../work-items/learning-delivery-20261029.md) |

The reusable product is ordinary, versioned Python code plus contracts. Models,
Facts, market rows and run outputs stay in their governed external storage. A
reader, successful test or immutable archive does not grant scientific, PIT,
P6 or production acceptance.

## Smallest supported boundary

| Capability | Production modules | Intended reuse and limits |
| --- | --- | --- |
| Continuous structure Facts | existing `structure_v2`, `structure_support_facts`, `stage_projection` | One immutable job-local materialization shared by in-job consumers. Preserve the complete input/config/code/window/universe identity. Do not make TMP into ADM. |
| Deterministic feature encoders and bounded model primitives | `stage_learning.head2_facts`, `head2_revision`, `local_head` | Exact four/seven/nine-feature encodings, frozen optimizer policies, checkpoint load/dump, fit/update primitives. Any real new fit/update still requires its own qualified request. |
| Model-to-consumer boundary | `stage_learning.head2_consumer`, `qualification`, `prefix_qualification` | Missing input stays UNAVAILABLE; present but unqualified model stays ABSTAIN. Readability and completed engineering never grant consumer use. |
| Review/factor comparison | `stage_learning.factor_slice`, `factor_review_utility`, `head_comparison`, `review_list` | Reproducible research comparisons and cost accounting. Existing rejected mappings remain rejected; no automatic review queue or ranking policy. |
| Held-position interface | `expert_strategy.held_structure_inputs`, `held_structure_preflight`, `held_structure_integrity`, `held_structure_shadow` | Exact source/code/episode binding and existing expert/veto precedence. The tested old 10/11 mapping is parked; these interfaces are reusable. |
| Capital arithmetic | `stage_learning.entry_capital_analysis` plus existing backtest engine | Summarise already-qualified complete capital paths. This is not a new generic historical execution platform. |
| Consumed-path diagnosis | `expert_strategy.held_decision_census` | Validate the complete existing denominator and count recorded decisions. No new model, score, market read or economic replay. Unobserved alternatives stay NOT_OBSERVED. |
| Historical decision lookup | `expert_strategy.decision_review` | [Exact-archive read-only projection](historical-decision-review-v1.md), event-key queries and offline JSON/HTML. Actual retrospective time and parked conclusions remain unchanged. |
| Original B decision-chain lookup | `expert_strategy.decision_chain` | Exact original entry/signal/order/fill/held/exit reference queries, run-level ledger context and offline HTML; [same governed companion boundary](historical-decision-review-v1.md#original-b-decision-chain-companion), no new replay or invented allocation reason. |

Use `scripts/learning_held_decision_census.py --help` for the narrow offline census
entry. It requires an exact external selection SHA256 and a fresh output file.
The data-bearing selection and execution budget are not checked into Git.

The [existing-consumer calling guide](../operations/learning-assets-consumer.md) provides a
validated synthetic invocation of the supported expert/aggregator APIs, reason-bearing JSON,
and the required input contract. It introduces no additional runner or model-use admission.

## Archive-only experiments

The immutable archive is Git ref
`codex/learning-research-archive-20261029T074601Z`, exact revision
`bd474bff3b9e2d8292868ceab2ea9927f66e3d15`.

The earlier feature-contract source is preserved separately at
`codex/learning-feature-contract-archive-20261029T074601Z`, exact revision
`7f4ecc9ad9d0569b68b27cfac5a8081aae4e5a63`. It contains the original
`head2-revision.v2.json`, feature regressions and historical-runner closure test;
that history is not an ancestor of the March archive. The main-target selection
retains its pure feature tests and configuration, with the archived-runner test
kept on that exact earlier revision.

`scripts/entry_march_{fit,confirmation,capital,cold}.py`,
`expert_strategy.entry_march_capital` and
`stage_learning.entry_confirmation` are archived experimental drivers, excluded
from this main-target delivery. They are not a reusable training/execution API.
The reported P2 in the March fit driver remains **open on that archived driver**:
derived feature/label closure was not checked before fitting. Actual frozen-run
inputs matched their sources in the subsequent audit, so the negative experiment
is retained; exclusion is not a claim that the defect was repaired.

The older B trace launcher `expert545_capture_b_trace.py` intentionally consumes
its explicitly versioned old producer, not the current package. Its provenance
guards and synthetic regressions are preserved for evidence reproduction. Do not
run it with arbitrary current code or describe it as an independent main API.

Frozen historical requests/configurations and reports remain archival records.
They do not authorize their old data paths, elapsed deadlines or candidate runs.

## Retained identities and acceptance

Six retained checkpoints (the original four-feature fit/update and independently
reproduced pair, plus seven- and nine-feature models) can be read with the same
11-module reader closure. The main-target smoke uses a new process with
`-I -S -B`, explicit code/model hashes and no previous reader/cache/network or
market input. It verifies bytes, parent/scaler preservation, UNAVAILABLE/ABSTAIN
behavior, and corruption/hash/binding failures. It does not fit or independently
retrain anything. No model array or private source row belongs in this repository.

Formal independent construction remains separate from this compatibility smoke.
The original independent runs are retained as evidence, not counted as newly run
progress. Old four-feature local-update evidence cannot establish a seven-feature
same-chain update or product-level adoption.
