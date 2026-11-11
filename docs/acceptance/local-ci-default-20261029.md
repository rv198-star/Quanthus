# Local acceptance and learning delivery follow-up — 2026-10-29

| Field | Value |
| --- | --- |
| Authority | Owner: treat GitHub CI as unavailable by default and resolve delivery locally |
| Governing rule | [Local acceptance default](../../AGENTS.md#local-acceptance-default--owner-2026-10-29) |
| Parent | [Learning delivery](../work-items/learning-delivery-20261029.md), #546 / PR #577 |
| Prior tested production code | `bf0a928f39c675226e5b98ec23045ddcc8c3264e` |
| Follow-up starting head | `9a7e40bfbeb5a1d745f0bf23b3ead2d1c351819a` |
| Exact PR base | `a332e79f3f548f6c0ea558092f745b584045de80` |
| Evidence root | `cn02:/srv/agentdock/.codex/local-ci-default-20261029T090122Z-9a7e40b` |

The earlier [complete local acceptance](learning-delivery-20261029.md) remains
valid for its byte-identical executable/config/test closure: Python 3.10.12,
5,652 unique passes and 20 skips, selected Ruff/mypy passing, and 193 existing
whole-repository lint findings in files unchanged from the PR base. The reuse
proof covers all tracked non-documentation files except the append-only progress
ledger; its configuration, test, workflow and dependency bytes are unchanged.
The follow-up changes only operating guidance, current recovery prose and progress
records. Targeted blueprint tests, exact-base history, planning, affected issue
reconciliation and diff checks are recorded separately; this is not another full
test run or another research result.

Self-review finds no blocker to this bounded engineering delivery. The new census
authenticates the already-consumed selection, preserves its denominator and original
retrospective cutoff, rejects invalid joins and guard bypasses, and leaves unknowns
unavailable. It does not establish entry/allocation counterfactuals or value. Six
retained model readers and their rejection cases remain valid compatibility
evidence. Specialized March drivers and their open P2 remain excluded and archived.
The present review is the executing agent's self-review, not a new independent audit.

GitHub-hosted Python 3.10/3.12, Web and ledger jobs did not execute because of
account payment/spending limits. Python 3.12 and Web are NOT_RUN in the local
batch; no Web files are changed. The local result must not be labelled a passing
hosted matrix. Existing optional skips and baseline lint remain disclosed.

No billing, workflow, protection, credentials, runner service or production setting
is changed. Ordinary merge still requires its existing authorization and GitHub's
actual enforcement. Head2 experiments stay parked; overall MVP consumer admission
stays NOT_GRANTED. Final commit, local-check receipt, ordinary merge result and any
technical rejection are recorded in the follow-up evidence, not predicted here.
