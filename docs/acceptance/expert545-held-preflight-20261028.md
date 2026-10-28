# #545 held-position structural-input preflight

This is the bounded `P3-EXPERT-007` preflight authorized on 2026-10-28, not full #545 acceptance.
Governing implementation: [held-structure-preflight](../design/lld/04-experts-aggregator/expert-strategy-layer/held-structure-preflight.md).
Base: `bd9543ae2f847317aed7b22ec8622016a0f179fe`; retained P3 archive producer/closeout:
`4199786e5a0a0fdde55ed84271b3ed703f0aa3fc`. Execution remains on CN02, on the isolated branch
`codex/expert545-preflight-20261028`. Scope began at 07:01:34 UTC with a 10:01:34 UTC hard end.

## 1. Consumer and input admission — completed, real comparison blocked

The actual consumer is `PositionRiskPolicy.evaluate_held`, through
`FormalP2ReplayRuntime._review_positions` and `FormalStrategyRunner.evaluate_held`.
The selected B/BH/TH archive manifests bind the same strategy-card semantic digest as the retained
1.0.1 configuration: `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`.
The current expert uses volatility20, RSI14, holding sessions, marked return and prior reduction
count. The enclosing base policy also uses its existing total-return/SMA20/SMA60 deterioration
streak, maximum holding period and constraint stop. These branches remain unchanged. No new
structural input is read by the expert, and no new action mapping has been frozen.

The existing #541 fixture has 19,302 security-days across 128 new-2021 issuers. It records raw-price
breakout margin and volume ratio; it does not contain unreclaimed-support age, signed support
distance or post-break rebound. All three held populations have **zero** code/date overlap with
that exact fixture. Even nonzero ticker/date overlap would not establish an exact listing,
price/action coordinate, calendar, feature revision, knowledge or purpose bridge. Its review-queue
purpose is not held-risk action admission; raw prices are not aliased to total-return features.

The retained 10/11 contract still establishes development projection semantics only. Its counts
(241 observations; 10 contexts and 6 nested rebounds) are retained accepted metadata, not a fresh
held-coverage census. The one exact P029 manifest locator named in the retained result,
`/data/event-runs/structure-runs/run_c7f1203e5d8c4dc6a6e5884858bf8e72/manifest.json`, is absent
at that CN02 path. No private archive search or recovery was performed. This does not assert that
the original F9 runs are globally missing or that their historical acceptance failed.

All source/purpose findings are in `consumer-input-admission.safe.json`. The inspected archive
source information cutoff remains `2026-10-25T08:51:06.934678+00:00`, with historical PIT false;
no date has been backfilled. Missing held evidence still abstains rather than causing a sale.
Independent existing base actions and guardians retain their own authority.

## 2. Full decision denominator and reachability — completed within selected scope

The exact source selection and event addition were frozen before their respective first row reads.
The command verifies all 13 selected files (105,346,156 bytes) against full SHA256 pins, including
unused archive metadata, before writing output. It reconstructs one immutable job-local identity
TMP and reuses it for the admission and footprint screens. No B/BH/TH economic replay was rerun.

| Arm | All decisions | Held decisions, all resolved | Issuers by archive code | Entry episodes | Held dates |
| --- | ---: | ---: | ---: | ---: | ---: |
| B | 31,348 | 8,393 | 343 | 538 | 2,378 |
| BH | 31,363 | 8,408 | 330 | 517 | 2,407 |
| TH | 32,084 | 9,129 | 246 | 302 | 2,378 |

Each decision resolves the same-day position snapshot and an earlier filled entry through the
accepted coordinate map. No missing row is dropped. The archive key `held:capital-unit-NN` alone
does not identify a security. The complete aggregate census covers 25,930 held reviews; overlapping
reviews and entry episodes are not independent scientific samples.

