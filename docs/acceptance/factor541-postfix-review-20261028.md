# #541 / PR #572: post-fix independent review

Governing design: [one-factor research review slice](../design/factor541-research-review-slice.md).
Reviewed head: `5b73a1916939d69728c06a4696fd577c253d79cd`.
Runtime code: `a1d333cace5eea26375998ec9bac3cf68beeac2a`.
Previous findings: [initial review](factor541-astra-review-20261028.md).
Implementation evidence: [bounded delivery](factor541-convergence-20261028.md).
Review type: separate Astra review of the preceding Sol implementation, with fresh
tests and independently written arithmetic; not an independent market source or human panel.

**Accept the three P2 closures and the bounded calculation. Retain the engineering
asset and park this volume gate.** No new actionable P0/P1/P2 was established in
the inspected slice. This is not acceptance of broad #541, the learning MVP,
predictive quality, historical PIT, expert decisions or P6. No production code or
research policy was changed by this review.

## Three P2 closures

| Finding | Verified correction | Remaining trust boundary |
| --- | --- | --- |
| R1: declared semantics disagree with the executed formula | Complete supported policy digest is checked before building. Lookback, exclusion of T, unit, version, missing policy and year mutations fail; the original formula/Registry remain unchanged. | Only this versioned, fixed-purpose 20-session contract is supported. This is not an arbitrary factor execution platform. |
| R2: unverified values or identities can change the public queue | `PanelIdentity` binds all panel bytes, exact definition/policy, each security/date source member and factor/Fact revision. Bare panels and mutated values, source or definition fail. | Expected identity must come from the qualified producer/receipt. A caller hashing its own unqualified panel does not establish source truth. |
| R3: incomplete executable closure reaches source access | Exact 497-member code/config closure and actual imported Quanthus origins are checked before mkdir or source reads. Optional executor is loaded before checking origins; canonical CLI dispatch and diagnostic handoff identity are tested. | This is the declared Python/config closure with a recorded runtime, not a claim to verify every installed binary or tolerate concurrent code replacement. |

Fresh focused run: **66 passed in 2.77 seconds**, direct exit 0. The review also
compared every declared code/config hash with the present tree. All 497 matched.
No previous acceptance/failure receipt was overwritten.

## Same-count result and stopping decision

A new standard-library recount imports none of the producer, consumer, utility
evaluator or previous oracle. It reconstructs the daily matched prefix, every
selected key, changed/common sets, outcome denominators, missing-result bounds,
issuer/date removal sensitivities and monthly contributions.

| Outcome | Same-date same-count control | Volume gate |
| --- | ---: | ---: |
| Review slots | 395 | 395 |
| Known quick failures | 255 | 253 |
| Known non-failures | 124 | 126 |
| Unknown | 16 | 16 |

There are 377 common slots and 18 changed selections per arm, over 17 dates and
27 issuers. The changed sets contain one unknown each. The resulting **1–3 fewer
failures / 0.25–0.76 percentage points** are fixed-sample missing-data bounds,
not a confidence interval or an estimate of future population benefit. Common
unknowns cancel exactly; no unknown is silently removed or counted as success.

The frozen continuation rule requires robustness that is absent: no bootstrap
is generated while changed outcomes are unknown, and removing one affected
issuer leaves zero known improvement. Thus parking follows the preregistration
and is also a reasonable allocation decision. The evidence is mildly favorable
but too fragile to justify further tuning or promotion; it does not establish
that volume information is universally useless.

A second new calculation shares only the previously qualified native admission
adapter. It walks backwards through each valid coordinate/session history,
reconstructs 15,570 prior-20 price anchors, then all 585 five-session targets,
unknown reasons and source-member sequences. All match exactly. No Facts engine,
queue implementation, utility evaluator or preceding oracle is called. The
full pool remains 362 failures, 205 non-failures and 18 unknowns. It reads only
the already frozen 2021 fixture; no 2022 tail, new sample, acquisition or threshold
was introduced. Historical knowledge remains unknown.

## What the cost and cold evidence actually establish

The comparison matches review **slots**, not measured human time. Its per-day N
comes from the candidate count; this is a conditional-capacity diagnostic, not a
new independently deployable quota policy. Neither a 38-slot reduction in the
original unequal-count comparison nor equal slot counts prove time savings.

Recalculation confirms the five alternating queue pairs' median incremental
time of +0.020141 seconds. The recorded activity-engine component is 0.506117
seconds. These exclude some shared construction, binding and verification work;
total factor incremental cost and person-time remain unmeasured. The instrumented
36.895-second complete run cannot be compared directly with the earlier
24.980-second run as a performance improvement or regression.

The prior cold run's frozen namespace command, original-output denial, trace
digest and zero IP/forbidden-source opens were reviewed; all eight deterministic
outputs still match full bytes. Missing/corrupt-copy failures remain bound to
their original receipts. This review did not rerun cold construction or those
destructive-copy tests and does not label them as fresh executions. Fresh checks
verified all 73 readonly-export members and all eight selected source files'
SHA256, size, device, inode and mtime. Source bytes remained unchanged.

## Blueprint and integration disposition

FACTORS engineering and fixed-input arithmetic remain accepted within this
slice; value remains inconclusive and admission research-only. #541 stays open.
The learned-head, independent-reference, actual expert-decision and learning-MVP
gates are unchanged. Old 10/11 development semantics and Head2's retained research
asset are not automatically qualified consumers or trading signals.

The current PR worktree's old P3 work-item recovery prose predates the separately
committed retrospective closeout. Review used its exact acceptance record at
`4199786e5a0a0fdde55ed84271b3ed703f0aa3fc` plus live issue state, not the stale
paragraph as authority to reopen B/D. Any successor must bind that versioned
disposition explicitly. Existing B/BH/TH results must not be replayed under a new
name or counted again as new value evidence.

PR #572 remains draft, stacked on unmerged draft #570. GitHub Actions runs
40592066808 and 40592066847 did not start job steps because of account billing/
spending restrictions; local checks are not remote CI success or merge approval.
The separate non-governing `next-plan.md` proposes a bounded #545 decision-use
preflight, not an activated expert policy, source request or strategy experiment.

## Fresh checks and evidence

Evidence root:
`/srv/agentdock/.codex/factor541-postfix-review-20261027T221927Z-5b73a19`.
See `evidence/independent-recount.safe.json`, `evidence/target-review.safe.json`,
`evidence/cold-and-cost-recheck.safe.json`, `evidence/make-check.safe.json`,
`checks.json` and plain-text logs. A fresh full `make check` returned direct exit 0:
**5,386 passed, 20 skipped, 8 warnings**, pytest 613.22 seconds and total 616.277
seconds, with doctor, compile and planning/blueprint checks passing. This used
Python 3.10.12 and the unchanged reviewed runtime code. Skips concern absent
production/PostgreSQL fixtures and optional sklearn/torch dependencies.
After the audit/ledger documentation update, 33 documentation/blueprint tests
passed in 1.06 seconds. Targeted Ruff passed on both modules and all three factor
test files; isolated mypy (`--follow-imports=silent`) passed on the two modules.
The append-only blueprint check against the reviewed head passed.

The recount's first harness attempt assumed
a list of objects where the sealed cold receipt has filename strings; its
failure log is retained, and the corrected schema handling does not change an
acceptance rule or source. Final check counts and the audit commit are recorded
in the handoff. Whole-repository lint/type debt and the lack of a fresh Python
3.12 run remain disclosed.
