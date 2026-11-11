# Allocation-audit consumer assessment — 2026-10-30

| Field | Value |
| --- | --- |
| Parent | [Historical decision review](../design/historical-decision-review-v1.md#entry-funnel-and-optional-allocation-observation) |
| Work | META-LEARN-001 / #540; existing #545 callers; #2 recordkeeping |
| Authority | Owner approved conditional steps 1–3, within three hours; stop after step 1 without an actual qualified upcoming caller job |
| Baseline | Local `7c8e9a747258020931ccd7e59b2708246cc0175f`; complete tree equals main `178679c7227de16628a4a16deb2a40b01082d7e5` at the recorded snapshot |
| Delivery gate | Bounded demand assessment complete; conditional integration not activated |
| Prior delivery | [Optional runtime audit and historical funnel](entry-allocation-delivery-20261030.md), merged PR #589 |

## Steps 1–3 and the actual decision

1. **Completed.** Within 514.246 seconds, inspect the named ordinary-code callers,
   current work-item/activation records, issue #540/#545/#546 metadata and bounded open
   issue/PR snapshots. No upcoming, already-authorized and qualified replay task needing
   the new allocation sidecar was identified in that scope.
2. **`NOT_RUN_CONDITION_NOT_MET`.** Do not add opt-in forwarding, immutable persistence
   or readback changes without the concrete job. No caller or historical artifact changed.
3. **`NOT_RUN_CONDITION_NOT_MET`.** There is no new integration to measure or qualify;
   no new multiframe performance result or economic replay is claimed. Repository checks
   below validate this handoff; they do not constitute this conditional acceptance step.

`FormalP2ReplayBackend.execute_variant` could persist returned artifacts, but its existing
cache path returns sealed evidence before invoking the runtime. It does not currently
forward the optional flag. `P3Strat003ReplayRuntime.run_research_variant` delegates without
that flag. The structure-economic and exact B-trace producers also exist, within their
retained frozen scopes. These are possible integration points, not evidence of an upcoming
qualified task. Neither an open planning issue nor an old TPlan header marked `active`
establishes new source, sample, reference or execution admission.

This is an unmet **current engineering activation condition**, not a negative strategy
result or a global assertion that audit has no future use. Preserve the PR #589 capability
and its evidence. Old missing allocation reasons remain `NOT_RECORDED`; do not rerun a
market experiment just to manufacture the missing operational history. Head2, P6, policies,
thresholds and production stay at their previous dispositions.

## Evidence and accounting

The job is `cn02:/srv/agentdock/.codex/entry-allocation-consumer-20261029T210011Z`.
`evidence/caller-assessment.safe.json` binds the exact ordinary-code/config/doc hashes,
issue metadata, caller findings and conditional outcomes. Its SHA256 is
`7099680e577d15a4bbb3c71c703424e50c74d3f56e093def0c0d8b0dca02c3df`.
`authority.frozen.json`, both frozen proposal revisions and the initial rejected preflight
are retained. The corrected proposal removed an unrelated S03/S04 review; scope and budgets
were unchanged. Initial TPlan CLI mistakes and a bounded Git fetch timeout remain in the
logs; they are not successful checks or evidence of node/GitHub unavailability.

The assessment reads ordinary code and task metadata only: no new source requests, market
rows, labels, economic results, training or production mutation. Its negative finding is
limited to the enumerated scope, not every external/private or future task. The job and
worktree share a 192 MiB write ceiling and an 8 GiB system free-space floor. Model tokens,
full billable effort and unknown historical campaign costs remain unknown, not zero.

Local verification on Python 3.10.12:

- Blueprint/exploration unit tests and documentation/consumer-example contracts:
  **93 passed**. The same focused selection is checked after the final evidence prose.
- `make check`: **5,763 passed, 28 skipped, 8 warnings**, exit 0; pytest 642.88 seconds,
  whole command 650.19 seconds. The skips are five absent disposable PostgreSQL setups,
  two absent production-artifact mounts, eight absent optional `astock` cases, three
  optional sklearn cases and ten optional torch cases. No test selection was narrowed.
- Exact-base immutable history is verified against full main SHA `178679c...` through
  authenticated GitHub contents, in addition to local checks against its complete-tree
  equivalent SHA. Live issue reconciliation reports no drift.
- The measured peak process-group RSS is 537,509,888 bytes, combined job/worktree
  allocation 92,942,336 bytes and minimum system free space 8,738,045,952 bytes.
  This is test accounting, not a full-history replay performance measurement.

Actual commands, complete results, unchanged test-input closure and resource receipts
are recorded in the read-only handoff.
Hosted CI remains unavailable by Owner instruction. No new Python 3.12, Web, production
replay, or full-repository lint/type qualification is implied. No Python implementation
changed in this batch; existing lint/type limitations remain disclosed by the prior review.

## Blueprint and reopening

The learning checkpoint `CP-540-20261029T210855864308Z` explicitly reviews EXPERT, REPRO
and CLOSEOUT without promoting any facet. The accompanying #2 checkpoint records unchanged
TRACE maturity for the append-only conclusion update. The exploration memory parks only
this current adoption attempt. Previous supported engineering evidence remains current;
quality, strategy value and admission grades are unchanged.
The conclusion also references the earlier engineering checkpoint to retain that asset's
original scope; only the new assessment receipt supports this batch's demand finding.

Reopen only when a maintained caller names an actual near-term task and a concrete missing
operational decision, with exact input/config/window identities, qualified purpose and a
finite execution budget. Select `allocation_audit=True` explicitly for that new run. This
handoff creates no successor experiment, polling task or collection request.
