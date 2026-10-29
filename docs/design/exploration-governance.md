# Exploration-driven delivery

| Field | Value |
| --- | --- |
| Design status | approved — Owner requested engineering adoption on 2026-10-29 |
| Delivery gate | open until the local conformance and integration receipt |
| Parent | [LLD 09 roadmap](lld/09-roadmap.md) |
| Work item | META-TRACE-001 / #2; learning portfolio coordination remains #540 |
| Code | `src/quanthus/exploration_governance.py`, `scripts/exploration_governance.py` |
| Contracts | `config/planning/exploration-conclusions.v1.json`, `next-delivery-request.v1.json` in the same directory |
| Tests | `tests/unit/test_exploration_governance.py` |
| Current records | [Conclusions](../work-items/exploration-decisions.md), [blueprint](../work-items/learning-blueprint-progress.md) |

## Purpose and ownership

Quanthus must accumulate usable capabilities and decision-relevant knowledge. A negative
result can complete a bounded experiment and alter future selection without accepting a
model or completing a product. Each new tranche must have a concrete consumer or explain
which decision its information could change. Easy work alone is not high-value work.

The **direction/adoption layer** retrieves prior findings, selects worthwhile questions,
assigns finite resources, interprets results and extracts useful assets. The **execution
layer** carries out the admitted work, validates execution and returns evidence through
TPlan. These are responsibilities, not a mandate for multiple running Agents. No timer,
autonomous proposal execution or new generic research runtime is installed by this design.

| Owner | Owns | Does not establish |
| --- | --- | --- |
| Agentic direction/review | Applicability, question value, evidence meaning, adoption recommendation | Permission beyond the current Owner/domain contract |
| TPlan / domain job | Authority envelope, attempts, cumulative budget, resource guard, recovery | Scientific truth from task completion |
| Conclusion ledger | Append-only scoped conclusions, decisions and reopening conditions | Current module maturity or trading permission |
| Blueprint ledger | Current engineering/quality/value/admission, linked to evidence | Truth from issue lifecycle or positive findings |
| Domain consumer / P10 | Use admission / shared-data governance, respectively | Automatic acceptance because bookkeeping passed |

## Required working cycle

1. Recover the actual blueprint and question. Retrieve relevant old conclusions, including
   their scopes and superseded history. Module overlap finds candidates for review; it is not
   proof that an old negative applies. A renamed family still needs related-module review.
2. Name the consumer, unmet need, minimal deliverable, baseline where applicable, and the
   different actions following success/failure. Establish feasibility before expensive work.
3. Freeze a finite proposal, qualify required inputs/references and bind the actual runtime
   authority. Validate proposal structure before execution; retain the resulting receipt.
4. Execute the smallest sufficient test/delivery. Share deterministic prerequisites once per
   Job. Keep formal independent construction separate. Preserve unknown and failed attempts.
5. Validate execution, then judge evidence at its actual grade. A completed process is not
   necessarily a valid experiment. Apply required independent review at the existing gate.
6. Record the scoped conclusion and a concrete next decision. Deliver qualified components
   to real consumers even when the particular research candidate is rejected.
7. Append the appropriate blueprint checkpoint and link it from the conclusion record; check
   and commit them together. If reconciliation fails, retain evidence and repair metadata;
   do not repeat the experiment or report the module update as complete.

There is no obligation to produce a binary scientific verdict. An inconclusive result can
lead to a rational stop based on cost or better alternatives. It must remain inconclusive.
Reviewers must not manufacture a negative label to justify stopping.

## Records and proposals

`exploration-conclusions.v1.json` is a small metadata ledger, not a market or model store.
Each record identifies a question/family, work item/modules, purpose/time scope, execution
status, verdict, disposition, claim ceiling, evidence, retained assets, cost review and
reopening conditions. Evidence uses the existing blueprint full-Git/SHA256 or external-receipt
contract. External receipts are declared identities, not files reopened by the checker.
Each module must be covered by a referenced existing blueprint checkpoint.

`completed`, `blocked`, and `invalid` describe the result-bearing execution, not whether an
administrative source assessment completed. Blocked/invalid execution must remain
`not_evaluated`. `not_met` is failure of the frozen use gate in this scope, not proof of no
effect in the population. `supported` remains separate from consumer admission.
`candidate_for_adoption` is only a recommendation; all records explicitly grant no admission.

