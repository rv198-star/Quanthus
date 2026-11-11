# March-cutoff entry confirmation: completed negative batch

| Field | Value |
| --- | --- |
| Design status | approved — Owner approved the fixed TRAIN20 / EVAL87 successor and a five-hour ceiling on 2026-10-29 |
| Delivery gate | blocked for consumer admission; the three-step research batch is complete |
| Work items | #540 coordination; #542 qualification/reference; #545 consumer comparison; #546 reproduction |
| Parent | [Conditional continuation and current recovery route](../work-items/learning-next-route-20261028.md) |
| Frozen request | [`entry-march-confirmation.v1.json`](../../config/research/learning-mvp/entry-march-confirmation.v1.json) |
| Job | `cn02:/srv/quanthus-base22-data/jobs/entry-march-confirmation-20261028T191129Z-8aee69a` |
| Control and execution state | `cn02:/srv/agentdock/.codex/entry-march-confirmation-20261028T191129Z-8aee69a` |

## Decision

Complete this exact batch and park **this Head2 entry-filter candidate**. The new
model fails the frozen requirement to improve both controls after costs without
worsening drawdown. This is useful negative evidence, not a blocked or unstarted
capital comparison. Consumer admission remains `NOT_GRANTED`; no P6 simulation,
production change, extra acquisition, threshold tuning or later-cutoff rescue follows.
The broader learning MVP has not established incremental decision value.

The earlier fixed-checkpoint zero finding remains correct for its original scope.
This is a separately approved March-cutoff experiment, with different training and
fresh initial cash. It does not reproduce the old full-market B equity curve.

## 1. Fixed opportunities and blind reference

- Training is exactly 20 rows through 2021-03-31: four positives, 16 negatives,
  three anchor blocks and seven features. The training manifest SHA256 is
  `5b18e075167e23c3c286666a2d25abae9d7d15e4bb58c0bc79cce12e659c10f6`.
- Evaluation retains all 87 opportunities across 66 companies. Its manifest SHA256
  is `6858a54dca8251b0efa0d26cd63e5dafcc09ed5174c5acc3e9eebc72c45000d3`.
  Train/evaluation companies are disjoint; 34 previously studied held-position
  companies were conservatively excluded before new scores or returns were opened.
- Complete Facts support 83 opportunities. Three cross a coordinate-changing action
  within the fixed 40-session window and one has an unsupported training mask.
  These four remain unavailable, block affected entries and never become negative
  labels or forced-sale instructions. The prior metadata preflight of 84 passes was
  not the final Facts/mask qualification.
- Two separate candidate-blind AI readers committed complete 87-opinion envelopes
  against the same anonymous current20 / preceding20 packet. There are 58 definite
  consensuses (23 positive, 35 negative), 25 unknowns and four unavailable cases.
  Positive/negative support spans 23/33 distinct companies and five monthly blocks,
  exceeding the unchanged 12-consensus, five-companies-per-class and two-block floors.
  These are operational AI references, not independently established market truth.
- Full opinions were opened only after the checkpoint and all 87 predictions were
  committed. The support-only receipt preceded fit. There was no third vote,
  sample replacement, label rescue or new collection.

This remains a minimum pilot: four training positives are little support; training
companies are 2020 IPOs whereas evaluation companies listed in 1993–2019. The
design and source acquisition happened later. An observation cutoff in March 2021
does not establish historical PIT availability in March 2021.

## 2. One refit and three actual capital paths

There was one new generation-zero fit: train-only scaler, zero initialization,
200 steps, learning rate 0.05 and L2 0.01. There was no update or calibration.
Checkpoint SHA256 is
`958eb4a2b2d04b91a0b1f073d106c5b687c8b2332491fc396e4b2ef7afb1f0bf`;
all-prediction SHA256 is
`51d395337bd9a5ab7d4ccc9c3af111ba6b9829bf7477db43b31ab4519ba53f1e`.
Both were committed at 19:45:13 UTC before full reference opinions were opened.
The old 40-row checkpoint remains unchanged.

On the 58 definite references, model recall is 100% versus R0's 52.17%, while
negative specificity falls to 65.71% from 91.43%. Balanced accuracy is 82.86%
versus 71.80%. More positive detections also produce 12 false positives versus
three; the new model does not dominate the simple rule on both classes. These are
descriptive reference results, not proof of trading usefulness.

The pointwise admission diagnostic has 48/11/28 eligible opportunities for
C0/C1/C2, with 17 C1–C2 changes across 17 companies and 16 signal dates. The
predeclared zero/single-company/single-date futility test therefore did not stop
the actual capital comparison.

Each arm starts independently with five cash units of 100,000. The retained
`RestructuringRuntime` owns each arm's allocation, cash, holdings, next-open fills,
fees and exits. The new adapter can only withhold a prior B pass, not re-enable a
B rejection or force a sale. The 183 common session frames run from 2021-04-02
through 2021-12-31; entry opportunities end in August. The unchanged rights policy
is `DO_NOT_SUBSCRIBE`. Six selected action rows were admitted as source inputs;
none produced a corporate-action cash event in these actual scoped portfolios.

| Path | Net total return | Maximum drawdown | Fees | Fills | Average idle capital |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0: original B, scoped universe | +1.145054% | 2.967447% | 816.73 | 50 | 88.3171% |
| C1: B + unchanged R0 | −1.980956% | 2.067270% | 346.78 | 22 | 95.2288% |
| C2: B + new Head2, raw score ≥0.5 | +0.385698% | 3.150021% | 652.51 | 40 | 91.0401% |

