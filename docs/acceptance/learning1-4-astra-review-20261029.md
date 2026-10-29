# Learning continuation steps 1–4: independent review

| Field | Value |
| --- | --- |
| Review | 2026-10-29 Asia/Shanghai; Astra review requested by the Owner |
| Audited revision | `5a89997ca8d2f14d3a50c6b892ec176e5818611a` |
| Scope | Steps 1–4 of the [conditional route](../work-items/learning-next-route-20261028.md); no new research experiment |
| Work items | #540 coordination; #542 qualification; #545 boundary; #546 retained assets |
| Evidence job | `cn02:/srv/agentdock/.codex/learning1-4-astra-audit-20261028T160224Z-5a89997/` |
| Governing parents | [Blueprint mechanism](../design/blueprint-progress.md), [original MVP](../work-items/learning-system-mvp.md) |

## Verdict

The engineering progress is real. It does not yet establish a useful learned
decision component. The current obstacle is the experiment's purpose and design,
not a demonstrated need for another warehouse rebuild, larger disk or new model.
This review supplies the previously requested purpose review; another Astra round
is not a prerequisite for routine preparation of the proposed bounded slice.

| Step | Independent disposition | What remains |
| --- | --- | --- |
| 1. Comparison boundary | Accept the scoped repair: held bytes, source/code/import identities, launch-before-invocation and new no-process count checks are implemented and covered by tests | A newly qualified real capture has not run. Do not claim retroactive repair of the old capture or a general episode-independent engine |
| 2. Minimum asset retention | Accept the minimum reader handoff: six serialized files, original hashes, 18 rejection cases and a clean new reader process | These include duplicate original/reproduced v0/v1 pairs, not six independent models. Feature/census/temporal modules remain outside the minimum handoff; no fresh refit or main integration |
| 3. Consumer opportunity | The decision to park the proposed human priority queue is justified | Choosing that queue was a low-value detour from the algorithmic MVP. No observed backlog, capacity or useful missing human decision was established. This negative result does not reject algorithmic entry uses |
| 4. Qualification | Accept the bounded metadata investigation and refusal to launch an unqualified experiment; do **not** accept experiment qualification | Purpose-specific action mapping, source/action closure, reference, split and acceptance criteria were not completed. Steps 5/6 remain NOT_RUN; 7 remains NOT_ADMITTED |

No new P1 defect invalidating the retained fixed-case results was found in the
reviewed code. Three matters need correction in the next execution route.

## Findings and their effect

### A. Do not turn an invented workflow into a product prerequisite

The original MVP connects qualified inputs through models, experts and safeguards
to algorithmic decisions. `FormalStrategyRunner.evaluate_entry_control` and
`evaluate_entry` already provide the real entry ports. The inspected held consumer
reviews every eligible position. A five-slot research list elsewhere does not
establish a human workload or a five-position capital policy here.

The step3 negative conclusion is sound for its chosen queue. Its absence is not a
reason to stop all learning-to-decision work. The later step4 report already
acknowledges this distinction, but had not supplied an executable alternative.
The proposed amendment now names one: **pre-entry consolidation context**, using
the existing B/P4 opportunity stream and preserving every original guardian.
This is a testable hypothesis, not an assertion of financial benefit.

### B. Separate development diagnosis from fresh confirmation

Step4's script explicitly filters `first_listing[:4] == '2020'` before deriving
the 61-minus-48 remainder. An independently implemented, metadata-only recount
in this review reproduces **64/128/48**, **61**, **13**, and zero overlap with the
inspected newer held identities. The underlying identity inventory has 1,453
records across multiple listing years. Those other records are **not** thereby
qualified, unexposed or available for research. Neither 13 nor 1,453 is a global
usable-sample count.

The old 48-distinct-issuer evaluation cannot be repeated from 13 issuers. However,
that old experiment's sample size and 2020-IPO restriction do not automatically
govern a different, newly versioned question. Repeating the same issuer in two
months does not create two independent issuers. Old evaluation and protected pools
remain excluded from training, threshold selection and claims of fresh evaluation.

An explicitly reused, already qualified **development** population can first
answer whether a fixed candidate changes any decisions at all, duplicates the
simple control or changes only one issuer/date. That diagnosis cannot establish
generalization or profitable selection. It can cheaply eliminate an unpromising
use before commissioning fresh reference work. For any later result-bearing
comparison, model-training time must precede the evaluation anchors; retrospective
source availability must not be re-described as historical PIT.

