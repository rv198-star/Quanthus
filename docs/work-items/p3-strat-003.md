# P3-STRAT-003 — Multi-expert incremental value and recent-history validation

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | Owner continuation active; formal retained B succeeded with PARTIAL_INPUT_QUALIFICATION; B unaccepted; Daily adapter/PIT gaps #473/#474; A/C/C-R1 preserved; D NOT_RUN |
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

The Owner explicitly required continued attempts after PR #419 merged at `a64a3f3`.
Live #317/#412 were reopened. Current Owner instructions permit OCI as an existing data node
through NexusAgent; the #424-only SSH override is not used here.

The preregistered formal PostgreSQL/event-queue B Job
`job_5352d6c61af94a1aac4162a9a6a5d357` succeeded on 2026-10-03 at exact revision
`92a70a7bc11b2df2da16cd00dbacdbd4877d6b8b` (PR #469 merged). Its durable single attempt is spent.
Independent artifact verification passed, but the acceptance disposition is
`PARTIAL_INPUT_QUALIFICATION`: **B remains NO_PASS and D is not active**. The 6,407 candidates,
27-field exact equality, 60 warmup opens and 416 next-open pairs do not qualify deficient inputs.
Final manifest SHA is `4a6557aad4416d9f86c184c2f57552d444093fbb2e76f4db942e02906887fcce`.

Two concrete gaps are registered for unified governance. [#473](https://github.com/rv198-star/Quanthus/issues/473)
tracks Daily `price_basis='none'` versus the retained producer's `UNADJUSTED` filter: all 4,992
positions on each of the last 35 opens are invalid (174,720 total). LEFT JOIN placeholders explain
why missing_bar=0 does not establish coverage. This proves a consumer mismatch, not absent raw DWD.
[#474](https://github.com/rv198-star/Quanthus/issues/474) tracks missing per-family economic
availability/PIT qualification. Raw-known-late timestamps remain preserved; they are neither
automatic leakage proof nor permission to replace them with T-close.

The completed input-only `daily-adapter-probe.v1.json` freezes the exact
35 original packages and 70 bar/calendar files (846,352,085 bytes). One aggregate diagnostic per
node uses 33 OCI packages and two exact CN01 packages; it reports raw 1d coverage, basis/price
validity and raw-time counts. This is a new diagnostic closure under the Owner's continued
delegation, not a retry/rename of the spent retained request. It builds no corrected bundle or
candidate, opens no economics/labels, and cannot qualify B, activate D, close data issues or
mutate P6/formal data. Budget: 600 seconds per node, two DuckDB threads, 2 GB memory, 512 MiB TMP,
64 KiB output, one attempt per node. CN01's Aug04/Aug05 probe has completed: 9,984 `none`日线,
zero legacy matches/invalid prices, all raw timestamps late. OCI stopped before market rows at
Aug27 read permission, preserving the old marker. A separate frozen pre-read recovery binds the
failure and changes only that package's storage to private exact copies; one new OCI attempt,
unchanged bytes/budgets, no repeated CN01 and no further automatic recovery. Frozen PIT gates
still apply to any later corrected B proposal.

The separate OCI recovery completed at `5cf2fb1` in 2.246 application seconds: 164,736 raw 1d
rows, all `none`, zero legacy matches/invalid prices/unknown basis or time. Receipt SHA
`c9b50c3dccc6489f3f695018f5888fa5e3b2cec4ba5af98c1353622efe5ea070`.
Combining this with the existing CN01 success verifies exactly all 35 frozen dates, 4,992 raw rows
each, 70 files / 846,352,085 bytes: 174,720 valid-price rows, zero selected by the legacy adapter,
173 zero-activity rows. All raw knowledge timestamps are after T-close. Thus the entire diagnosed
suffix has raw bars; #473 is a consumer adaptation defect, not evidence of absent DWD daily bars.
Zero-activity rows retain their existing tradability gate; valid prices do not establish eligibility.
The combined result digest is `sha256:69cb5e1626990f044ef2fc163ad016c81a2213b2d1d4a5ce6cc568a335eb9cb8`.
Both successful attempts and the original pre-row failure are spent and preserved. No corrected
bundle or candidate was built. Current next action: qualify per-family economic availability from
existing bound source evidence before proposing a distinct corrected B; a price-basis alias alone
cannot fix #474 or activate D. See `../acceptance/p3-p5-input-recovery-20261003.md`.

The separately frozen `temporary-daily-adapter.v1.json` completed a temporary source adaptation
under the Owner's continuing delegation. It retains all raw values/types/times/provenance and the
raw `none` label, adding canonical `UNADJUSTED` only. Every output row remains explicitly
decision-unavailable pending #474. One new private, content-addressed materialization per node;
no old attempt reset, retained-bundle repair, candidate scan, economics, labels or formal data/P6
mutation. Sixteen new adapter contracts and the independent implementation review pass. Versioned
GitHub code was used after full check and preregistration; #473/#474 remain open.
Both actual materializations are now complete at `9d825e9`: 174,720 rows / 21 original columns,
zero typed differences, 10,043,032 private Parquet bytes, all decision-unavailable. Exact output
hashes/cache/receipt identities and the 35-date union pass independent local verification. Both
attempts are spent. Combined digest `sha256:62c2b95ecd440a82f99d0cec428b5415ea9a06c2e82452cb429ed3e8a84aae1f`.
This completes temporary basis adaptation only; #474's original publication/version evidence is
still needed before any distinct corrected B can run. No current P3/P5 research acceptance follows.

The exact retained candidate and accepted P4 A/B industry sources were recovered. A bounded
isolated CLI inspection at `835dea9b5502f9dbe1f0830b538312bc8f0f4b9e` completed in 38 seconds:
2,455,175 identity/date rows, 5,251 distinct securities, 5,249 in the decision window.
DWD covers 5,201; sealed P4 covers another 49 (47 in the decision window); zero source conflicts.
One decision-window security, 688287, lacks a company industry membership in both admitted sources.
Its absence also holds in the original 259-member supplement; no code-only substitution is used.
The valid sequential 300114 -> 302132 ticker change is represented by dated listing bindings.

The diagnostic manifest is `sha256:0afe3c451292e94bffa35c064585720ece936f2f858a897aa4dfbd1e8ef11123`;
result digest `sha256:3a1d4b28b64498f9a89af9d6247878a091f528ee83a04fc0c002a3ab829d9965`.
Artifact: OCI `/home/ubuntu/quanthus-research/p3-p5-20261002/diagnostic-v3/.staging/`
`p3-b-r1-resume-20261002/inspection-835dea9-private-base`.
This is an actual isolated diagnostic, not a formal platform B Job or B acceptance. Original source
permissions were preserved; only exact industry members were copied into private job-local inputs.
No candidate rebuild, strategy outcome, target label, P6 mutation or daily pipeline work occurred.

The Owner approved the frozen one-security proposal on 2026-10-03, then required data gaps to be
registered for unified repair by the separate data workstream. The separate activation receipt is
`config/research/p3-p5-temporary-source-activation.v1.json`; the original proposal stays sealed.
[Data issue #470](https://github.com/rv198-star/Quanthus/issues/470) tracks formal industry repair,
including the 49 existing supplemental dependencies and the one residual. Any new source here is
temporary, static non-PIT, private research evidence. It cannot modify Base/Daily/CURRENT or close
the data issue. A governed replacement needs an exact closure and comparison before temporary
sources exit new runs; preserve old audit receipts.

The approved single query executed at `3d2ac91c02de819ee1e998349d87da9fec02fb39` through Nexus
OCI session `session-b8e60ae67d4f9030de5fff08`, using an isolated BaoStock 0.9.3 dependency.
Disposition: `QUALIFIED_TEMPORARY_STATIC_SOURCE`; one query, zero retries, 1.439 application seconds.
Receipt SHA `b26177362b1da0f15a4f4612ffb4325e8bded039b1035443fed2c429176a428b`;
captured response SHA `6883ab4e46add9770c79f49cdaec258bd5a80912612ddfadbcb40edbc3a2abfd`.
Actual retrieval was `2026-10-02T17:55:56.775201+00:00` (2026-10-03 Asia/Shanghai), never backdated.
Source evidence lives in private OCI `p3-p5-20261002/p3-temporary-source/attempt`, not DWD.
The collector captured exact request, provider fields/rows and update date; server request-echo
attributes were not retained. That extra audit suggestion is not an approved qualification gate
and does not authorize a second query. The fixed attempt cannot be automatically reopened.

The isolated S2 static-industry composition ran at `782b37e0c67996d163b172f5b605b560caee01a9`
through Nexus OCI `session-e2238406ba64130c4fb872b7`. All 5,251 securities are covered:
5,201 DWD + 49 sealed P4 + 1 approved temporary source; missing/duplicate/conflict counts are zero.
The 5,249 decision-window denominator and dated code changes are preserved. Application time was
5.028 seconds; each identity inventory was built once and reused. Twelve synthetic tests and an
independent read-only implementation review passed. Execution verified the clean Git revision and
every production/config file against a host-generated code receipt; outputs are fixed to a private
owner-1001 root. No new network lookup, candidate rebuild/scoring, replay values or outcome access.

Receipt SHA `2981ae78e2afffe638c610ce0c6545f84b4c489350e8b03e982939c7a7dcda3a`;
mapping SHA `36dac5f6f941ddad05640a0f12d0f77555af8457d07a528e7b43d9a5af4b0280`;
identity/provenance SHA `b27484490a201e54a4d44b84fccd7eedb41396170c3b81faff67d510b40534f9`.
The three files total 3,573,178 bytes under private OCI
`p3-p5-20261002/p3-temporary-industry-closure/attempt`. They are temporary static non-PIT
concentration inputs, not a governed DWD repair. #470 remains open.

Historical S3 next action (now completed): finish the retained-input engineering checks and
versioned release gate, then submit one formal input-only B Job. The static mapping alone is not complete B input
qualification. #319 stays NO_PASS; B remains unaccepted and D is not active.

The retained failed-job replay input also still exists. Metadata SHA for `replay/bundle.json` is
`5d592983f9d04ca78e65bdcc48ed95c6a90c116106b4c5760187cae2e6f11b8e`; bundle identity is
`84f0e7dba9c5373e1b01f3c21ba254f6af4fadf8b11197f1e7226cf1d36b9a60`. All seven declared files
(245,777,504 bytes) match their recorded physical checksums, verified through OCI session
`session-a0435a2fc91ae14e0af6f20e`. This metadata/byte check did not deserialize market values,
evaluate strategy economics, rebuild a candidate or reproduce the original failed Job. It provides
a concrete retained-input option for the next bounded closure; no historical chain reconstruction
or formal B/D continuation is implied by these file checks.

The subsequent source investigation recovered the exact Base V2.1 predecessor chain without
rebuilding any package. OCI retains the suffix; CN01 retains the exact Aug05/Aug04/Aug03 prefix.
The actual target chain has 35 members through 2026-09-18, including only the already accepted
Aug27 semantic bridge. All declared files total 870,036,020 bytes and matched their checksums.
CN01 verification used Nexus and its existing passwordless sudo permission for fixed read-only
paths; no mode/credential/service was changed. The initial unprivileged permission failures were
resolved through that existing interface, not recorded as missing data. Old Base-v2 Aug04/Aug05
entries in the historical 35-row inventory are a different branch and were not spliced into V2.1.
External lineage receipt SHA `f270f66bacedc15de744a590841740247b394a3fd4164b8bf316f57c1435e9f7`;
CN01 byte-check session `session-0dd82cb246f4a583508d4b23`, OCI
`session-3050af1458f431228293fa46`. The target identity and sync timestamp match the retained bundle.
This is recovered-source evidence, not independent reproduction of the failed Job.

The existing EventWorker executes its deployed code and does not check out the Job's declared SHA.
It has no single-Job claim filter; an extra `--once` Worker is not a safe substitute. The formal S3
route therefore needs a narrowly governed retained-input entry/allowlist, engineering review, and
the existing event-worker-only versioned release gate before one new B Job. P6/replay-worker and
their environment must remain unchanged. A private CLI or private SQLite Job must not be described
as that formal B run. At that pre-release checkpoint no new formal B had been submitted.

The narrow entry is now implemented in `p3_retained_input.py` and `p3_retained_audit.py`, under
`config/research/p3-strat-003/retained-input.v1.json` (physical SHA
`b642509bd80bde69a5e765c5e8b7860e01b3643e33e576029e6788d343294ff4`). It freezes 52 exact files,
304,584,475 bytes: the retained bundle/candidate, the completed temporary industry mapping, the
source-lineage receipt, and authoritative Base/daily calendars plus adjustment factors. The
calendar suffix uses the recovered 35-member chain and only the already accepted Aug27 bridge.
Scope remains 60 pre-2025 common open sessions and the original decision/execution end dates.

The Job verifies all input bytes before any Parquet read, checks all 27 producer projection fields
with exact Decimal comparison, rejects identity/calendar/pair-set drift, and builds one shared
coverage intermediate for all ledgers and the replay report. It then scans the original projection
under the unchanged outcome-blind rule and seals an input artifact. Budgets are 1,800 wall seconds,
two DuckDB threads, 6 GB DuckDB memory, 8 GiB TMP and 4 GiB output. A durable external attempt
marker permits one attempt; sealed staging may be recovered for the same Job without rerunning
the scan. The parent Worker enforces the wall deadline even during a blocked native query.

Thirty synthetic tests pass, including complete build/seal/recovery, exact projection drift,
missing whole-calendar days, overlapping availability reasons, map substitution after resealing,
and parent timeout. Two independent read-only reviews passed; an independent synthetic comparison
matched the existing replay-reader audit. These are engineering results only. Actual market-row
qualification and candidate scanning were unexecuted at that engineering checkpoint. They have
since run as recorded above; formal B acceptance and D remain unaccepted/unexecuted.
The existing event-worker-only release route requires a clean exact GitHub revision reachable from
main; naming a new SHA in a Job cannot substitute for deploying that version.

### Historical bounded closeout — 2026-10-02

The 2026-10-02 continuation completed the permitted early failure closeout under #317/#322.
Outcome: `invalid_execution_evidence`; hypothesis support remains `inconclusive`.
Bounded recovery disposition `DIAGNOSTIC_INPUT_UNAVAILABLE` describes the actual authorized
metadata investigation, not a formal diagnostic Job. #319 remains NO_PASS and B unaccepted;
S2/S3/new formal B/D/cold reproduction are NOT_RUN. See
`../acceptance/p3-p5-bounded-closeout-20261002.md` and
`config/research/p3-p5-closeout-20261002.v1.json`.

Historical next action (superseded): none within that closed generation. #412 and #321 were closed with explicit
stop/NOT_RUN reasons before #322's independent early review; #322 and #317 are now closed.
No reconstruction or new source campaign starts automatically. Reopening needs a new bounded input proposal.

### Historical first recovery checkpoint — 2026-10-02

Before the bounded closeout continuation:

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

Historical next action (superseded): obtain the exact location of an already retained candidate and sealed sources on
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
