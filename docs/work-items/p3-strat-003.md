# P3-STRAT-003 — Multi-expert incremental value and recent-history validation

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | Business continuation only; data work transferred to P10; temporarily defer B acceptance/D results; PARTIAL_INPUT_QUALIFICATION retained; A/C/C-R1 preserved |
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

**Single-successor Stage A completed, 2026-10-24 (latest):** the Owner-authorized
three steps are complete: the separate TR/TCH executor and exact admissions,
two-role replay with independent accounting/isolation acceptance, and the fixed
comparison. All four raw replay digests were recomputed; both roles' six business
artifacts agree under exact generated-ID/source-snapshot mappings. Each TR first
reproduced its complete retained anchor before TCH. Independent cash-flow checks
reconciled 118 position cycles and 832 portfolio closes, and 112 original input
files remained unchanged. Cold native old-source/index/primary-TMP reads and
network access were denied. Previously independently constructed own-role inputs
were reused under exact provenance; features were not rebuilt or claimed new.

The fixed TCH−TR net-return difference is +5.751824 percentage points and the
maximum-drawdown difference is −0.301555 points. Net returns are −1.367046% for
TR and +4.384778% for TCH; maximum drawdowns are 3.242351% and 2.940796%.
The descriptive unadjusted 95% net-return interval is [−6.262400, +16.166721]
points and crosses zero; the drawdown interval is [−2.870481, +6.156359] points.
The predeclared resource screen passes, which can only prioritize a separately
authorized fresh pilot. No successor strategy is accepted, and no pilot starts.
The 416-session point estimates retain all dates; the last 20 dates are excluded
only from incomplete-block resampling. The common mask retains 4,326 eligible and
3,690 excluded candidates, with overlapping `ENTRY_SUPPORT_UNAVAILABLE` and
`FEATURE_NOT_READY` reasons disclosed separately from in-replay missingness.

Focused tests passed 111 cases twice; targeted lint/format/type and standalone
planning checks passed. Full `make check` reports 3,831 passed / 20 skipped /
28 failed, the exact same DuckDB/BaoStock frozen-dependency failures as baseline;
there is no global-green claim. One pre-result environment recovery repaired only
supervisor cleanup/stat races, preserving empty startup directories and evidence.
Post-result validation fixes corrected an array loader and the TCH action-ID map
against already frozen qualified sources; no replay, financial value, input or
policy changed. Detailed evidence, plain logs, capacity receipts and local-only
commit/diffs are in `/srv/agentdock/.codex/p3-risk-entry-veto-20261024T061738Z-34d19dc/`.
The Owner will arrange Astra Max review. Strict B/D, the old five-cell experiment,
P6, production and the 4,173 unavailable visibility semantics remain unchanged.

**Single-successor Stage A activated, 2026-10-24 (prior):** the Owner explicitly
instructed “那就开始启动，完成1-3，完成后我会再让Astra Max做复审”. This supersedes the
proposal's then-unstarted execution status only for the exact exposed-window Stage A.
`risk-entry-veto.v1.json`, its separate card/mapping and exact execution registry
own the new TR/TCH namespace. The sealed five-cell request/registry, strict B/D,
P6 and production remain unchanged. Implementation, two-role replay and fixed
analysis/delivery are the three current tasks. Each role may physically reuse only
its own previously independently constructed preparation and session index under
fresh complete hashes and an unchanged-producer proof; this is bounded experiment
reuse, not a general cache or a claim of newly recomputed features. No fresh pilot
or source collection is authorized. Current job:
`/srv/agentdock/.codex/p3-risk-entry-veto-20261024T061738Z-34d19dc`.