Records are append-only. A new conclusion may supersede the current record for the same
question/family. It retains original evidence and costs. Corrected evaluators or new evidence
require an explicit superseding judgment and downstream impact review, never silent editing.
The first adoption imports three already-consumed cases, not all project history. Missing
older costs stay `unknown_not_zero`; a recorded cost needs actual attempt references.

A prospective request names kind (`delivery`, `repair`, `exploration`), family, modules,
consumer, scope, deliverable, acceptance/stop conditions, decision branches, cumulative-cost
review and finite wall/network/write budgets. It binds the canonical conclusion ledger hash.
Every current related record needs a reasoned `respect`, `different_scope`, or `reopen` review.
Different scope requires a concrete distinction; reopening requires a material change and
evidence. The checker validates presence/identities, not whether that judgment is persuasive.
Respecting a parked same-family exploration cannot launch it unchanged. Engineering reuse
may respect the scientific stop without reopening the experiment.

The example next request is **proposed**, never an activation. `preflight` always returns
`PROPOSAL_CHECKED_NOT_AUTHORIZED`. It does not run commands, inspect market payloads, contact
suppliers, schedule tasks or deduct resources. TPlan still owns live cumulative accounting and
runtime guards. The metadata check cannot prevent an unrelated command being run outside this
process; contributor rules and domain admission enforce that boundary.

## Selection, budgets and evidence proportionality

Select among actual consumer delivery, necessary repair and bounded exploration. Consider
benefit, confidence, total acquisition/qualification/implementation/integration cost and reuse.
Unknown ROI is not zero, but must not become an invented numeric ranking. A direction's full
attempt history and sample exposure follow it across new samples, features, issues or Agents.
Do not reset cost or claim a fresh holdout by renaming a task or independently reproducing it.

No fixed 70/30 allocation is enacted. Preserve room for high-value usable delivery; review
the allocation using actual outcomes. No worthwhile executable question is a valid wait state.
Unused budget need not be spent, and a loop must not create work merely to remain active.

Choose evidence proportional to the decision **before** observing outcomes. A small reversible
interface change need not rebuild a warehouse or repeat a capital experiment. A small screen
may support declining further investment without qualifying adoption. Existing safety, PIT,
protected partitions, expert/veto paths, frozen policies and independent acceptance remain.
Do not infer availability times, convert missing input into structural damage or force sales.
Shared-source acquisition and repair remain P10's responsibility; only affected steps stop.

Human involvement is for actual decisions outside the authorized envelope, not every file or
routine continuation. If no eligible task remains, preserve state and stop/wait. Timer or agent
activation requires its own finite scope and lifecycle; this implementation installs neither.

## Commands and conformance

```bash
python3 scripts/exploration_governance.py context --family head2-entry-filter --module S02
python3 scripts/exploration_governance.py preflight --request config/planning/next-delivery-request.v1.json
python3 scripts/exploration_governance.py render > docs/work-items/exploration-decisions.md
python3 scripts/exploration_governance.py check --base-ref FULL_40_CHARACTER_PR_BASE_SHA
make exploration-check
make check
```

`planning-check` includes the offline memory/view/next-proposal check. The exact-base command
also rejects rewritten/deleted history, even when a generated view and all hashes were updated.
Use the same check locally because hosted CI is unavailable. It does not forge remote success.
Generated views are disposable projections; the ledger remains the recorded-decision source.
Use the existing blueprint recorder for module updates; do not edit its generated view.

Conformance includes blocked execution mislabelled negative, admission promotion, stale or
unreviewed memory, dishonest zero cost, missing reopening evidence, scope mismatch, nonfinite
budgets, rewritten history, valid scoped reuse, and withdrawal/supersession. Scientific
applicability and ROI still require an accountable Agent review in addition to tests.

Report capability delivered, question resolved, actual decision changed, retained usable asset,
cost and remaining work. Do not count trials, issue closures, code volume or positive-result
percentage as product success. A finite delivery stage may define a weighted denominator;
the 12+X blueprint itself still has no issue-count-derived completion percentage.

## Research basis

The mechanism adapts [RD-Agent-Quant's hypothesis/feedback cycle](https://arxiv.org/html/2505.15155v2),
[Booking.com's searchable successes, failures and decisions](https://arxiv.org/html/1710.08217v1),
and [Microsoft ExP's pre-experiment design checks](https://www.microsoft.com/en-us/research/articles/patterns-of-trustworthy-experimentation-pre-experiment-stage/).
These motivate the design, not Quanthus performance claims. Market backtests are not online
randomized experiments or deterministic correctness tests. The Owner approved engineering
adoption; the prior discussion artifact alone is not a governing source.
