# P6 historical replay over the retained Base 2.4 handoff

| Field | Value |
| --- | --- |
| Parent | [Validation governance](../08-validation-governance.md) |
| Design status | approved direction: Owner explicitly chose historical replay on 2026-10-22 |
| Delivery gate | blocked for strategy execution; calendar plan and input admission are executable |
| Work item | P6-HISTORY-001; routing in [P6 CN02 work item](../../../work-items/p6-cn02-start.md) |
| Frozen plan | `config/forward-validation/runtime/p6-historical-replay-plan.v1.json` |
| Code and tests | `forward_validation/historical_admission.py`; `test_p6_historical_admission.py` |

## Purpose and authority

The current Owner direction is to advance P6 using existing historical data,
without waiting for new Daily products or 66 future trading days. This is a
separate historical successor. The accepted prospective contract, original
activation, ledger, failed Jobs and budget remain immutable and do not admit or
receive historical runs. The previously installed controller remains disabled.
Reconciliation with the old OCI writer and a new prospective activation are not
predecessors of an isolated historical run.

The purpose is to exercise the frozen candidate, expert/veto, next-open paper,
position/accounting and target-settlement chain. It does not authorize tuning,
training, P3/P5 research-result access, blind 2025 access, notification, a broker
or live capital. Dates are fixed before any result-bearing read. Known source
limitations remain truthful; a completed input scan is not a completed P6 replay.

## Fixed historical intervals

Use the exact matching SSE/SZSE open sessions from the already isolated 2026
P10 handoff, bound by calendar member hash and session-list digest:

| Purpose | Inclusive dates, Asia/Shanghai | Sessions |
| --- | --- | ---: |
| Shared warmup | 2026-01-05 through 2026-07-06 | 120 |
| Short smoke | 2026-07-07 through 2026-07-13 | 5 |
| Continuous decision interval | 2026-07-07 through 2026-09-14 | 50 |
| Report S1 | 2026-07-07 through 2026-07-29 | 17 |
| Report S2 | 2026-07-30 through 2026-08-21 | 17 |
| Report S3 | 2026-08-24 through 2026-09-14 | 16 |
| Target maturity tail | 2026-09-15 through 2026-10-20 | 20 |

The 190 available open sessions determine this bounded selection: preserve
120 sessions before the first decision and the existing 20-session target
horizon at the end. Fifty decisions are the remaining interval, not a claim
that the prospective 66-session budget has passed. The 61/22 feature/candidate
windows and 120-session listing-age rule still apply per security; calendar
warmup alone does not establish valid security history.

S1/S2/S3 are reporting views over one continuous run. Capital, holdings, pending
orders and unsettled targets cross their boundaries. Do not reset them, reuse
warmup as observations, claim that these adjacent slices are independent, or
choose dates after inspecting outcomes. The smoke's state is disposable and
does not seed the main run with a second exposure to results. Final replay
must use a fresh isolated ledger and reproduce the smoke prefix deterministically.
The target tail adds no new entries; retain unresolved positions/targets with
their actual terminal disposition rather than force a final sale or invent a label.

## Stage graph and present executable boundary

1. Bind exact source manifest, full physical inventory, calendar and frozen
   policy closure; freeze the above intervals and resource budget.
2. Verify member bytes once. Reuse that unchanged input binding across smoke
   and full-window input-admission diagnostics. A source mutation fails closed.
3. Use one bounded, metadata-only query for the necessary historical bar,
   identity and state clocks. Derive five-day smoke and all 70 decision/tail
   day reports from it. No price values or economic results are selected.
4. Admit a strategy replay only after P10's historical purpose, full temporal
   closure, feature windows, dated execution and relevant actions qualify.
   Existing later-snapshot context is not that grant. The actual historical
   strategy orchestrator is not delivered by the admission module.
5. Once admitted, share one immutable job-local history/feature intermediate
   across daily consumers, run the continuous decision/tail sequence and
   independently reproduce the complete result. No generic platform or new
   data acquisition is required by this plan.

Stages 4/5 are not performed merely because stages 1/2 pass. A zero-admitted-day
assessment has status `BLOCKED`, with `strategy_replay_executed=false`. Its
`ASSESSMENT_COMPLETE` marker seals the negative assessment, never a candidate
READY, replay acceptance or a fabricated no-trade strategy result.

The source's existing historical-purpose rejection is preserved. P10 owns a
qualified successor or a precise unavailable disposition under the existing
historical-PIT dependency #526; P6 must not recover archives or infer old
knowledge times locally. A historical outcome date can be in the past while
the data version was first known much later. Passing bar/identity/state clock
checks would still be only a necessary condition, not full source admission.

## Retention and checks

Input roots and existing handoffs remain read-only. Freeze a new unique job's
device/free-space, write/RSS/time limits before writes. Retain plan, code and
configuration identity, safe daily counts/timestamps, pure-text logs and input
unchanged evidence. Only the bounded DuckDB TMP is removed after the query.
Do not create full mirror copies for this metadata-only stage or modify the
running prospective database, deployment, activation or old services.

Tests cover two-venue dates, exact contiguous windows, missing/duplicate
sessions, close-date/time errors, slice overlap/gaps, shared warmup, immutable
source/policy drift, late/unknown/expired knowledge versions, zero-admission
failure and no prospective/strategy/order side effects. Run applicable P6
regressions and `make check`; report remaining source and execution gates.