### C. Distinguish not checked from unavailable; bind the full metadata input

The prior `qualified_new_sources=0` and `qualified_new_references=0` report what
that metadata job constructed. They do not show that zero usable source files
exist. Source/action and reference qualification were not executed. Treating
those zeroes as demonstrated data absence would commission unnecessary P10 work.

The earlier one-off census pre-pins its selected/exposure/native manifests, but
loads the held plan separately and records that parent's digest after use. The
child is checked against the parent, yet the parent and child are absent from the
initial frozen file set. This is a **P2 provenance-completeness gap in that census**,
not evidence of tampering or incorrect counts. The audit pins both exact known
files before parsing, independently recomputes the count, and rechecks all selected
hashes afterward. They agree. This supplies a new complete-closure receipt without
rewriting the old run or claiming it originally had that closure.

Future qualification should freeze every parent/child input up front and use four
distinct states: VERIFIED, NOT_CHECKED, MISSING_EXACT_ITEM, and INCOMPATIBLE_FOR_USE.
Only an actual missing/deficient shared source becomes a bounded P10 request.

## Engineering boundaries retained

- The 20 added integrity regressions check actual runner paths, including fully
  resealed disposable negative cases. Code/config and imported-module identity
  checks execute before materialization. The broad dependency closure is
  conservative; this review does not replace it with an unproved smaller closure.
- The trace producer does not emit an independent `entry_key`; compare checks it
  only when present. Current held bytes and exact date/unit identity are pinned,
  so this is not a bypass of those fixed-input checks. Do not advertise the
  optional check as an independently captured episode witness. Before generalizing
  to reused unit IDs or a new episode consumer, version the producer contract,
  derive the episode from its actual event/ledger relation, require it, and add a
  missing-field negative. This is not a reason to rerun the old B result now.
- The clean-reader OS trace is evidence of the observed file/network accesses,
  not a hermetic operating-system sandbox or an independently rebuilt model.
- PR576 was still draft/open at head
  `66be5431005385af209dbaf4482363ab23b21a04` when checked. The local step4 commit
  was not a published new PR. Reader preservation and main integration remain
  separate claims; no merge or deployment occurred in this audit.
- Seven features remain a research asset, nine remain parked, 10/11's old reduction
  mapping remains parked, and 3/4 are not silently restarted. Head2 is not a
  structural-damage, calibrated-loss or sale-probability model.

## Fresh checks and bounded resources

The audit reran a single complete `make check` on the audited revision:

```text
make check PYTHON=/srv/agentdock/projects/Quanthus/.worktrees/base22-standalone/.venv/bin/python
PYTHONPATH=src
TMPDIR=<audit-job>/tmp
PYTEST_ADDOPTS=--basetemp=<audit-job>/tmp/pytest -o tmp_path_retention_policy=failed -o tmp_path_retention_count=1
```

Python 3.10.12: **5,529 passed, 20 skipped, 8 warnings**, pytest 577.53 seconds;
command 584.356 seconds, exit 0. The old 15 failures were synthetic executables on
the data mount's `noexec` filesystem. Using executable system TMP plus pytest's
ordinary passed-test cleanup fixed the check environment without changing tests,
mount options, source ACLs or old failed evidence. Sampled TMP peak was
41,955,328 bytes, minimum sampled system free space 8,891,543,552 bytes, above the
8 GiB floor. The sampling interval was two seconds; this is an observed peak,
not a kernel-enforced storage quota.

Subsequent changes are audit/route/blueprint documentation only and receive the
focused documentation and append-only blueprint checks. A second whole-suite
repetition is unnecessary without a code change. Whole-repository Ruff's known
216 baseline errors remain disclosed; `make check` does not include `make lint`.
No new type-affecting production code was changed. The delivery check receipt
distinguishes rerun checks from retained and not-run checks.

No market price, label, prediction or economic-result rows were read by this audit's
identity recount. No new model scoring, fitting, update, source acquisition,
production action or P6 execution occurred. The proposed route is in the existing
[next-route work item](../work-items/learning-next-route-20261028.md#astra-reviewed-proposed-amendment-2026-10-29),
not an acceptance claim or a new frozen research request.
