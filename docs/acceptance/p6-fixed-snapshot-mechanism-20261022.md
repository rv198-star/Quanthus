# P6-HISTORY-001 fixed-snapshot mechanism acceptance

| Field | Value |
| --- | --- |
| Governing design | [Historical replay](../design/lld/08-validation-governance/p6-historical-replay.md) |
| Work item | P6-HISTORY-001; [current recovery checkpoint](../work-items/p6-cn02-start.md) |
| Accepted scope | Engineering mechanism only; no historical PIT, economic, prospective or production acceptance |
| Producer code | `6902a9c37dc8946475e82d2420291cd184695552` |
| Exact source manifest | `sha256:9251e3e1736831d30a3efd59a101a27791ce01bdbd64824b2691540489099e47` |

## Recorded execution

The Owner selected a later fixed snapshot after the strict historical admission
failed. Actual knowledge cutoff is `2026-10-22T13:21:21.312532+00:00`; the
separate historical simulation clock does not rewrite native source times.
Use 120 warmup sessions (2026-01-05–07-06), 50 decision sessions (07-07–09-14)
and a 20-session no-new-entry tail (09-15–10-20). S1/S2/S3 share one continuous
ledger without resets. The hindsight, action-free engineering cohort contains
517 of 5,245 listings, and is not representative-market or historical PIT evidence.
Twenty-two candidate-input rows with unqualified native clocks remained held.

The primary run produced 142 candidate decisions, three trial entries, three
reductions and three full exits. Nine simulated fills and 20 held-position
reviews exercised the accepted expert/veto/P2 paths. All 70 daily ledgers
reconciled; final positions and pending intents were zero. Independent review
found 17 settled and 125 not-executable forecast records, with none unresolved;
these counts do not establish forecast accuracy or live executions.
All 125 not-executable records report `missing_execution_evidence`; this is
missing qualified execution evidence, not proof of 125 actual suspensions or
strategy rejections. It limits usable outcome coverage and belongs to P10's
source-purpose disposition, not to a local P6 data-repair pipeline.

The private data Job is
`/srv/quanthus-base22-data/jobs/p6-fixed-snapshot-20261022T133009Z-6902a9c`.
Its safe, read-only handoff is
`/srv/agentdock/AgentDock/base22-review-afe2526/p6-fixed-snapshot-20261022T133608Z-6902a9c/`.
The handoff SHA256 is
`341eab1f2a589a441b92b716c4ae98d92bd556f159401f16b25a700baf95d710`;
the `SHA256SUMS` SHA256 is
`d20649bb7e69a1993690e4468259f4ed8fb911797f179be250ff52b3ff42efda`.
Private decisions, prices, feature files and monetary ledger values were not exported.

## Reproduction, failure behavior and checks

One physical history, feature panel, candidate scan and reader served smoke and
primary replay. A new process independently rebuilt prerequisites with a new
network/mount namespace, original source roots and primary caches masked,
uid/gid 998 and no external network. Its 70 private cycles, final state, feature
and candidate files matched byte for byte; the five-session smoke prefix matched
the main run. Two disposable copies with a necessary file deleted or one byte
corrupted were refused before replay output creation. Original sources and
sealed artifacts stayed unchanged. The initial isolation harness stopped before
replay because inherited host sysfs was an invalid network-namespace observation;
the corrected socket API check passed without changing isolation.

At producer delivery, broad focused checks had 193 passed; the final type-only
rename had 16 focused passes. `make check` returned 7,453 passed, 26 skipped and
61 warnings. That run began on `438cfc7` while only the local variable type-name
fix was applied; it was not a wholly frozen final-SHA full-suite rerun. Final
primary and independent execution used the exact producer SHA above. Changed
files passed Ruff; two new production files passed mypy. Expanded mypy retained
12 diagnostics identical to the extracted predecessor baseline; repository Ruff
retained 216 failures. Python 3.12 and Web were not run.

The later read-only `review.py` performs an independent semantic account/census
review without rebuilding history/features or rerunning the strategy. Its own
code and checks belong to the successor handoff, not to the producer revision.
Tests cover fully repackaged forged counts, ledger discontinuity, tail entries,
companion-summary forgery, independent divergence, unknown files, symlinks and
unresolved short-tail records. It never upgrades these artifacts to formal P6
observations or economic scorecards.

## Remaining programme boundaries

P6's six earlier engineering children retain their recorded acceptance. The
GitHub issue state has not yet been reconciled with all local acceptance records;
an OPEN issue does not erase accepted work, and this document does not close it.
The local controller remains disabled. A CN02 production successor still needs
the exact deployed revision, read-only qualified Daily and activation mounts,
old-writer reconciliation, readiness/rollback evidence and a valid first cycle.
This execution did not deploy, change services, publish code, amend old ledgers,
access protected partitions or acquire/repair data. The original formal review
and 66+20 prospective budget remain distinct and uncompleted.
