# March-cutoff experiment: Astra review and portfolio disposition

| Field | Value |
| --- | --- |
| Design status | approved for review of existing evidence; no new experiment is activated |
| Delivery gate | accepted for the narrow negative research conclusion; reusable fit entry has one open P2 finding |
| Reviewed revision | `b1dbc376c6e286e11be6f08024c58488bdbf3499` |
| Work items | #540 coordination; #542–546 scoped research and asset delivery |
| Parent | [Original learning MVP](../work-items/learning-system-mvp.md) |
| Experiment | [March TRAIN20 / EVAL87 delivery](learning-entry-march-confirmation-20261029.md) |
| Review evidence | `cn02:/srv/agentdock/.codex/entry-march-astra-review-20261029T065252Z-b1dbc37` |
| Next route | [Bounded asset delivery and decision-gap diagnosis](../work-items/learning-after-march-review-20261029.md) — proposed, not a new execution permission |

## Verdict

The evidence supports rejecting **this Head2 entry-filter candidate** under its
unchanged value gate. Park further Head2 fits, feature additions, threshold changes
and alternative cutoffs unless a materially different, independently justified
consumer question emerges. This does not establish that every Head2 use, all
learned states or the 12+X architecture are ineffective.

Do not switch automatically to Head3/4 or rerun the rejected held-position mapping.
The next worthwhile tranche is durable delivery of the existing assets, followed
by one bounded diagnosis of an actual algorithmic decision gap. Successful execution
of a negative experiment, engineering integration, recognition quality, incremental
decision value and trading permission remain separate conclusions.

## Independent checks performed in this review

Before inspection, freeze 79 exact artifacts, 4,288,451 bytes, at the reviewed
revision. A separate standard-library verifier imports no production evaluator.
It checks the 20 training identities, March cutoff, company separation, train-only
means/scales and all 87 committed prediction records. For the 83 available inputs,
independently calculated logits/sigmoids match within 2.23e-16. The four unavailable
cases retain unavailable scores and block C1/C2 entries; C0 retains its original B
eligibility because these are additional Head2 input requirements, not a newly
discovered defect in C0's required inputs.

The verifier reconstructs both committed reference panels and their consensus,
recounts confusion matrices, reconciles all final capital ledgers and independently
calculates return, drawdown, fees and issuer contributions. The 21 primary/cold
capital files remain byte-identical with distinct physical inode identities.
An additional check compares **complete** derived TRAIN20 rows, including features
and labels, with their hash-qualified original source rows. They agree exactly;
cold Facts, training, packet, checkpoint and predictions also agree exactly.
All inspected original artifacts remain unchanged. No new fit, market replay,
collection or protected-period read occurs in this review.

| Scoped capital arm | Net return after fees | Maximum drawdown | Fills |
| --- | ---: | ---: | ---: |
| C0: original B on this population | +1.145054% | 2.967447% | 50 |
| C1: B plus unchanged R0 | −1.980956% | 2.067270% | 22 |
| C2: B plus March Head2 | +0.385698% | 3.150021% | 40 |

C2 loses 0.759356 percentage points to C0 and has greater drawdown. Its improvement
over C1 does not satisfy the requirement to beat **both** controls; removing its
largest issuer contribution reverses that improvement. Contribution removal is
accounting sensitivity, not a counterfactual portfolio with capital reallocation.
The disclosed descriptive bootstrap is unnecessary to establish the failed point
return/drawdown gate and must not be upgraded to a preregistered confirmatory test.

The 58 definite operational references reproduce: R0 has TP12/FN11/TN32/FP3;
Head2 has TP23/FN0/TN23/FP12. Better recall and balanced accuracy coexist with more
false positives. The references are two candidate-blind AI opinions, not independent
market truth. Four positive training examples, the training/evaluation listing-cohort
shift and the narrow 2021 capital population limit generalization. Negative value
here is sufficient to decline adoption, not a population-wide impossibility proof.

The first audit-only arithmetic script incorrectly assembled confusion-cell names
and stopped before producing a result. Its failed log is retained. Correcting that
review helper produces the matching counts above; no experiment output was edited.

## Findings

### P2 — derived training bytes are not admitted at the fit boundary

`scripts/entry_march_fit.py:fit` verifies the original input pins and training member
files, then reads `private/training20.json`. `training_support` compares identity
fields, dates, class support and mask support; the frozen identity records do not
include the features or labels. The fit command does not compare those complete
derived rows with the qualified original selection before calling `fit_head`.

A disposable, entirely synthetic reproducer keeps the original pinned files and
identity set unchanged, changes one derived feature or swaps two labels while
preserving class counts, and reaches the fitter in both cases. The fitter is replaced
with a terminating sentinel: **zero actual fits** occur. Only the wall-clock/resource
guard is bypassed for this synthetic setup; real integrity checks remain active.

This is a future-reuse/merge defect, not evidence of contamination in this run.
The complete real-row/source comparison and independent cold reconstruction pass.
Retain the negative conclusion. Before reusing or integrating this driver, bind
the exact derived training/evaluation/packet identities at qualification and verify
the consumed closure at fit, including actual fit code/config. Fail before any model
write on changed features, labels, selected identities or mismatched qualification.
Do not fix this by repeating the historical capital experiment or by inventing a
general orchestration framework. Regression and minimum consumer-boundary changes
are sufficient; the repair has **not** been executed by this review.