Every final ledger reconciles to equity; all positions are closed. C2 trails C0
by 0.759356 percentage points and has worse drawdown than either control. Its
2.366654-point advantage over C1 becomes negative after removing the largest
issuer or entry-decision-date contribution. Issuer/date sensitivity is accounting
attribution, **not** a rerun that reallocates the freed capital.

The paired five-session block bootstrap uses 10,000 draws and seed 20210331.
Its C2−C0 interval is [−3.377360, +1.703862] percentage points; C2−C1 is
[−7.242596, +11.123515]. The final three sessions are held fixed. The general
bootstrap was frozen before results, but this partial-block implementation was
not separately preregistered: these intervals are descriptive only. The negative
point-return/drawdown gates already suffice for `NO_PASS_FIXED_VALUE_GATE`.

## 3. Independent construction and reproduction

The cold role uses independent physical copies of the selected source closure,
versioned code snapshots and new processes. It rebuilds Facts and training rows,
fits again, commits predictions again and runs all three stateful portfolios.
Facts, training, packet, checkpoint and all predictions match byte for byte.
All 21 portfolio files also match byte for byte and have distinct physical inodes.

Landlock and seccomp deny original sources, primary artifacts and source network;
native Arrow denial probes are included. Actual native source/feature readers use
the independently copied inputs. This is independent construction of the new
scoped experiment from qualified immutable historical feature inputs, not a fresh
rebuild of the original ten-year feature lane or an external scientific review.

The final source postcheck rehashed 895 selected unique files (1,795,172,983 bytes),
including 124 runtime and 719 training members plus binding metadata. All original
input bytes remain unchanged and new source requests are zero.

## Engineering, attempts and retained limits

Production logic is in `src/quanthus/stage_learning/entry_confirmation.py`,
`entry_capital_analysis.py` and `src/quanthus/expert_strategy/entry_march_capital.py`;
the four `scripts/entry_march_*.py` drivers freeze, fit, execute and reproduce.
Four focused unit modules cover qualification, commitment/gates, the capital
adapter/budget census and actual cash/date/issuer attribution.

Primary capital executed adapter revision
`4b6f110cf4b331e914a507785051eeb80f58b528`, with retained runtime revision
`4199786e5a0a0fdde55ed84271b3ed703f0aa3fc`. Cold code and orchestration snapshots
are separately pinned in the job; the final handoff revision also includes typing,
attribution and monitoring repairs. Do not describe every final file as the exact
revision that executed the earlier primary portfolio.

- The first eager full-history SQL materialization exceeded the 2 GiB RSS ceiling
  and stopped before portfolio results. The repair projects the exact required
  companies/date range before materialization, preserving parent-plus-patch values.
  Primary/cold observed peak RSS is 2,031,620,096 / 1,872,285,696 bytes; elapsed
  capital time is 109.30 / 93.04 seconds. Failed attempts and their receipts remain.
- Cold transport attempts exposed an invalid denial-probe path, an unmapped rubric
  hash and an omitted 620-byte admission receipt already bound by the original
  baseline. Exact mappings/bindings were repaired; original failures remain. No
  new market source or invented qualification was substituted.
- A test-budget census raced deletion of disposable TMP directories. It now ignores
  vanished TMP entries while rejecting permission/scanning failures. This changes
  job resource counting, not the source inventory's fail-closed traversal contract.
- Python 3.10.12; PyArrow 23.0.1; DuckDB 1.5.6; NumPy 2.2.6. The frozen job has
  a 3 GiB data-write ceiling, a 2 GiB process RSS ceiling and an 8 GiB free-space floor.
  Source data, previous jobs and failed evidence were not deleted to satisfy it.

Final checks use the declared Python environment and are retained as plain text:

| Check | Actual outcome |
| --- | --- |
| Four new focused unit modules | 23 passed |
| Ruff on the 11 changed Python files | Passed |
| Mypy on the seven new source/driver files, `--follow-imports=silent` | Passed |
| Final `make check`, with 15 execution-sensitive cases assigned to executable TMP | 5,576 passed, 20 skipped, 15 deselected; doctor, compile and planning checks passed; command exit 0 |
| The same 15 deselected cases, unchanged assertions, executable system TMP | 15 passed; command exit 0 |
| Complete partitioned test execution | 5,591 passed, 20 skipped; no additional blanket skips |
| Whole-repository Ruff | 216 existing findings, exact finding identities unchanged; not green |
| Whole-repository mypy, remote CI, external Astra scientific review, P6 | Not run in this batch |

The first unpartitioned `make check` recorded 15 failures when test executables
were placed on the data mount's `noexec` TMP. An intermediate monitor also stopped
a retry on a vanishing disposable directory. Both original failures remain in
the job; neither is relabeled as a successful unmodified command. The final
split execution is an environment correction, not deletion of those test cases.
The full receipt is `evidence/final-repository-checks-v2.safe.json` under the job.

## Current disposition and next work

Record engineering and reproduction as completed for this exact experiment;
reference/model quality remains research-only, this entry-filter value is rejected,
and admission is not granted. Preserve the reusable Facts, fitter, readers,
capital adapter and independent-run machinery without counting them as a successful
learning MVP. #540/#546 remain open and no production or P6 state changes.

Do not try a June cutoff, retune 0.5, relabel unknowns or reuse this now-consumed
evaluation as a fresh holdout. Any further learning investment needs a separately
justified consumer/hypothesis and a fixed exposure-aware design. The present batch
does not authorize it. The append-only [blueprint](../work-items/learning-blueprint-progress.md)
owns the current module gaps and links to the exact safe receipts.
