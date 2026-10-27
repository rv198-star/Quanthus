# #541 / PR #572 independent review

This is an audit of `8c4bd034ec1a022e13a44986bce33d08b01d565a`, relative to the
slice baseline `ca52277c7187938e7b4149cb4bad687c663a2bd3`. PR #572 remains a draft,
stacked on unmerged PR #570. Governing design:
[one-factor research review slice](../design/factor541-research-review-slice.md).
The [original delivery](factor541-review-slice-20261028.md) and failed first attempt
remain historical evidence. This review changes neither policy nor runtime code.

**Disposition: fix three P2 reuse-boundary defects before merge; retain the verified
fixed-input result. Purpose value is inconclusive, not accepted.** No P0/P1 issue
was established in the inspected slice. This is not a P10 source-quality failure.

## Reproduced findings

| ID | Priority / location at reviewed SHA | Evidence and required correction |
| --- | --- | --- |
| R1 | P2, `src/quanthus/stage_learning/factor_slice.py:56` | The catalog accepts declared lookback 60 while the engines still compute 20; a value appears after only 21 rows. Wrong declared unit, factor version and business year also pass. Reject unsupported semantic contracts, or derive behavior from a completely validated versioned contract. The synthetic year mutation read no protected data. |
| R2 | P2, `src/quanthus/stage_learning/factor_slice.py:96` | The reusable consumer validates digest syntax rather than equality with the qualified definition/source/panel. Well-formed unknown digests pass; changing a synthetic volume value from 0.5 to 2 without changing its revision changes selection from 0 to 1. Bind the public consumer to a verified immutable panel and exact semantic/source closure. The original trusted in-Job producer and full arithmetic oracle did not produce this mismatch. |
| R3 | P2, `src/quanthus/stage_learning/factor_slice.py:354` | An empty or policy-only `code_and_config_members` list passes the guard, creates the output directory and reaches a stubbed source reader. Require the complete executable/config closure, including its relationship to the code actually running, before mkdir or source access. The probe did not produce READY or read a real source. |

These are disposable synthetic reproductions, not assertions that the original fixed
source was corrupt. Original run closure contains 495 members and was checked against
the reviewed code. Regression evidence: `boundary_probes.py` and
`evidence/boundary-probes.safe.json` in the audit directory below.

## Filtering and arithmetic

An independently written Decimal/rolling-window and queue-rank recount shared only the
native admission adapter. It did not call the reviewed `project_facts`,
`audit_arithmetic`, `consume_queue` or `compare_queues` implementations.

| Quantity | Independently verified |
| --- | ---: |
| Fixed 2021 issuers / security-days / dates | 128 / 19,302 / 241 |
| Available exact price and volume values | 15,570 each |
| Nontrading / insufficient-history rows retained | 56 / 3,676 |
| Price pool / after volume gate | 585 / 536 |
| Baseline / candidate review slots | 433 / 395 |
| Retained / removed / replacements | 390 / 43 / 5 |
| Slot reduction | 38 (8.77598%) |
| Price-pool exclusions | 49 (8.37607%) |

Every compared numeric string and daily selection matched. Full panel and queue bytes
match the previous separate cold run; all eight selected source hashes remain unchanged.
Knowledge time remains unknown. This establishes computation and observed selection,
not historical PIT, predictive usefulness or a trading result.

The consumer is a newly implemented standalone research queue. Repository inspection
found no existing expert/operations/manual-review integration consuming its output.
No measured person-time, independent usefulness criterion or cost-matched outcome
comparison exists in this delivery. Fewer slots alone cannot accept purpose value.

A separately frozen **capacity-only** audit used the first N entries of each original
baseline queue, where N is the candidate's same-day count. Both queues then contain395
slots: 377 common, 18 replacements across17 dates and27 distinct affected issuers.
This conditional quota control is a diagnostic, not an independently deployable policy.
No future outcome was read. For a binary per-slot success score, the largest possible
full-queue difference is18/395 =4.55696 percentage points; this is an arithmetic bound,
not an estimated effect or proof of negligible value. Repeated daily rows are not
independent experiments. A broad new study is not justified by these counts alone.

## Costs and independence

The recorded full run took24.9800 seconds with348,663,808 bytes peak RSS (332.51MiB);
the prior cold run took25.3724 seconds. Shared Facts/factor construction used18.281
seconds, source admission4.397 seconds, arithmetic0.596 seconds and the queue loops
0.240/0.229 seconds. These are single instrumented observations. They neither isolate
the incremental factor cost nor establish a speed improvement. No human minutes or
investment benefit was measured. The audit's independent recount took7.27 seconds;
it is different work and is not a performance competitor.

The original cold trace hash was rechecked: zero old-source, native-root or first-run
output opens, and zero IP connections. The eight copied source files have link count1.
Two UNIX socket attempts concern local name-service lookup, not source-network access.
The original namespace nevertheless mounted the entire current Job, making first-run
outputs physically reachable. Observed non-use supports this actual rebuild, but is
not a generic isolated artifact-consumer guarantee. A subsequent cold run should mount
only its code, source copy and new output; the current evidence must retain this limit.

## Fresh checks and scope

The reviewer independently ran the full `make check` on unchanged runtime code:

```text
timeout --signal=TERM --kill-after=15s 1200s make check \
  PYTHON=/srv/agentdock/projects/Quanthus/.worktrees/base22-standalone/.venv/bin/python
```

Direct exit0, 614.334 seconds; **5,348 passed,20 skipped,8 warnings**, followed by
successful remaining planning/blueprint checks. This resolves the old missing outer
wait-status uncertainty through a new run; the old observer failure is not rewritten.
Python3.10.12, pytest8.4.2, Ruff0.16.10, mypy1.20.2, pyarrow23.0.1 and DuckDB1.5.6.
Original56 focused tests, changed-file Ruff and isolated mypy are inherited results,
not new claims. Repository-wide lint/type debt was not rerun or declared resolved.
Remote Actions failed before job execution because of account billing/spending limits;
local success is not remote CI success. No new Python3.12 run was performed.

No new source requests, labels, outcome comparison, economic results, protected years,
training, state3/4 scoring, P6, production or merge occurred. New evidence is safe counts,
synthetic inputs and metadata. The source freeze preceded native row verification.

## Blueprint disposition and audit evidence

Append a FACTORS engineering `partial` checkpoint for the three reuse defects while
preserving the accepted arithmetic of this exact run. Narrow quality to the inspected
definition/input identity; do not claim arbitrary panels are bound by the public helper.
Change FACTORS purpose value to `inconclusive`. Admission remains research-only;
the broader #541 and parent MVP remain open. Old checkpoint records are immutable.

Audit directory:
`/srv/agentdock/.codex/factor541-review-20261027T204749Z-8c4bd03`.
Primary evidence: `evidence/review-verdict.safe.json`,
`evidence/independent-recount.safe.json`, `evidence/boundary-probes.safe.json`,
`evidence/equal-cost-capacity.safe.json`, `evidence/cold-trace-review.safe.json`,
`evidence/make-check.safe.json`, `logs/make-check.txt` and `checks.json`.
The separately delivered `next-plan.md` is a non-governing proposal, not executed
research or an alteration to the frozen request. This audit recommends concentrating
the next bounded tranche on the three fixes and one cost-matched usefulness diagnostic,
not expanding factors, state qualification or P6 before evidence warrants it.