### P2 — current recovery status must not make positive P&L the MVP closure rule

Live #540/#543/#544/#545/#546 and the original immutable MVP text allow rejection,
regression and inconclusive results while retaining the prior version. Positive
economic improvement is an adoption gate for this candidate, not a prerequisite
for every engineering or research subtask to close. A status saying only “no
incremental value, so everything is incomplete” creates an endless experiment loop.

This also does **not** justify declaring the whole MVP accepted. The evidence needs
lineage-specific reconciliation: the old real four-feature v0→v1 update is an
engineering fixture with six train/nine development examples and no consumer
admission; this March seven-feature model is generation zero, with no local update.
One cannot splice those two histories into a qualified, updated seven-feature
consumer chain. This is not a new requirement to update seven features specifically:
assess the old update against the original #544 criteria and credit exactly what it
proves. The actual old update and new negative experiment both remain valid within
their scopes. Do not run an otherwise purposeless new update merely to tick the MVP
box. The appended blueprint checkpoint corrects the next action and separates this
remaining qualification/integration work from the rejected value hypothesis.

### Delivery gap — tested assets are not yet on the default branch

At the review snapshot, #570, #572, #573, #574, #575 and #576 are all open draft PRs.
Their dependency stack is known; this is not six independent finished mainline
features. GitHub main is `a332e79f3f548f6c0ea558092f745b584045de80`; its exact tree
does not contain `local_head.py`, `head2_consumer.py`, `head2_revision.py`, the March
capital adapter or the original MVP work-item file. Earlier clean-checkout reader
acceptance remains valid on its committed task branch. Repeating that same smoke
does not replace integration into the intended distribution branch.

Use the existing tested reader/fit/Facts/comparison closure and six-model hash
checks when preparing delivery. Review the actual target-branch difference and
dependency stack; do not merge all historical research code blindly or create
another platform. This review neither publishes nor merges those PRs.

## Six-subitem reconciliation

| Item | What is already supported | What remains; no implicit new experiment |
| --- | --- | --- |
| #541 factor slice | Real volume-factor consumer comparison and same-cost negative result; reusable engineering | This volume gate stays parked; target-branch delivery is distinct from new factor value |
| #542 input/reference | Exact March cohort and operational blind-reference support passed | Retrospective, scoped and consumed; not fresh evidence for another round or historical PIT |
| #543 state learning | Real seven-feature fit and independent reproduction; limited recognition comparison | No broad 12-head, calibrated-probability or production qualification |
| #544 local update | Actual old four-feature update and independent reproduction exist | Its engineering grade does not prove a seven-feature qualified update/consumer lineage |
| #545 consumer comparison | Actual independent C0/C1/C2 capital paths completed; fixed purpose rejected | No candidate adoption or P6; retain interfaces without reopening the same hypothesis |
| #546 closeout | Retention, clean reader, cold construction and this review available | Repair the P2 entry, finish durable integration and reconcile exact lifecycle claims; do not add a positive-return gate |

The old four-feature update receipt is
`cn02:/srv/agentdock/.codex/p10-mvp-window-run-20261025T211421Z-65d0788/rx6800-connect-20261026T062134Z/real-fit-and-reproduction.safe.json`,
SHA256 `7d9dd8585dc8def5b01e8f793d42d6c3f1b25aa47368ba6590336d41ecd2abe5`.
Its real fit/update history is preserved; it is not relabeled as synthetic or absent.

## Checks and limits of this review

The new evidence includes `independent-recount.safe.json`,
`derived-input-real-run-postcheck.safe.json`, `rule-predicate-recount.safe.json`,
`derived-input-gap.safe.json`, exact
live issue/PR/main metadata and plain-text check logs. They contain no model arrays,
credentials, row prices or protected research results. Detailed command outcomes
are recorded in the review's `checks.json`. Fresh checks on Python 3.10.12:

| Check | New result |
| --- | --- |
| Four March regression modules plus blueprint unit tests | 51 passed |
| `make check` on data TMP, with execution-sensitive cases assigned separately | Exit 0; 5,576 passed, 20 skipped, 15 deselected; doctor, compile and planning passed |
| The same 15 execution-sensitive cases on executable system TMP | 15 passed; no added blanket skips |
| Complete suite coverage across those two commands | 5,591 passed, 20 skipped |
| Blueprint append-only/change-impact check and Git whitespace check | Passed |
| Whole-repository lint/type and remote CI | Not rerun; no production Python changed; the known 216 lint findings remain disclosed |

An initial `make check` was terminated by the audit-only disk monitor when a
disposable TMP directory disappeared during scanning. Its log/receipt remain.
The monitor then adopted the existing behavior of ignoring only vanished owned
TMP entries, while permission/other scan errors still stop; the final full check
above completed. This was not a repaired production test or a waived source scan.
Retained new check TMP is 385,413,094 bytes on data storage and 54,395 bytes on
executable system storage, within the frozen bounds and 8 GiB free-space floor.

The review independently rechecks retained evidence and arithmetic; it does not
repeat a two-gigabyte source scan, refit models or rerun capital paths. Original
source/cold isolation evidence is inspected with that limitation. No issue is
closed, no new authority is inferred and no production/P6 component is changed.
