# P3-STRAT-003 — Multi-expert incremental value and recent-history validation

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked; A, C and C-R1 accepted; B / #319 closed no-pass and unaccepted; B-R1 / #412 is the only active B continuation; D blocked by B acceptance; E blocked by D |
| Parent | post-MVP Expert Strategy Layer research increment |
| Governing design | `../design/lld/04-experts-aggregator/expert-strategy-layer.md` |
| Frozen request | `../../config/research/p3-strat-003/request.v1.json` |
| GitHub milestone | `P3-STRAT-003 | 多专家策略增量价值与近期历史验证` / #11 |
| Parent issue | #317 |
| P6 relationship | parallel; existing P6-v1 is not a predecessor and must not be mutated |

## Goal

Determine whether the existing Trend and Position Risk strategy effects add economically useful
information relative to a non-empty deterministic P4 candidate baseline under the same data,
guardians, capital, matching, cost and base-position rules.

This milestone is not a new backtest platform, a third-expert programme, an aggregator search, or a
replacement for P6. P6-v1 continues to collect its already frozen prospective evidence independently.

## Why this is new work

The accepted `P3-ATTR-001` comparison removed the Trend policy effect by abstaining. Because Trend
is the only entry scorer in `candidate.p4-trend-risk@1.0.1`, that legal fallback blocks all new
entries. It proves the effect of the declared fallback, but it is not the same question as comparing
the expert strategy with a non-empty simple candidate strategy.

P3-STRAT-003 therefore introduces one explicit research control,
`control.p4-candidate-pass-through@1.0.0`, which emits a deterministic positive entry score for an
otherwise eligible P4 candidate while still traversing the accepted aggregator, system guardians,
capital allocator and P2 execution path. It does not bypass missing-data or tradability checks.

## Frozen factorial

The request freezes exactly two experimental factors and four variants:

| Variant | Trend effect | Position Risk effect | Entry scorer |
| --- | --- | --- | --- |
| B | off | off | deterministic P4 candidate pass-through control |
| T | exact v1 | off | existing Trend mapping |
| R | off | exact v1 | deterministic P4 candidate pass-through control |
| TR | exact v1 | exact v1 | existing Trend mapping |

The headline contrast is `TR-B`. `T-B`, `R-B`, `TR-R`, `TR-T` and the factorial
interaction `TR-T-R+B` are diagnostic only. No variant, threshold, candidate rule, weighting or
exit rule may be selected after viewing results.

## Data authority

The result-bearing windows are frozen before any P3-STRAT-003 result read:

- 2025-01-01 through 2025-12-31 is authorized once for this bounded major-strategy validation.
  Its previous permanent-blind role is consumed when D first reads result-bearing evidence; after
  that read it may not be described as untouched for successor research.
- 2026-01-01 through 2026-09-18 is the fixed recent-history window. The parent issue was created at
  2026-09-19T16:35:56Z, after the last complete Friday session; the study does not roll forward
  with CURRENT.
- General 2022-2024 result access remains closed. B may resolve and read only the exact 60 exchange
  sessions immediately before 2025-01-01 that are required as warm-up. Those rows are input history,
  not a 2022-2024 strategy-result sample.
- A reads no 2025/2026 strategy result. B qualifies inputs only. C uses synthetic or already
  consumed golden evidence. D is the first result-bearing gate.

Open positions at the final admitted close are marked to market without forced liquidation.
Unmatured targets remain pending and are excluded from settled expert-quality denominators. D does
not wait for future sessions solely to make the end of the historical window look mature.

### B pre-result reconstruction correction

B remains an input-only gate. The failed exact-manifest recovery attempts are retained as evidence
and are not reclassified as accepted input. R9 proved that the recovery path resolved a historical
builder revision from the original package id but still executed the current daily-domain builder,
so matching the old package id did not reproduce the old manifest bytes. That mechanism is no
longer an admissible route to B acceptance.

The replacement path separates two identities:

- **original historical package identity** remains immutable evidence and is never rewritten or
  claimed to have been reproduced when its bytes are unavailable;
- **research reconstruction identity** is a new, result-blind materialization from exact immutable
  ODS/Base inputs under a frozen reconstruction implementation.

Before a research reconstruction may feed B, an outcome-blind equivalence probe must pass over the
exact daily payload consumed by the existing recent-history effective-view builder:
`dwd_security_bar`, `dwd_tradability_state`, and `dwd_trading_calendar`. Existing original packages
on both sides of the known builder-change boundary are overlap sentinels: the reconstructed and
original consumer payload file checksums, sizes, and row counts must match exactly. Manifest,
package-id, and audit-only byte equality are not substitutes for that payload comparison and are
not required when the consumer payload is exact.