**Successor review step 3 completed, 2026-10-24 Asia/Shanghai (prior):**
the Owner authorized Sol 6.1 Max first, with Astra only if the result is inadequate.
The bounded decision proposes TCH (entry heuristic veto off, 20% unit cap and full
held risk on) against TR, without selecting an accepted strategy or running new results.
The versioned `risk-successor-proposal.v1.json` under the research config directory
records the exact source bindings, business screen, two finite stages and denied
execution authority. The [governing proposal](../design/lld/04-experts-aggregator/expert-strategy-layer/first-formal-topology.md#single-successor-proposal-2026-10-24)
owns details. Only one unmeasured conditional effect is proposed; no new cells are
added to step 2 and no threshold is tuned. Contract checks explicitly reject using
this proposal or TCH in the sealed five-cell runner.

The existing TR−B supported-increment rule remains unmet. The higher observed T
return does not qualify a replacement; the two held-policy contrasts have opposite
signs and are conditional on entry rules. Stage A would be an exposed-window
mechanism/engineering experiment in a separate namespace, not fresh confirmation.
Stage B would be a separately activated, PIT-qualified 66-session descriptive
feasibility pilot. No fresh interval is currently qualified; later dates alone do
not prove freshness. This step finishes the decision/plan and does not wait for that
future pilot, rewrite original strict B/D or mutate P6/production.

Current work/evidence root:
`/srv/agentdock/.codex/p3-successor-step3-20261024T050704Z-08bbd44/`.
All three decision/diagnosis steps are complete. A separate Sol 6.1 Max read-only
review passed the exact proposal; a finite clarification added file-count/inode
headroom to both future write-before gates. No Astra fallback was needed. Neither
future stage is admitted, and no result-bearing successor execution has started.
The four new contract cases passed independently and the focused combined checks
passed 58 cases. Full repository checks and their unchanged-baseline comparison
are recorded in this job's `checks.json`; no global-green claim is made.

**Fixed component experiment step 2 completed, 2026-10-24 Asia/Shanghai (prior):**
the Owner authorized step 2 after completing step 1. The frozen five-cell design
and separate admission registry are `risk-components.v1.json` and
`risk-components-execution.v1.json` under the same research config directory.
The governing [formal topology](../design/lld/04-experts-aggregator/expert-strategy-layer/first-formal-topology.md#fixed-component-diagnosis-2026-10-24)
defines the exact effects and claim limits. All five cells completed in both the
primary and independent cold processes. Ten replay digests were recomputed, all
business outputs and recorded decisions matched under exact identity mappings,
72 original input files remained unchanged, and a separate cash-flow implementation
reconciled 320 position cycles and 2,080 portfolio closes. The cold process denied
native old-source reads and network access. Each role reused only its own independently
constructed prerequisite and verified checkpoint, not the other role's materialization.
Step 1 remains complete; step 3 remains NOT_STARTED. This is a separate retrospective
component diagnosis and never replaces the original frozen B/T/R/TR request.

The fixed ordered return difference TR−T is −13.840258 percentage points:
TC−T = −6.855502, TE−TC = −9.886442, and TR−TE = +2.901686. Held management
under original entry instead gives TH−T = −12.694886 points. These conditional
effects cannot assign fixed responsibility to a component or select a successor
strategy. The five-cell return/drawdown comparison and six descriptive intervals
are in `recovery-attempt-01/evidence/component-analysis.safe.json` under
`/srv/agentdock/.codex/p3-risk-components-step2-20261023T205243Z-a88b88d/`;
acceptance is `component-acceptance.safe.json` beside it. The actual information
cutoff remains 2026-10-23T11:21:45.250143+00:00 and the window is already exposed.
Separate arithmetic and read-only independent review passed; intervals are not
multiple-comparison adjusted and do not establish confirmatory or historical-PIT value.

The first attempt stopped on the retained ledger snapshot-identity anchor before
any new cell ran. Its evidence remains intact. The precise old/new identity mapping
and own-role checkpoint recovery were reviewed and registered before restarting;
no policy, threshold, economic field or analysis rule changed. Final focused tests
passed 144 cases, with changed-file lint/type/format checks passing. `make check`
reported 3,795 passed, 28 failed and 20 skipped; the 28 failure identities exactly
match the prior environment baseline. Separate `make planning-check` passed.
The repository remains non-green; no strict B/D, P6 or production gate changed.

**Corrected risk diagnosis step 1 completed, 2026-10-24 Asia/Shanghai (prior):**
the Owner requested stepwise continuation: (1) diagnose existing corrected outputs,
(2) review/register and execute a bounded component experiment if justified,
(3) judge a successor strategy and its further validation. Only step 1 was started.
The read-only diagnostic binds 72 retained input files and recomputes all eight
accepted replay digests. No new replay, warehouse/source acquisition, threshold
search or business-window change is involved. The arithmetic helpers and synthetic
contracts are `p3_risk_attribution.py` and `test_p3_risk_attribution.py`.

Actual filled-cycle wealth/fees and every portfolio close reconcile. For TR versus
T, the observed 13.840258-percentage-point return deficit is partitioned by actual
entry cohorts and calendar month. Forty-seven T buys rejected by TR's entry rule
contributed +9.735456 points on the observed T path; this is **not** a causal estimate
of what TR would earn without the veto. Only nine entries actually filled in both
variants, while capital/timing, entry caps and holding decisions also change paths.
The average invested-capital fractions are 9.4836% TR and 21.6221% T, with similar
average active-unit counts (3.851 and 3.779). The unit-scoped entry caps are 20% and
30%, respectively; TR executed 41 reductions toward the 10% unit target. Its 44
full-exit decisions all derive from the unchanged base rules; none is an additional
risk-extreme exit. All 5,751 held decisions reconcile to their original rules.
An independent cash-flow implementation confirms all 321 filled cycles and 1,664
portfolio closes. Focused checks passed 74 tests; full `make check` reported 3,757
passed, 28 failed and 20 skipped. The 28 failure identities match the preceding
environment baseline (27 frozen DuckDB-version checks and one BaoStock-version
check); the repository is not green. The 759 retained code/config member hashes
are unchanged, but the new diagnostic module is an additive closure member, so
no retained replay receipt is reissued for the enlarged current code closure.

Evidence and the three-step status are under
`/srv/agentdock/.codex/p3-corrected-risk-attribution-20261023T201407Z-12c189f/`.
Step 2 remains NOT_STARTED: a new versioned diagnostic design must distinguish
entry filtering/sizing from held management, preserve base guardians, and expose
the interaction and already-observed-window limitations before execution. The
frozen TR-B headline decision and strict B/D/PIT/P6/production gates are unchanged.

**Accounting-v2 corrected comparison completed, 2026-10-24 Asia/Shanghai (prior execution checkpoint):**
the Owner authorized all three successor steps after the independent review of
`01c7e06`: versioned wiring, fixed four-variant execution and independent cold reproduction.
Implementation is `d6f04f802a01bbf46f7e2d3920075009b20df4b3`; exact primary/cold
activation is `b0627e9f72165195ff73da839c4f350a050b779e` under
`retrospective-execution.v2.json`. The generic accounting-v2 constructor remains
unadmitted, and the v1 registry and sealed results are unchanged.

Both actual runtimes completed B/T/R/TR with one shared session index each. The
cold process used its own previously independently constructed prerequisite,
fresh physical copies/hashes, explicit unchanged-producer AST proof, kernel
filesystem restrictions and network denial. It did not reuse primary batch
intermediates or recalculate identical features. Every business artifact, decision
field and engine record matches under the exact one-to-one physical-receipt identity
mapping; eight new replay digests were recomputed. All 5,751 held reviews reconcile
cycle wealth to independently derived realized/unrealized/dividend PnL. All 38
retained original inputs are unchanged. Every one of the 8,016 entry-policy
decisions per variant is unchanged from v1; B/T economic results are unchanged.

The fixed analysis and independent numerical reproduction are complete. R/TR
improve after correction. The combined variant has clearer drawdown improvement,
but its full-window return deficit versus B exceeds the predeclared drawdown-benefit
tradeoff allowance. Thus the original conjunctive headline acceptance rule still
does not pass. This is not proof of negligible economic effect or permanent harm,
and the diagnostic Trend-only result does not select a replacement policy.
Both accounting fixes were applied together; their individual economic effects
were not separately identified. Actual information cutoff, business windows,
data, expert thresholds, costs and statistical rules remain frozen.

Runtime artifacts, exact bindings, comparison, checks and safe evidence are under
`/srv/agentdock/.codex/p3-accounting-corrected-20261023T192255Z-01c7e06/`.
Focused checks and two synthetic cold relocations pass. Full `make check` records
3,736 passed / 28 failed / 20 skipped; the exact 28 environment-version failures
match the independent-review baseline, with no new failures. No strict B/D,
historical PIT, independent expert-target, P6 or production acceptance is implied.

**Bounded risk diagnosis, 2026-10-24 Asia/Shanghai (prior diagnosis checkpoint):** existing v8 candidate,
feature and decision evidence explains all 801 additional `TR` entry vetoes: 601
volatility-only, 99 RSI-only and 101 both. Annualized-fraction volatility, 0..100 RSI,
strict-above thresholds and all four entry-policy dispositions match; zero entry
mapping mismatches were observed. No new strategy replay or warehouse access occurred.

Two held-adapter defects invalidate a policy-only interpretation of the earlier
portfolio comparison: v1 omits partial-sale proceeds/cash dividends from its return
input, and its strategy-reduction count survives entry into another position. Closed
ledger diagnostics identify 6 R / 5 TR review states where v1 crosses the 8% stop and
position-cycle wealth does not. These are input inconsistencies, not proof that all
11 exits disappear: other frozen exit rules can still apply. Likewise 250 R / 389 TR
recorded no-action states match the reduce condition if the stale cycle count alone
is reset; they are repeated state observations, not a count of counterfactual trades.

Additive `formal-p2-position-accounting-2.0.0` supplies synthetic-tested correction
hooks without activating real execution or modifying old results. The original v1
path is retained for reproduction. See the governing first-formal-topology accounting
correction section and `tests/unit/test_p3_risk_diagnostic.py`. Safe full diagnosis,
exact input hashes and an executable read-only census are outside Git under
`/srv/agentdock/.codex/p3-risk-diagnostic-20261023T182737Z-7409b5a/`.
Next: Owner's Astra review, then a separately bound correction run; no threshold
optimization, strategy promotion, strict B/D acceptance, #474 closure or P6 change.

**Retrospective interpretation completed, 2026-10-24 Asia/Shanghai (interpretation checkpoint):**
the already admitted v8 primary/cold artifacts were read without another replay,
source repair, collection or parameter search. Every manifest and all eight replay
digests were recomputed against the registered receipts and completed acceptance;
daily returns were independently reconciled to exact equity. The frozen six
contrasts, 22-session nonoverlapping paired blocks, 2,000 resamples, request/contrast
SHA256 seeds and one-percentage-point headline rules were applied. The combined
`TR-B` comparison does not satisfy either supported-increment rule, nor the
statistically supported material-harm rule. This is inconclusive retrospective
evidence, not evidence that the economic difference is negligible. Year slices
and the Trend-only comparison remain diagnostic, not a selected replacement policy.

Analysis code is `src/quanthus/expert_strategy/p3_retrospective_analysis.py`; tests
are `tests/unit/test_p3_retrospective_analysis.py`. The complete result, exact
input hashes, uncertainty intervals, entry-veto overlap and reproduction script
are outside Git under
`/srv/agentdock/.codex/p3-results-20261023T174812Z-ba5eb65/`.
The final 20 return dates remain in every full-window point estimate; the incomplete
trailing block is excluded only from resampling and explicitly listed. These are
portfolio-policy comparisons; independent settled expert-target/MAOE quality was
not evaluated and must not be described as accepted.

This completes execution, reproduction and interpretation for the separately
Owner-authorized retrospective comparison. It does not accept the original strict
B, activate original D, resolve #474, qualify historical availability, or change P6
or production. 2025 result access has occurred within the admitted retrospective
namespace and cannot be described as untouched. The single suggested successor is
a bounded review of the existing position-risk veto/state traces; no new run,
threshold search or strategy promotion is opened. `docs-impact: none` for governing
design: this implements the existing fixed analysis contract and records evidence,
without changing policy, authority or experimental windows.

**R2 actual comparison completed, 2026-10-23 15:33 UTC (execution checkpoint):** all four
Owner-authorized retrospective variants B/T/R/TR completed in both the primary
runtime and the independently built, relocated, kernel-isolated cold runtime.
Both process exits are zero. Exact input re-admission commit is `07067eb`; the
implementation correction is `280d194`. There are 416 execution frames, 8,016
frozen candidates and 4,326 common eligible entries. No new source collection,
production switch or P6 mutation occurred.

Full preparation verification took 1,052 seconds concurrently for both roles.
Each runtime built its own execution index once (~759 seconds); all four variants
then completed without the earlier held-action failure. The safe acceptance receipt
is `evidence/r2-final-acceptance-v8.safe.json` under the existing job root, SHA256
`a2d8b06126ca9478cc587eab7323b6c333f9c497b46086e834acf948f0f0a4fc`.
For every variant, equity, daily returns, final ledgers and reconciliation are
byte-identical, and every business decision field is equal after candidate event
coordinates are aligned. Independent physical preparation manifests include distinct
resource/timing receipts; their bound event/decision hashes therefore differ. The
original complete traces remain intact. Explicit one-to-one event/decision identity
mapping and engine-record multiset comparison preserve all numerical, time, sequence
and state fields. Raw trace byte identity is not claimed.

Checks: 51 focused tests, focused lint and three-module type checks pass. `make check`
reports 3,682 passed / 28 known frozen-runtime failures / 20 skipped, with exactly
the same failed test identities as the pre-change baseline; planning-check was run
separately because make stops after those failures. Full-repository green is not
claimed. Original strict B remains unaccepted, D is not activated, #474 historical
PIT is unresolved, and 405 retained-action exception securities remain fail-closed.
Next business step is interpretation of these already approved retrospective
comparisons, not another source-preparation restart or production promotion.


**R2 missing-bar/action separation, 2026-10-23 (historical v8 launch checkpoint):** v7 completed full
semantic verification for both independent preparations and started the first
retrospective variant, then both executions stopped with
`RETRO_HELD_ACTION_UNQUALIFIED`; zero of four variants completed. The exact retained
August DWD confirms no bar/state for the held listing on 2026-08-03. Absence is not
proof of suspension, delisting, or an observed price. The runtime incorrectly used
missing session rows as its corporate-action evidence gate.

The additive `retrospective-comparison.v3.json` carries the existing P10 retained
implemented-action inventory claim separately from bar availability. It embeds exact
original manifest/plan/exception bodies; 405 exception securities remain blocked.
The coverage derivation adds no acquisition and no historical-PIT claim. Missing
prices remain missing and cannot authorize any fill; only existing frozen accounting
may carry the prior mark, applying a qualified action if one exists. Source inputs,
8,016 candidates and the 4,326-entry common mask remain unchanged. Tests cover unknown
and out-of-scope actions, independent action evidence without bars, complete four-way
synthetic execution and offline source/universe binding (51 focused tests passed).

v8 preparation/re-admission and actual execution are the current next steps; old
failed v7 workspaces remain evidence, not running processes or completed results.
Evidence root is the existing `p3-launch-convergence-20261023T085009Z-04bcbe9` job.
`docs-impact: none` for governing design: this corrects an implementation violation
of existing missing-data/no-forced-sale and independently governed action semantics;
no frozen research, P2 matching, strict B/D or P6 policy is relaxed.


**R2 prerequisite recovery, 2026-10-23 (historical v5 checkpoint):** both independent preparations
completed 2,455,533 ports, 2,162,018 feature rows and 8,016 candidates, of which
4,326 pass the common entry mask. The full candidate scan exposed the legitimate
300114 → 302132 dated listing transition. Commit `0faa3de` scans contiguous listing/
company identities separately while retaining the 22-session gate; cross-identity
windows never qualify. Its 33 focused tests passed. Failed v3 attempts remain intact.
The v5 continuation physically reuses each independently built port/feature artifact;
exact hashes and AST checks prove unchanged upstream producer semantics. Recovery
proof is `sha256:ae1464d8a03d9f5f7b7baee44103b753cb9b9ac8f4a14aadc981b7f449ec2294`.
Primary prepared manifest is
`sha256:29a6c62c1710808ecb2944411ca85433070f5bb099d25b84f3ff02c65faca39f`;
independent prepared manifest is
`sha256:bc068ca573bec1710a3c178414a39a156385dcd4673fcc76531fb2df31df4efa`.
Their complete member entries are equal; incidental timing/resource receipts differ.
Full semantic admission verification is running from `prepared-primary-v5` and
`scratch/r2-independent-v5/prepared` under the existing job root. Real comparisons
remain **0/4** until exact receipts are registered and the R2 runtime actually starts.
Original strict B/D and P6 boundaries remain unchanged. Do not restart raw conversion.



**P10-authorized support integration, 2026-10-23 (current):** the Owner explicitly
approved “授权按 P10 职责修复，再启动 P3”. Continue this existing job, not the old
archive hunt. Runtime evidence root is
`/srv/agentdock/.codex/p3-launch-convergence-20261023T085009Z-04bcbe9`.
The 70 SSE/SZSE missing-clock sessions have a separate rule-derived schedule and
passed two isolated relocated cold checks; native nulls remain. Retained status
replay has 2,548,004 observations with independently checked Parquet-local source
ordinals. Retained raw EM/F10 reconciliation produced 8,778 economic events;
405 exception securities remain fail-closed under normalization-v3. The consumer
static concentration projection covers 5,201 securities without a historical-PIT
claim. These are support facts, not original B/D acceptance or real comparisons.

The once-built native join has 2,455,533 rows. Execution qualification v6 closed all 2,455,533 rows, including 1,353,874
qualified execution observations. The v5 census caught expired September calendar
versions; v6 selected the exact visible calendar and reused the unaffected 2,385,414
rows through August. Preserve old failed attempts and evidence. The additive
handoff `sha256:d762075663f3daa8037018ea22856eb4ee3b09b4e81867f5758c10ea1f800c4f`
passed two independent physical, kernel-isolated cold reads and corruption rejection.
`retrospective-comparison.v2.json` freezes its true new support snapshot and all
input identities. Next: build the shared real preparation,
register exact purpose-specific admission, and launch B/T/R/TR. Do not call a
preparation marker a real comparison. Existing frozen strict B/D and P6 remain
unchanged. The execution registry remains the result-access boundary.


**Execution convergence, 2026-10-23 (current):** Owner requested continuation through actual
P3 startup. The separate two-clock runtime is now connected to existing B/T/R/TR/P2 arithmetic
for qualified no-action inputs. Synthetic four-variant fills/ledgers, strict-control arithmetic
parity and two relocated isolated processes are verified; this is still **0/4 real comparisons**.
Do not represent a synthetic COMPLETE as original B acceptance or D activation.

The actual consumer-date/venue/effective-interval census now isolates 175,110 August/September
observations with missing calendar clocks and explicit price bounds. Calendar is P10 #555;
execution status, action composition/revision and no-action support are P10
[#556](https://github.com/rv198-star/Quanthus/issues/556). The earlier broad key-only join had
175,145 rows; 35 are outside the exact consumer effective-interval denominator. Preserve both
receipts. The 8,736 same-security/ex-date action groups have different canonical term sets;
this does not establish that every group is corrupt or economically conflicting. Four
IMPLEMENTED transfer records lack ex_date. Published calendar metadata proves a pre-window
120-session lower bound for 5,330 of the 5,602 retained listings; the other 272 are not all
missing data and must be evaluated at their actual candidate dates. Do not repeat source hunting.

Continue from [the execution boundary](../design/lld/04-experts-aggregator/p3-retrospective-preparation.md#separate-execution-boundary-2026-10-23-continuation)
and `retrospective-execution.v1.json`. Its real admission registry is intentionally empty.
Next data-dependent step is a versioned P10 effective-view/support handoff, then exact
consumer admission and normalized-action integration where required. Preserve current
business dates, unknowns and true snapshot time. Do not silently replace the input with the
later, narrower P6 #538 repair or reinterpret the old strict gate as passed.

**Owner-approved exploratory preparation, 2026-10-23:** the Owner explicitly selected
“同意回溯口径，继续准备”. The new comparison purpose is
`config/research/p3-strat-003/retrospective-comparison.v1.json`; it preserves the original
strict request, business window, numeric policies and original B/D status. It authorizes
preparation, not a real strategy/economic result run. `p3_retrospective_preparation.py`
now materializes one typed compact journal, one prefix-correct feature panel, exact P4
candidates, a common mask and four identically bound variant inputs from a single port
stream. Its completion marker is deliberately not READY/admission. Source/support semantics
are not qualified by creating a package; late clocks must not be passed off as strict
EventRecord/StrategyReplayFrame/BacktestSignal objects.

The confirmed 35-date OPEN calendar clock gap is registered with P10
[#555](https://github.com/rv198-star/Quanthus/issues/555), linked to #485/#474 and distinct
from the bounded P6 source work #538/#539. Do not resume an archive hunt or infer 09:30/15:00
source clocks. See [the current preparation record](p3-base24-goal-audit-and-plan-20261022.md#19-回溯比较准备与-p10-数据缺口)
for the exact actual compatibility evidence, checks and remaining admission boundary.

**Consumer component continuation, 2026-10-23:** the separate R2 bridge now connects
source-snapshot/business-date ports to frozen P4 numeric rules, prefix-safe shared feature SQL,
one complete-case mask and execution-only P1/P2 adapters. The R2 candidate dictionary is not a
strict EventRecord; clocks remain original. See [the component continuation](p3-base24-goal-audit-and-plan-20261022.md#18-双时间消费组件接续)
and its current evidence before continuing. Synthetic component integration is not real input
qualification, original B acceptance or D activation. Preserve the selected input and completed
copy proof; do not rerun the historical source hunt.

**Result-blind consumer continuation, 2026-10-23:** `p3_retrospective_view.py` now reads the
sealed retrospective input through a separate two-clock interface. Canonical warehouse
`common_equity` requires explicit CNY and SSE/SZSE scope; the original `A_SHARE` strict-reader
default is preserved. Empty input cannot pass. Closed sessions and open sessions with missing
clocks are distinct; source times stay unchanged. Global snapshot verification precedes bounded
monthly reads, with one shared market-scope TMP per month. This is input-consumer engineering,
not candidate/features/common-mask qualification, original B acceptance or D activation.
Continue from [the actual consumer-start record](p3-base24-goal-audit-and-plan-20261022.md#17-结果盲消费端实际启动),
preserving the failed bounded attempts and their evidence. Do not reopen the same historical
source hunt or pass the retrospective reader into the strict bridge.

**Purpose-specific continuation, 2026-10-23:** the Owner requested trying the bounded
evidence/purpose-contract route instead of indefinitely recovering historical versions. The
additive `base24-retrospective-purpose.v1.json` permits a result-blind, fixed-snapshot input
assessment and physical job-local preparation. It does not replace the strict frozen request or
infer any historical clock. `p3_purpose.py` checks the selected archive with original knowledge
intervals at its declared snapshot; `p3_retrospective_input.py` preserves those clocks, separates
unknown-clock records, and verifies the new input without reopening source roots. Strict
NativeView admission rejects this distinct artifact. This advances retrospective input work;
it is not original B acceptance, candidate/feature integration for the new purpose, or D
activation. The original 4,173 factor-visibility policy stays unchanged. Continue from
[the purpose-specific disposition](p3-base24-goal-audit-and-plan-20261022.md#16-按用途收敛历史证据缺口),
not by renaming the new artifact as strict-PIT input or retrying the spent B Job.

**Actual bounded result, 2026-10-23:** the selected 16-file source closure was verified and
one 232,280,216-byte physical retrospective input was sealed as manifest
`sha256:6d680dc7685e40582439e99c0d1bf19637afc0bbfd28605bcbbdbc929c787735`.
All 16 typed table comparisons matched their selected source rows without changing values
or clocks. Two independent physical copies passed full offline verification in new processes
with kernel filesystem/network isolation; missing-file and corrupt-byte disposable tests
rejected access. Twenty-four archived price-limit listing references do not occur in the
selected daily observations and are disclosed as unconsumed, not repaired. This closes the
bounded physical-input work only: the new purpose still needs an explicit two-clock
candidate/feature/execution integration and purpose-specific admission before any D results.
Do not spend another source-recovery attempt on these same historical-clock gaps.

**Prerequisite repair, 2026-10-23:** exact 60-session membership is now derived from the
SSE/SZSE 2024 official notices (both declared 2023-12-26), reconciled against all 732 native
2024 exchange/date metadata rows, and pinned to the native session identities for
2024-10-09 through 2024-12-31. This does not backdate warehouse clocks or qualify historical
market values. `p3_base24_batch.py` now connects verified native streams, the existing P4
scan and baseline feature SQL to the existing B/T/R/TR runtime; synthetic execution includes
actual P2 fills, independent prerequisite rebuild, two relocations and two new processes
with kernel network denial. Qualified action terms can replay; unavailable terms block the
affected held-position step without inventing adjustments or selling. Six additional exact
sealed metadata members were physically verified by P10: original historical-purpose proof
is still not recovered in that selection. No real candidate/features, new B acceptance or D
results were produced. Continue from [the current three-prerequisite disposition](p3-base24-goal-audit-and-plan-20261022.md#15-三项前置修复的实际交付),
not from an older engineering-only claim or spent formal B attempt.

**Domain-port continuation, 2026-10-23:** the Owner requested fixing the startup blockers and
continuing. The native P4 row conversion, shared B/T/R/TR complete-case mask and separate P2
execution-evidence port are now implemented in `p3_base24_bridge.py`. They consume the verified
job-local native closure, retain original clocks/Decimal values and reject unsupported source/key
substitution. This is consumer engineering, not a formal candidate/feature-panel build or B/D
acceptance. An explicit P10 review checked eight exact sealed metadata members and retained the
five required historical-purpose gaps; it did not find original availability proof in that
selected evidence or infer global absence. The previous clock measurement is referenced without
pretending it was rerun. See [the domain ports and remaining boundary](p3-base24-goal-audit-and-plan-20261022.md#14-原生消费端口与历史资格边界).
Do not pass a later P6 qualification as historical support, recreate the spent B attempt, or
start real strategy results until the independently qualified new B identity and D gate exist.

**Base 2.4 startup, 2026-10-23:** the Owner requested P3 continuation after the input-engineering
reverification. The new input-only scope `P3-BASE24-INPUT-20261023-CLOCK-001` ran against published
projection manifest `ec8adacf91b55062e4873ca6f18216fc4d41c1c6c6f59bc172255606a2f0a6dd` at code
`4633f22cd2a8c953eef50f85d2f563ad22798f79`. All 16 native member hashes matched; input clocks were
counted without selecting prices, outcomes, or pre-2025 market observations. This is a new startup
diagnostic, not another formal B attempt. The existing P10 handoff explicitly does not grant P3
historical use. Current raw clocks yield no daily bars visible at their own business close and no
2024 calendar versions visible before 2025; warehouse lateness alone does not prove economic
unavailability. P10 #474 must supply an exact, purpose-qualified clock/evidence contract before
formal B/D continuation. Preserve #470/#473, the original 4,173 unavailable factor exceptions and
all frozen windows. The consumer bridge was incomplete at that diagnostic; the successor ports
above do not change its historical-purpose verdict. See [the startup result and recovery command](p3-base24-goal-audit-and-plan-20261022.md#13-2026-10-23-p3-启动的实际结果).
The earlier receipts and spent formal attempt below remain historical evidence.

**Current ownership supersession, 2026-10-03:** P10 permanently owns all source recovery,
acquisition, data-quality diagnosis/repair and availability evidence. The completed receipts below
remain immutable. Their former next-source-action instructions no longer authorize P3 data work.
#412 retains consumer admission and strategy business work: register a P10 issue, choose and
record bypass/temporary ignore/acceptance of a known limitation, and continue where the contract
allows. #470/#473/#474 are P10 dependencies. The required B/PIT gate remains unaccepted and D
inactive; no whole-line freeze or automatic research acceptance follows from issue routing.
Current business choice is **temporarily ignore** the unqualified B acceptance/D result steps;
retain existing diagnostics and continue only previously authorized business/synthetic engineering.
This records a business disposition, not data repair or B/D acceptance.
See `../design/lld/12-permanent-data-line.md` and `config/data-ownership.v1.json`.

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
bundle or candidate was built. Current P10 dependency: qualify per-family economic availability
from bound source evidence before any distinct corrected B; a price-basis alias alone cannot fix
#474 or activate D. P3's next business action is to record bypass/temporary ignore/acceptance of
the known limitation and continue only permitted domain work. P3 does not investigate or repair
the source. See `../acceptance/p3-p5-input-recovery-20261003.md`.

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
still needed before any distinct corrected B can run. P10 owns obtaining that evidence; P3 chooses
business disposition and consumer admission only. No current P3/P5 research acceptance follows.

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
