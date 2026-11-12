# R5：固定动量响应的日历时钟接缝

| Field | Value |
| --- | --- |
| Parent | [Exploration governance](exploration-governance.md), research Epic #608, R5 milestone 19 |
| Design status | draft — independent prospective design review pending |
| Delivery gate | open |
| Selection | `META-RESEARCH-R5-SELECT-001` / #640 |
| Execution | `P3-RESEARCH-R5-CLOCK-001` / #642 |
| Request | `config/research/research-r5/request.v1.json` |
| Runtime | existing guarded `ops/bin/hf-dwd-research`; one narrow analysis dispatch |
| Independent construction | separate pandas/numpy script, without producer SQL or aggregation imports |

## Decision and minimum information delta

R2 established a 6.4101bp next-session intraday raw-price increment in its original
population. R4 completed the fixed single-position capital comparison and parked that
use under its original annual/concentration gates. The opportunity-population response
over the next two opens has not been measured. The T+1 rule alone cannot determine
whether overnight prices cancel, preserve or amplify the intraday difference.

The consumer is the #608 research allocator and its prospective signal-screen protocol.
The selected question is: holding the original R2 screen and band fixed, does its
increment remain over T+1 open to T+2 open at the population level? The outcome changes
whether this exact raw-band screen branch remains in the resource pool or the next
selection budget moves to an independent question. R4 remains parked in every branch.

The three-candidate selection compared this narrow diagnostic with historical cycle
coverage and another existing-asset consumer slice. Historical cycles have a qualified
product but no current named period-choice decision; the existing-asset slice has no
new observed demand beyond delivered/NO_GO cases. This diagnostic has the smallest
verified input and execution delta. Selection consumes code, metadata and prior reports.

## Input, population and clocks

- Same fixed Base 2.5.1 HF revision and 56-member closure as R2. The exact identities,
  sizes and dependency lock are in the request; all bytes are checked before use.
- Latest-sealed retrospective use; original 2020–2021 exposure and costs remain.
  The market read limit stays 2021-12-31. Calendar metadata begins in 2019 only to
  establish listing age. No 2022+ market payload is accessed.
- Decision interval is prospectively 2020-03-02 through 2021-12-29. The original
  2021-12-30 last decision is right-censored because a second following open would
  exceed the read boundary. Do not roll the boundary forward to complete it.
  Its original base-screen candidate/control rows remain in a separately reasoned
  audit denominator as `right_censored_second_session`, by decision year and group;
  they supply no response or paired date to the primary comparison.
- Reuse all original R2 base filters, closed momentum band [0.02,0.12], disjoint
  outside-band control and first-open price/status guard. No threshold, ranking,
  holding-period grid or capital path changes.
- Resolve both following sessions using the authoritative calendar for the same
  venue. Join by listing/session, retain security identity and [from,to) validity.
  Never use the next observed bar as a substitute for a missing scheduled session.
- One common cohort requires valid first-open identity/guard, positive finite
  first open and close, and valid positive finite second open. All three price
  intervals and the interaction use precisely this cohort.
- Ordered exclusion reasons retain the original first-open guard reason, invalid
  first close, missing second identity, invalid second open and nonfinite response.
  Count reconciliation includes valid unpaired cells/dates and invalid rows.

The labels describe unadjusted calendar-aligned prices. Second-open sell execution,
company-action economic normalization and fees are outside this diagnostic. These
labels supply a screen-resource decision, with separate gates for an economic use.

## Metrics and fixed branches

For an opportunity with first open O1, first close C1 and second open O2:

- primary `open_to_next_open = O2/O1 - 1`;
- explanatory `intraday = C1/O1 - 1` and `overnight = O2/C1 - 1`;
- `interaction = intraday * overnight`;
- the per-row and aggregated identity is primary = intraday + overnight + interaction.

Equal-weight listings within each date/venue/group, difference candidate and control,
then equal-weight the two venues and paired dates. Require both groups in both exact
venues. Each year needs at least 200 paired days; group opportunity, listing and
concentration gates remain those frozen in the request. Missing or malformed inputs
stop the affected run; an insufficient valid sample uses the insufficient branch.

Only the primary label determines the branch. Use the R2-shaped 20-session circular
moving-block interval, stratified by year, 2,000 replicates, fixed seed 20261112 and
nearest-rank 95% bounds. Secondary components explain the response and do not become
alternative winning labels. The original R2 statistic retains its old population;
the common-cohort intraday result is reported separately.

| Branch | Condition | Actual resource action |
| --- | --- | --- |
| insufficient | any frozen sample/concentration gate fails | inconclusive; park this calendar-aligned screen and retain the exact shortfall |
| advance | primary mean >=5bp, each year >0, interval lower >0 | retain distribution-level evidence; a separately named consumer is needed for any further tranche |
| below_minimum | primary interval upper <5bp | not_met for this screen; park it and prioritize an independent question in the next selection |
| uncertain | other valid results | inconclusive; park it and require material new evidence before reopening |

The prospective 5bp threshold is a fixed minimum information difference for this
resource-routing use, carried from R2 to avoid result-led threshold selection.
No branch restarts the parked R4 capital use, changes a strategy or grants training,
protected-partition or production authority.

## Execution, budget and recovery

1. Register #640 and the execution issue/module contribution; recover current related
   conclusions and preflight versioned proposals. Self-review and independent design
   review precede result-bearing execution.
2. Implement the narrow analysis and semantic negative cases. Check source identity,
   common-cohort missingness, two session links, lifetime boundaries, finite values,
   decomposition and resource/failed-stage publication guards.
3. Fetch the fixed closure into one isolated CN02 task root, reusing the verified R2
   source as a cache. Run one formal calculation and one independent construction.
   Independent inputs, feature/cohort joins, summaries and decision are rebuilt;
   producer SQL/summary functions are not imported by the reference.
4. Independently compare counts, dates, groups, branches and primary/component
   summaries. Required integer/identity agreement is exact; float absolute tolerance
   is 1e-10. Keep independent-failure evidence and one identified bounded recovery.
   The reference checks its clean tracked Git tree and records the three reference
   scripts, request and full 25-package lock hashes. Its parent guard checks final
   wall time, scratch and child-reported peak RSS after exit as well as live samples;
   exit zero with an overrun remains failed acceptance.
5. Publish one immutable named result package to the existing private HF results
   repository and read every file back at its fixed commit. Retain decision-worthy
   evidence on CN02. Append conclusions/blueprint checkpoints, update issues and the
   parent value brief, then finish reference/cache/worktree disposition.

The selected complete tranche is planned at 1–2 active hours, including implementation,
focused validation, independent review and handoff; full-regression waits are measured
separately. The estimate is reviewed if a material seam exceeds it. No general
multi-horizon platform, new Worker rollout or source-quality repair is included.
Worker limits remain 2 threads, 3GiB DuckDB, 6GiB RSS, 2GiB scratch, <=512MiB input
download, <=4MiB result upload, 180s screen and 1,800s job wall time. All attempts
and inherited unknown costs remain cumulative. Publish can retry up to three times
with the same package. A calculation fault gets at most one identified recovery
after a TPlan decision; a semantic change requires a new prospective request.

Missing shared data is routed to P10 with a precise consumption gap. On invalid
identity, out-of-scope dates or resource excess, stop the affected stage and retain
the receipt. Preserve completed work rather than replaying unrelated stages.