The missing 2026-08-27 boundary is admitted only if its child-bound original package identity,
current ODS catalog prefix, and the frozen audit-only compatibility precondition all agree. The
compatibility precondition is limited to the known `f8f8330` -> `9ca9135` daily-domain change and
requires the `financial_disclosures_delta` artifact in the actually selected CN01 release to have
exactly zero rows. The selected CN01 release and artifact bytes remain self-verified through
READY/manifest/size/SHA checks and the observed artifact SHA is recorded as evidence, but a physical
SHA copied from another source such as Hermes is not a cross-source prerequisite. This is a bounded
compatibility proof, not a generic historical-Git executor.

If either overlap sentinel differs at the consumer-payload level, if the missing-boundary source
precondition differs, or if proving equivalence requires materially broader historical-execution
infrastructure, B closes as insufficient admissible historical input evidence. The window must not
be shortened and no D result may be read to repair the input path.

After that probe passes, corrected B may reuse the sealed 2026-08-27 research-reconstruction package
inside the accepted probe artifact rather than rebuilding it. B must reverify that package, its
manifest and its consumer-payload digest against the probe receipt. The original 2026-08-28 child
still retains its historical prior package id/manifest SHA; B records the resulting 2026-08-27 to
2026-08-28 lineage difference as one explicit semantic bridge. It must not rewrite the child,
rewrite CURRENT, or claim the old 2026-08-27 manifest was reproduced. Every other daily-chain edge
continues to require the existing exact binding.

### Current recovery checkpoint

Current recovery on 2026-10-02:

- owner delegated P3/P5 execution and convergence within the existing frozen contracts;
- live #412 remains open/blocked; #319 remains NO_PASS and unaccepted, D/E remain blocked;
- NexusAgent is available. CN01's Base at
  `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1` has the exact frozen manifest and READY
  (`sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`);
- the retained failed candidate has not been located on permitted reachable nodes. CN01 lacks both
  known Event volume names; Hermes volume metadata is permission-denied. Host path absence alone
  is not proof that an uninspected container volume or another migration copy is absent;
- no market rows, candidate rebuild, formal diagnostic Job, B rerun, or D result read occurred;
- recovery code now seals corrupt/unreadable retained candidate input as unavailable, verifies
  frozen DWD manifest/READY/selected industry payloads before scanning, and repairs B-R1 planning
  ownership. These engineering changes do not constitute runtime B-R1 acceptance;
- current user instructions explicitly retire OCI. Later conflicting repository prose is not
  authority to access that node in this recovery.

Unique next action: obtain the exact location of an already retained candidate and sealed sources on
a permitted NexusAgent node. If no such input survives, record the diagnostic-unavailable stop with
its actual observation limits; do not reconstruct it. Do not submit a placeholder Job on a new empty
node to manufacture an unavailable receipt. See
`../acceptance/p3-p5-convergence-recovery-20261002.md` for this recovery's checks and limitations.

### Historical B-R1 checkpoint — 2026-09-22

Last verified after opening the bounded B-R1 successor:

- B / #319 remains closed with `NO_PASS_STATIC_INDUSTRY_INPUT_COVERAGE`, **not accepted**.
  Its negative evidence is immutable and is not reopened or rewritten as PASS.
- `P3-STRAT-003-B-R1` / #412 is the only active B continuation. It is a bounded input-source
  correction and coverage diagnosis, not a new historical platform or a reset of B.
- PR #413 is merged. The result-blind industry-coverage inspection is published on main at
  `cb5065dfb6d5f38d2ebf3c2a8d7b7c1d4cc56635`. PR #414 is also merged and makes NexusAgent-only
  routing project governance. The frozen inspection request digest is
  `sha256:3262e61bf8cfab5e58f049a5215dedce319ec71c6d2ec2a6855e43562364d57c`.
- The inspection is allowed to read only the retained failed-B candidate identity/date columns and
  sealed industry-source evidence. It must keep the historical `10259` missing candidate-event
  occurrences separate from distinct-security counts, and it carries no strategy-result, outcome-
  label, P6 mutation, candidate rebuild, network collection, or B-acceptance authority.
- If the failed candidate projection or sealed source evidence is unavailable, the valid outcome is
  `DIAGNOSTIC_INPUT_UNAVAILABLE`; do not reconstruct the candidate projection or silently acquire
  a replacement industry source.
- The original corrected formal Job
  `job_313b915e82fd4865b32c1222c5b094f2` remains the latest formal B-v2 execution and failed with
  `p3_strat_003_industry_mapping_incomplete: 10259`. Do not rerun it.
- PR #409/#410's exact-package diagnosis and repair remain accepted history; the successful
  2026-08-27 semantic probe, A/C/C-R1, and all their existing digests remain preserved.
- D / #321 and E / #322 remain blocked. There is no accepted B input/candidate digest and no
  complete A+B+C batch binding.
