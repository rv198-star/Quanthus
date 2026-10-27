# Blueprint-linked delivery progress

| Field | Value |
| --- | --- |
| Design status | approved — Owner requested this mechanism on 2026-10-27 |
| Delivery gate | accepted-conditional for the mechanism; default-branch integration is merged, remote Actions execution remains a separately recorded limitation |
| Work item | META-TRACE-001; first bounded adoption: META-LEARN-001 / #540 |
| Parent | [Delivery roadmap](lld/09-roadmap.md) |
| Module architecture | [Layered state portfolio](stock-stage-vocabulary-portfolio.md) |
| Runtime | `src/quanthus/blueprint_progress.py`, `scripts/blueprint_progress.py` |
| Conformance | `tests/unit/test_blueprint_progress.py` |

## Purpose and authoritative records

A contributor must be able to answer: which product module does this task serve, what changed
in that module, and what remains before its actual use? One source of current gaps replaces
repeated hand-written status summaries. This mechanism initially covers #540–#548 and all
12+X state modules, with P10 and existing P3/P6 capabilities referenced as dependencies.
It does not retrospectively rewrite every historical issue or launch the proposed next research batch.

| Record | Owns | Does not own |
| --- | --- | --- |
| `config/planning/learning-blueprint.v1.json` | Stable module IDs, hierarchy, dependency map, work-item/issue contribution | Scientific truth or runtime authority |
| `config/planning/learning-progress.v1.json` | Append-only scoped module outcomes, evidence identities and acknowledged issue metadata | New thresholds, access, training or deployment permission |
| Generated [current blueprint](../work-items/learning-blueprint-progress.md) | One readable module matrix, current gap per module and work-item map | Independently edited status |
| GitHub issues | Open/closed/reopened lifecycle and task discussion | Module acceptance or automatic product completion |
| TPlan / job receipts | Execution state, attempts, checks and recovery context | Automatic promotion of a module when a Task says completed |

The tracking catalog references prior domain contracts. It is not a replacement for them.
Changing a research threshold, sample, purpose or use authorization still follows its existing
governing contract. Optional parked states do not block independently qualified modules.

## Four independent progress facets

Each module reports **engineering**, **quality**, **value**, and **admission** separately.
Every asserted outcome names its exact scope, evidence, remaining gap and next action.
An engineering pass cannot set any other facet. A rejected experiment can be a completed task
while module quality remains rejected or inconclusive. Unassessed is explicit; absent evidence
never becomes a negative market label. Development qualification of 10/11 remains development;
Head2 actual fit/update remains different from usefulness; P6 non-activation remains different
from completed simulation.

There is no overall percentage derived from closed issues or number of heads. A later fixed,
weighted acceptance denominator would require an explicit versioned product plan. The view
shows current facets and the chosen focus gap for each module. `context` exposes all facets.
Parent modules have their own evidence; child counts never automatically accept a parent.

## Normal working loop

1. **Start:** `python3 scripts/blueprint_progress.py context --issue 543` shows contribution,
   affected modules, parent/dependencies, current outcomes and their exact checkpoint IDs.
   New tracked work first registers a stable ID, issue number and affected module IDs. Items
   outside this first adoption scope keep their existing plans; do not block unrelated work.
   Add `--module S02` to recover only the current head instead of loading every sibling.
2. **Do the work:** use the existing TPlan/job protocol. Routine logs and small edits need no
   progress record. At a milestone, rejection, blocker, defer/stop or issue lifecycle change,
   the executing agent assesses its module impact and prepares one checkpoint. The Owner is
   not asked to manually maintain this table or reapprove already authorized file reads.
3. **Record:** `python3 scripts/blueprint_progress.py record --checkpoint /path/update.json`.
   The script verifies links/evidence identities, rejects stale predecessor records, appends
   under a local file lock, and regenerates the view. Replaying identical ID/content is idempotent.
   To avoid manually assembling issue metadata, first run
   `python3 scripts/blueprint_progress.py prepare --issue 543 --output /path/update.json`.
   It reads only that issue, creates a new draft without overwriting existing files, and leaves
   the evidence/judgment fields empty so it cannot accidentally advance progress.
4. **Handoff:** run `make blueprint-check` and the applicable repository checks. Commit the
   catalog change (if any), appended record and generated view together. PRs name the work item,
   modules, changed facets, evidence and remaining work. A justified no-module-change is valid.
   PR CI checks changed `watch_paths` from the **base** catalog. Every affected module needs
   an appended update or `reviewed_modules` plus `no_change_reason`. This catches a completed
   code change whose issue has not yet closed; removing a watch in the same PR cannot bypass it.
