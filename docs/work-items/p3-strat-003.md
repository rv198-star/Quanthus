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

Last verified on 2026-09-22 during the bounded S1 source-repair closeout:

- B / #319 remains closed with `NO_PASS_STATIC_INDUSTRY_INPUT_COVERAGE`, **not accepted**.
  Its negative evidence is immutable and is not reopened or rewritten as PASS.
- `P3-STRAT-003-B-R1` / #412 is the only active B continuation. It is a bounded input-source
  correction and coverage diagnosis, not a new historical platform or a reset of B.
- PR #413 is merged at `cb5065dfb6d5f38d2ebf3c2a8d7b7c1d4cc56635`; PR #414 is also merged
  and makes NexusAgent-only routing project governance. The frozen inspection request remains
  byte-identical, with digest
  `sha256:3262e61bf8cfab5e58f049a5215dedce319ec71c6d2ec2a6855e43562364d57c`.
- [Draft PR #420](https://github.com/rv198-star/Quanthus/pull/420), branch
  `issue-412-s1-source-closure-repair`, preserves source-repair commit
  `fb1ea787e1dd38b9a4d10c1bb8c5ffdb097267cf` on parent
  `8bd989dfae98acd4f28799a376ee0ca5df9162aa`. It verifies the DWD manifest/READY and two
  industry-file bindings, retains independent all/decision-period DWD/P4 coverage and reason
  counts, and rejects exact-security P4 matches with conflicting listing-code/venue identity.
  The diagnostic module SHA is
  `sha256:01d96c46d2e4deb1594887054634d1b324f3b91c26e2a989771fbe27c48dfd1d`.
  Event Worker changes are formatting only; its AST is identical to the `8bd989df` base.
- The authorized merge of current main
  `98ae4a0518968bd5e071275df5b0a417f6a472e5` into that branch produced integration commit
  `4f38ee11f03b5f50d7ea4cf2352a600d8a7d90ec`. Relative to `fb1ea787`, it adds only
  `lane_runner.py`, `minute_tdx.py`, and `test_ods_providers.py` changes; all S1 repair and
  frozen-config file hashes remain unchanged. PR #420 is still a draft and has **not** merged
  into main.
- Source commit `fb1ea787`: Ruff check/format and syntax passed; focused tests returned
  **131 passed, 0 failed, 0 skipped** (exit 0, 4.473 seconds); `make check` returned
  **2799 passed, 0 failed, 0 errors, 7 skipped** (exit 0, 270.846 seconds).
  Integration commit `4f38ee11`: its one additional `make check` returned
  **2801 passed, 0 failed, 0 errors, 7 skipped**
  (exit 0, 266.771 seconds). Full-suite evidence binds these code revisions; the subsequent
  checkpoint-only documentation commit does not represent another full-suite run.
- Both full-suite runs skipped five PostgreSQL tests because `QUANTHUS_TEST_POSTGRES_DSN`
  is not configured (acceptance, candidate runs, event shards, expert-evaluation runs, and worker
  queue), plus two tests because production expert artifacts are not mounted
  (`test_expert_mvp_acceptance.py` and `test_product_expert_evidence.py`).
  Original commands, logs, JUnit counts, file hashes and AST receipts are retained under
  `/srv/agentdock/experiments/quanthus-p3-strat-003-s1-source-closure-20260922/`;
  the independent integration evidence is in its `integration-98ae4a05/` subdirectory.
- **No actual S1 industry-inspection Job has been submitted in this closeout; no sealed S1
  diagnostic result exists.** The repair has not been deployed. The inspection may read only
  the retained failed-B candidate identity/date projection and sealed industry-source evidence.
  The historical `10259` missing candidate-event occurrences remain separate from distinct-
  security counts. Synthetic repair tests confer no runtime, strategy-result, outcome-label,
  candidate-rebuild, source-collection, P6 mutation, or B-acceptance authority.
- If the failed candidate projection or sealed source evidence is unavailable, the valid outcome
  is `DIAGNOSTIC_INPUT_UNAVAILABLE`; do not reconstruct the projection or acquire a replacement.
  The original corrected formal Job `job_313b915e82fd4865b32c1222c5b094f2` remains the latest
  formal B-v2 execution, failed with `p3_strat_003_industry_mapping_incomplete: 10259`; do not rerun it.
  PR #409/#410, the accepted 2026-08-27 semantic probe, A/C/C-R1 and their digests remain preserved.
- D / #321 and E / #322 remain blocked. There is no accepted B input/candidate digest and no
  complete A+B+C batch binding.
- One broad status response in a prior execution context prevents certification that the
  preceding access history remained result-blind throughout. No historical result values are
  reproduced in this checkpoint. Any later runtime execution requires a fresh context with
  strict allowlisted field filtering before responses are displayed or persisted.
- **NexusAgent is the only current remote execution/control channel. OCI is retired.** Historical
  OCI references are archival evidence only; do not restore or use OCI as a fallback.

Resume by verifying live #412 and PR #420 and completing the draft review. Before any deployment
or diagnostic execution, explicitly approve the reviewed replacement code revision in place of
the originally requested `f4568f` revision. Then bind the approved merged revision and frozen
request to an event-worker-only deployment and exactly one industry-inspection Job through the
authorized execution channel. This checkpoint records the draft-PR closeout; it does not grant
merge, deployment or Job authority. Interpret the sealed S1 disposition before deciding whether
B-R1 may proceed to a source-binding closure.

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
