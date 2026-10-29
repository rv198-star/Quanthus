# Existing-consumer delivery: bounded acceptance

| Field | Value |
| --- | --- |
| Work items | #540, #545, #546 |
| Scope | [Executed steps 1–3](../work-items/learning-consumer-cycle-20261029.md) |
| API design | [Supported assets](../design/learning-assets-v1.md) |
| Base | `caa54653ab8a02d0197e887db094533f6c672eff`; its tree equals verified main merge `cc2801f838511e8b5db6ca8cba4e86a2c67bcdb1` |
| Result | consumer example passed; task-selection NO_GO; final local-check receipts in the handoff |
| Evidence job | `cn02:/srv/quanthus-base22-data/jobs/learning-consumer-cycle-20261029T120743Z` |

## What was validated

The verbatim [calling example](../operations/learning-assets-consumer.md) runs with the current
source and existing frozen registry in a new Python 3.10 process using `-I -S -B`. It imports
the production consumer, uses synthetic facts, and returns complete ordinary decision JSON.
There is no new production implementation, feature, threshold, fit or market-data adapter.

The ready synthetic entry returns `trial_entry`; missing required input returns
`no_action` / `block_new`; missing held input and mark retain `no_action` without a forced
sale. A synthetic pending Head2 score of 1 remains `abstained` / `NOT_GRANTED` and leaves
both the ready and blocked aggregation results unchanged. The receipt binds the exact
document/code block, strategy/topology identities and output hash. These are interface
observations, not inferred fills, historical PIT or market-quality evidence.

The source review confirms that an existing actual consumer is present. The delivery closes
its missing runnable usage explanation; it does not claim a new functional adapter or live
consumer onboarding. Replacing the synthetic facts with real observations still requires
the caller's existing purpose and input admission.

## Task-selection review

Only the two precisely hash-matched prior safe aggregates and the pinned repository reports
are read. The existing held census, entry diagnostic and later March conclusion retain their
denominators, ordering, exposure and limits. The 790 diagnostic cases are not fresh evaluation;
the later failed fixed-use gate takes precedence over an earlier plan to continue that use.
Unknown structure and unobserved allocation/fill alternatives are not negative labels or
confirmed deficiencies. No source archives, price rows or protected research results are read.

There is no established new material permissible decision gap in this selected evidence.
The correct branch is `NO_GO_NEW_COMPARISON_FROM_CURRENT_EVIDENCE`; the conditional comparison
is `NOT_RUN_CONDITION_NOT_MET`, not a failed or completed market experiment. Record a park
decision with concrete reopening conditions. This conclusion does not cover every possible
Head2, state, entry or allocation use in the project.

## Checks and review boundary

Fresh focused expert/Head2/census, governance and documentation checks: **134 passed**.
Selected Ruff, doctor and planning pass. Exact-base append-only checks for both ledgers pass;
live issue reconciliation reports no drift. The first blueprint history check required an
explicit TRACE no-change disposition for the metadata edits; that original failure is retained
and the added no-change checkpoint passes the final check. No experiment was rerun to fix it.

The preceding full-check source manifest has 1,476 members: 1,473 remain byte-identical and
the only changes are the three named planning ledgers/proposal-metadata files. Production
code, tests, dependencies, Makefile and runtime policy/config remain byte-identical. Under the
repository's documentation/ledger-only follow-up rule, reuse the prior **5,683 passed /
20 skipped** complete Python coverage, with current focused/documentation/planning checks.
This is not a fresh full-suite `make check` or hosted-CI execution claim. The prior split
noexec recovery and 193 existing whole-repository lint findings remain explicitly preserved.
There is no modified production Python requiring a new type check; a fresh whole-repository
mypy, Python3.12 and Web suite were not run. Exact commands, hashes and logs are in the handoff.

This is a Sol engineering and task-selection self-review, not another Agent's independent
scientific audit. Code/metadata publication and final Git identity are supplied separately in
the handoff. Consumer, research-quality, P6 and production admission are not granted here.