| Arms | Common unit/date | Different security | Same security, different entry | Same entry | Same entry, different quantity |
| --- | ---: | ---: | ---: | ---: | ---: |
| B/BH | 6,393 | 4,620 | 13 | 1,760 | 1,136 |
| B/TH | 6,584 | 6,322 | 0 | 262 | 183 |
| BH/TH | 6,563 | 6,421 | 13 | 129 | 0 |

Even matching entry and quantity does not establish an equal complete held input state. Compact
decision records do not carry the full contemporaneous feature/reduction-history/context revision
binding. Therefore this census reports **zero qualified full-state pairs**, not evidence that no
equal state ever existed. It does not attribute economic differences to one expert.

The existing expert could react to supplied new evidence only after a versioned mapping exists;
currently its structural-field reads and added structural branches are zero. Potential changed
decisions and economic effect remain **null**, not a fabricated zero-effect result. The exact
selected fixture yields zero compatible decision/Facts pairs, and no independent usefulness
criterion or incremental label-over-continuous-Facts hypothesis is qualified.

## 3. Conditional comparison and disposition — closed with NOT_RUN

Decision: `STOP_NO_ADMISSIBLE_DECISION_FACT_PAIR`, scoped to this selected tranche. Per the
approved stop rule, no alternate sample, feature sweep, source search, new experiment or outcome
read follows the incompatibility finding. No new request claims executable comparison authority.
No threshold, accepted experiment, frozen research window, original source or consumer policy was
changed. This is neither a negative efficacy finding nor completed expert value/learning admission.

The single reopening condition is a jointly qualified **held-decision/Support-Evolution input and
comparison contract**: exact compatible evidence, its source/price/calendar/knowledge-purpose
binding, an independently resolvable usefulness criterion and a frozen candidate mapping with the
same-Facts control if labels are used. P3/P5 own consumer design/criterion/admission; only a proven
shared-source deficiency routes to P10. None was established by a fixture-purpose mismatch, so no
speculative P10 bug or acquisition campaign was created. #545 remains open for its broader goal.

The blueprint records completed offline engineering, blocked input compatibility, inconclusive
value and admission not granted separately. The 12+X learned-state objective, parked volume gate,
seven-feature Head2 backup, nine-feature rejection and stopped 3/4 reference route are unchanged.

## Verification and evidence

The focused suite covers complete denominators, distinct security/entry/quantity paths, missing and
duplicate rows, coordinate mismatch, future/unknown/Shanghai timestamps, scope bounds, null support
fields, integrity pins, corruption before output creation, no overwrite, no network, source-byte
preservation and no automatic admission from ticker/date overlap. Existing actual-consumer tests
include `test_missing_held_evidence_never_forces_sale`.

Runtime: Python 3.10.12, pytest 8.4.2, Ruff 0.16.10, mypy 1.20.2. Focused tests: **45 passed**;
documentation/blueprint contracts: **68 passed**. Changed-file Ruff and format checks pass; focused
mypy passes for the production module and CLI. `make check` exits 0: **5,418 passed, 20 skipped,
8 warnings**, versus the retained base's 5,386/20/8, adding 32 regressions. Optional dependencies
remain unavailable for the skipped cases. Full check elapsed 645.07 seconds with peak RSS
584,331,264 bytes; the census elapsed 3.064 seconds with peak RSS 220,651,520 bytes. These checks
do not change the scientific stop disposition. Whole-tree lint/type were not rerun; existing debt
and remote Actions billing limitations are not represented as green CI by these focused checks.

Selected evidence job:
`/srv/agentdock/.codex/expert545-preflight-20261028T070134Z-bd9543a/`.
Authoritative safe aggregates are `analysis-final/decision-footprint.safe.json`,
`evidence/consumer-input-admission.safe.json`, and `evidence/input-unchanged.safe.json`.
The source pins, runtime receipts, plain-text test logs, diff and SHA256SUMS accompany the readonly
handoff. The private identity TMP, prices, returns and original decision rows are not exported or
committed. These receipts establish bounded screening only, not source requalification or an
independently reproduced new economic comparison.
