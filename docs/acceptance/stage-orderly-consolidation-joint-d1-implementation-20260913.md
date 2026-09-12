# P3-STAGE-011-D1 — Fresh 2018 Joint Development Prepare Engineering Acceptance

## Disposition

```text
D1 ENGINEERING: ACCEPTED
AUTHORIZED NEXT ACTION: ONE RESULT-BEARING D1 PREPARE JOB ONLY
AUTHORIZED MARKET READ: 2017-09 WARM-UP + 2018 DEVELOPMENT ONLY
D2 CANDIDATE-PAIR SCORING: BLOCKED UNTIL BLIND REVIEW LEDGER IS SEALED
PRESERVED 2021 EVALUATION: UNREAD / NOT AUTHORIZED
2019 P010 / 2020 P009 / 2022-2025: SEALED
AUTOMATIC RETRY / SUPPLEMENT: NONE
```

## Implemented scope

D1 is implemented by:

- preregistration: `config/stage-learning/orderly-consolidation-joint-development-screen.v1.json`;
- execution contract: `config/stage-learning/orderly-consolidation-joint-development-execution.v1.json`;
- execution digest: `sha256:3424f673f11af42d37a454429fb9e624978b18799d10fea21c365f81c0c0d680`;
- 2018 evidence scope: `config/stage-learning/order-reference-evidence-scope-2018.v1.json`;
- evidence-scope digest: `sha256:2400ccd1c025098b0a3d050aebf39cc0ebc7241289d9db2e8425819f9daf76c9`;
- pure joint threshold/grid/scoring logic:
  `src/quanthus/stage_learning/orderly_consolidation_joint_development.py`;
- D1 prepare/verifier:
  `src/quanthus/stage_learning/orderly_consolidation_joint_development_prepare.py`;
- governed Stage Worker route:
  `quanthus.stage-orderly-consolidation-joint-d1-prepare.v1`.

No P011 market data was read and no RX6800 Stage Job was submitted while implementing or accepting
this checkpoint.

## Source isolation

Before any D1 artifact can be published, the prepare path verifies the exact governed upstream
artifacts from Stage-007, Stage-008, Stage-009 and Stage-010. The fifth fresh pool excludes:

- Stage-007 excluded identities and exposed pool;
- the complete Stage-008 fresh pool;
- the complete Stage-009 512-identity fresh pool, including the preserved 2021 identities;
- the complete Stage-010 512-identity fresh pool.

The prepare then discovers the preregistered 2017 cohort, constructs exactly one fifth fresh
512-identity pool under the frozen seed, takes the first 256 identities as development capacity and
requires zero overlap with the preserved 2021 plan.

D1 is bound to:

```text
history / warm-up start: 2017-09-01
development start:       2018-01-01
development end:         2018-12-31
candidate cohort date:   2017-12-29
```

It has no code path authorizing the consumed P010 2019 development, consumed P009 2020 development,
2021 market rows, 2022-2024 or 2025.

## Threshold and candidate preparation

The joint basis deliberately reuses the accepted P010 Stage0 basis implementation rather than
reimplementing it. P011 adds only positive contraction magnitude:

```text
contraction_magnitude20 = max(0, -width_change20)
```

from unlabeled development observations. Labels and reference opinions are unavailable to the basis.

The candidate surface is published before review scoring as:

```text
Stage0 profiles = 9  (exact P010 topology)
Stage2 profiles = 3  (exact P011 bundled topology)
Joint pairs      = 27 (exact Cartesian product)
```

Every Stage0, Stage2 and pair identity is content-derived and unique. The artifact records
`parameter_search_performed=false` and `grid_expanded_after_result=false`.

The D2 selector is also implemented before D1 data is seen. It freezes:

- separate Stage0 and Stage2 precision / positive-reference coverage / definite-negative FPR;
- the P010/P008 Stage0 no-regression rules;
- raw proposition overlap count equal to zero;
- the preregistered pair selection order;
- fail-closed handling when the frozen Stage0 baseline metric is undefined.

D1 itself does not invoke that selector. Candidate-pair scoring remains blocked until review references
are durably sealed.

## Blind development review transport

D1 reuses the already accepted Primary-only transport mechanics rather than creating a parallel review
protocol. The new Job kind has a separate deterministic review index and virtual page ids, while page
content remains limited to:

- the T-time Primary case;
- the two directional path Facts;
- the last 21 raw T-time path rows.

The transport explicitly excludes:

- threshold basis;
- Stage0 / Stage2 candidate output or pair ids;
- explicit recent derived Facts;
- future rows;
- the preserved 2021 evaluation payload.

This keeps the `short_term` and `fund` Primary review sealable before D2 pair scoring.

## One-shot Worker contract

The admitted Job kind is:

```text
quanthus.stage-orderly-consolidation-joint-d1-prepare.v1
```

Its budget marker is independent of every previous Stage Job:

```text
orderly-consolidation-joint-d1-budget.json
schema = quanthus.stage-orderly-consolidation-joint-d1-budget.v1
```

A same-revision resubmission returns the existing Job idempotently. A different revision is rejected
with no retry or replacement authority. There is no technical-retry, transport-recovery, semantic-
retry or automatic-supplement route for D1.

The Worker verifier uses the common `verify --artifact <path> --code-revision <sha>` contract. The D1
module implements that exact interface, avoiding the P010 verifier argument drift.

## Artifact verification

The governed D1 artifact inventory is exactly eight files plus manifest/READY:

```text
source-binding.json
development-selection.json
preserved-evaluation-plan.json
threshold-basis.json
candidate-grid.json
evidence.json
path-facts.json
summary.json
```

The verifier checks the preregistration/execution bindings, file hashes, 2018 evidence protocol,
selection/basis/grid/evidence/path-facts digests, `9 / 3 / 27` candidate counts, prior-development
non-access, `preserved_evaluation_data_read=false`, `parameter_search=false`, `opinion_count=0` and
`pair_frozen=false`.

Synthetic verifier tests also prove evidence-scope drift is rejected rather than silently accepted.

## Engineering verification

The focused P011 D1 engineering slice covers:

- joint basis / Stage2 profiles / 27 pair identities;
- pair hard gates and frozen selection order;
- fail-closed Stage0 baseline handling;
- D1 artifact verifier and evidence-scope binding;
- Worker one-shot/idempotency behavior;
- candidate-blind virtual review transport;
- the accepted Stage2 proposition module.

Focused D1 engineering result:

```text
100 passed
Ruff: PASS
git diff --check: PASS
```

Repository-wide gate with the repository development virtualenv explicitly bound as `PYTHON`:

```text
2259 passed / 7 skipped
Quanthus doctor: OK
compileall: PASS
planning sync: PASS
```

The initial bare `make check` in the isolated worktree selected `/usr/bin/python3`, which did not have
pytest installed; that environment-selection failure occurred before tests and is not treated as a
code failure. The complete gate above reran the same Makefile target with the existing repository
`.venv` interpreter and passed.

## Claim ceiling

This engineering acceptance does not:

- claim that the 2018 fixed sample will satisfy reference sufficiency;
- score or freeze any of the 27 pairs;
- change the P010 Stage0 topology after its consumed result;
- retune the P008 relocation guard;
- permit a review supplement or candidate-grid expansion;
- read preserved 2021 or any 2019/2020 consumed development rows;
- authorize 2022-2025, future outcomes, official labels, daily activation, strategy or trading.

After repository-wide verification and durable push, only one D1 result-bearing prepare Job may be
submitted on RX6800. If D1 fails or produces an insufficient fixed review denominator, there is no
automatic retry or supplement; the failure must be classified under a separately governed decision.