5. **Issue close/reopen:** `make blueprint-audit` reads only the registered GitHub issues. Any
   unacknowledged lifecycle/body/updated-time difference is `NEEDS_CHECKPOINT`, never a module
   pass. Append an observation from the returned receipt, together with the impact judgment
   or a no-change rationale. Do not rewrite the baseline or re-open a historical issue merely
   to make the dashboard green. An issue comment also updates GitHub's timestamp; acknowledge
   it at the next meaningful checkpoint, not by inventing new module progress.

When a work item closes before its record lands, the lifecycle workflow preserves a failed
reconciliation receipt. The follow-up commit reruns reconciliation on main. Fix the affected
bookkeeping before reporting that progress; other authorized work continues. Network/API failure
is an audit failure, never an empty/green snapshot. No workflow edits or closes GitHub issues.

## Checkpoint contract

A checkpoint contains `id`, timezone-aware `recorded_at`, `actor`, `work_item`, `outcome`,
`summary`, `evidence`, `updates`, and optional `issue_observations`. Use a unique stable ID;
chronology cannot move backwards. The first record alone may use `baseline`.

Each update contains `module`, `facet`, allowed `status`, `scope`, `remaining`, `next_action`,
`evidence` IDs and `supersedes` (the previous checkpoint for this exact module/facet, or null).
A multi-module issue records only the modules/facets actually affected. Do not copy a task's
completion flag to every mapped head. An empty update list requires `no_change_reason`.
`reviewed_modules` may explicitly acknowledge scoped implementation changes that do not alter
reported maturity; it is not an alternate acceptance field and requires the same rationale.

Evidence records use a full SHA256 and a `claim_ceiling`. Git metadata uses a full Git SHA plus
a `docs/` or `config/` path; verification reads that immutable revision, not mutable HEAD.
External receipts use an exact locator and `verification=recorded_not_reopened_by_ci`.
CI checks their declared identity, not their existence, semantic correctness or authorization;
the recording reviewer must inspect them within their existing access authority. No payload,
credentials, model arrays or protected economic results belong in the ledger. An accepted
admission additionally requires an explicit `kind=authority` evidence reference; this is a
traceability check, not a replacement for domain authorization.

Issue observations come from `audit` and bind the exact number, stable title prefix, lifecycle,
updated time and body SHA256. They contain no issue-body text. A normal checkpoint may
acknowledge only its own issue; the initial baseline may import the fixed registered inventory.

## Failure, concurrency and history

Malformed records, unknown/cyclic modules, orphan work, missing evidence, invalid hashes,
stale `supersedes`, duplicate changes, wrong issue identity or incomplete snapshots fail checks.
`check --base-ref FULL_GIT_SHA` refuses edits/deletions of the old ledger prefix. CI uses the
PR base or push predecessor; ordinary local `check` cannot independently establish past history.
Concurrent writers re-read the ledger under a POSIX lock. A crash between the authoritative
ledger rename and view rename leaves a stale view that `check` rejects; `render` safely repairs it.

After adoption, main is the shared checkpoint baseline. Long-lived research branches must
recover the latest blueprint before work and submit their scoped checkpoint against that baseline;
they do not become parallel progress authorities. A stale `supersedes` must be reassessed, not
silently overwritten. Unmerged research receipts may be referenced by identity without merging
the research implementation or importing its results into an unrelated branch.

Do not hand-edit generated Markdown. Do not copy the latest local uncommitted status into a
production authority file. Catalog topology changes must explain their product impact in the PR;
the mechanical cycle/link checks do not decide whether a dependency is substantively warranted.

## Automation and adoption boundary

`make planning-check` includes the offline blueprint check, so `make check` includes it.
The `blueprint-progress` workflow adds immutable-history checks to PR/main updates and a
read-only reconciliation on registered issue events, main updates and manual dispatch. It
keeps failure receipts as artifacts; it has only contents/issue read permissions, no data or
deployment credentials, no auto-promotion, no automatic issue comments and no new daemon.

GitHub issue events select this workflow only after it is on the default branch.
Adding the workflow does not silently enable branch-protection requirements. Default-branch
installation, an actual runner execution and a successful reconciliation are separate facts.
When remote runners cannot start, use the same CLI locally and retain the remote failure;
do not describe installation or a local pass as a successful Actions run. The current adoption
receipt is linked from the `TRACE` module in the generated blueprint.
This is one bounded addition to existing planning and TPlan, not a new general project platform.

Local implementation evidence: [2026-10-27 conformance record](../acceptance/blueprint-progress-20261027.md).