- **NexusAgent is the only current remote execution/control channel. OCI is retired.** Historical
  OCI references are archival evidence only; do not restore or use OCI as a fallback.
- At this checkpoint the current conversation did not expose NexusAgent, so no project Ruff/pytest,
  `make check`, deployment, diagnostic Job, market-data read, or B-R1 runtime acceptance had been
  executed. That limitation is about the conversation's execution channel, not evidence that
  Quanthus or its nodes are offline.

Resume by verifying live #412 and current main. When NexusAgent is available, run the project checks
on main `cb5065dfb6d5f38d2ebf3c2a8d7b7c1d4cc56635`; only if they pass may the Event Worker be
deployed through NexusAgent and exactly one frozen industry-inspection Job be submitted. Interpret
its sealed disposition before deciding whether B-R1 may proceed to a source-binding closure.

Do not repeat A/C/C-R1, #409/#410, the semantic probe, or the failed formal B job. Do not replace the
frozen industry SHA in place, drop unmapped candidates, lower the coverage gate, shrink the window,
read D results, mutate P6, or revive retired OCI infrastructure.

This checkpoint is operational status, not authority. Frozen config, immutable artifacts and live
Git/GitHub state take precedence if it becomes stale.

## Evidence and decision rules

The headline claim has two separate support surfaces rather than one universal score:

1. return increment: `TR-B` return delta is at least +1 percentage point, its paired 22-session
   block-bootstrap 95% interval is strictly above zero, and drawdown is not worse by more than
   1 percentage point;
2. drawdown increment: `TR-B` maximum-drawdown delta improves by at least 1 percentage point, its
   95% interval is strictly below zero, and return is not worse by more than 1 percentage point.

The same one-percentage-point return/drawdown materiality and two-percent action-change thresholds
reuse the accepted formal-attribution scale; they are frozen here before recent-history result
access. The 22-session block bootstrap uses 2,000 deterministic resamples and a 95% interval. A
22-session block is a dependence-aware resampling unit, not a claim of real-world independence.

If neither support rule is met, the result is honestly inconclusive or economically negligible.
Material harm, negative return, low activity or no supported increment are valid research outcomes
and do not authorize threshold search.

## Child sequence

| Work item | Purpose | Dependency | Result-bearing |
| --- | --- | --- | --- |
| P3-STRAT-003-A / #318 | freeze request, authority, comparisons, metrics and budget | none | no |
| P3-STRAT-003-B / #319 | qualify immutable recent-history inputs and isolation | A | no strategy result |
| P3-STRAT-003-B-R1 / #412 | bounded static-industry source diagnosis and corrected B input closure | B no-pass evidence + A | no strategy result |
| P3-STRAT-003-C / #320 | implement the four-variant research control and exact runner | A | synthetic / consumed only |
| P3-STRAT-003-C-R1 / #385 | bind recent-history D to B's outcome-blind candidate manifest instead of a legacy Event+Label outer Run | C | no |
| P3-STRAT-003-D / #321 | run primary and independent cold-reproduction batches | B + C-R1 | yes |
| P3-STRAT-003-E / #322 | read-only evidence review and one bounded successor decision | D | review only |

A closes the design gate. B and C then proceed in parallel. B's input review exposed one legacy population-binding seam, so C-R1 closes that result-blind integration boundary. D may start only when its exact request, B input/candidate manifest and C-R1 code revision are all sealed.

## Resource and reproduction budget

D receives exactly two result-bearing composite batches: one primary batch and one independent cold
reproduction. Each batch runs B/T/R/TR under one Worker at concurrency one and builds the expensive
provider prerequisite once for all four variants. The cold reproduction may not reuse the primary
batch's intermediate cache.

The proactive working-set ceiling remains 14 GiB, hard container ceiling 16 GiB, and the frozen
batch wall-clock ceiling is 60 minutes. One pre-result transport/environment recovery per batch is
permitted only with unchanged semantic inputs. A visible result ends that retry authority.

## Stop conditions

Stop the current request rather than repair it in place if any of these occur:

- the request or bound source digests drift;
- result-bearing data are read before A accepts the request;
- non-warmup 2022-2024 result data are opened;
- 2025 is accessed outside the declared D batch;
- the 2026 window rolls beyond 2026-09-18;
- original candidate or expert thresholds are searched;
- shared guardian or P2 semantics change;
- P6 Worker, ledger, budget or mutable data pointer is changed;
- cash/position reconciliation or PIT validity breaks;
- cold reproduction reuses primary-batch intermediates.

## Acceptance boundary

P3-STRAT-003 may close with supported increment, mixed trade-off, material harm, no support,
inconclusive evidence, or invalid execution evidence. Only the last case means the experiment itself
failed to produce admissible evidence.

No result automatically authorizes a third expert, P5, P7, broker access or live capital. E may
select at most one bounded successor research question and must preserve all already-published P6
and historical evidence.
