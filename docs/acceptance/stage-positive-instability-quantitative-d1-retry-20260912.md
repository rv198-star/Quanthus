# P3-STAGE-010-D1 Technical Retry Authorization — 2026-09-12

## Disposition

`TECHNICAL_RETRY_AUTHORIZED_ONCE`

The first governed D1 Job did not produce a governed artifact and did not reach development-case selection, reference collection, parameter search, candidate scoring, or preserved-2021 evaluation access.

## Failed Job

- Job: `stage_b5e3feae8b9f47faae7566c308444b3c`
- Revision: `3b5ada72b44905c520521ade6315552c36330d09`
- Input digest: `sha256:04789f6075e1d9874c5cf07df633565fd81c409dc02a1fdf9c058c70482a543a`
- Terminal state: `failed`
- Worker error: `stage_positive_instability_quantitative_d1_process_failed`
- Governed artifact: none

The RX log shows the process failed immediately at `read_development_identity_members()` with:

```text
StageDevelopmentSourceError: identity security_codes must be unique six-digit codes
```

A bounded read-only diagnosis replayed only the already-attempted cohort discovery and returned an empty candidate set (`count=0`, `unique=0`, `bad_count=0`). The 2018 daily bar, tradability-state, and trading-calendar partitions are physically present. Repository evidence already uses `2018-12-28` as the year-end market date. The preregistration had incorrectly supplied natural-year end `2018-12-31` to a cohort-discovery contract whose eligibility rule requires the final observed trading session to equal `candidate_cohort_date`.

## Exact correction

Only one preregistration field changes:

```text
source_isolation.candidate_cohort_date:
2018-12-31 -> 2018-12-28
```

Corrected preregistration digest:

`sha256:65dbe27838ce7721f5f3ca3e4a76f52b7a99cd7d819488e752e883adb5a05606`

Corrected execution digest:

`sha256:7e59a3fcadfab30369f66b214a82da1e2a38bae33e5e3c5dd80f35f7e7cd5e6f`

Retry authorization:

`config/stage-learning/positive-instability-quantitative-d1-retry.v1.json`

## Unchanged boundaries

The technical correction does **not** change:

- fourth-pool size `512`;
- D1 development identity capacity `256`;
- morphology-blind development target `160`;
- exact nine-candidate grid topology;
- development or evaluation gates;
- Stage-008 relocation guard;
- Stage-2 semantics or thresholds;
- preserved Stage-009 2021 evaluation plan or its digest;
- 2021 evaluation read state (`false`);
- 2022-2024 or 2025 access (`false`);
- semantic retry budget (`0`);
- automatic supplement rounds (`0`).

The original D1 budget marker remains immutable. A separate retry marker may be created once, only after the Worker verifies the exact parent failed receipt and the exact corrected preregistration. A third revision is rejected.

## Claim ceiling

This authorization proves only that the first failure was a calendar-endpoint engineering defect and permits one bounded technical retry. It does not claim any Stage-0 quality improvement, candidate eligibility, evaluation result, qualification, strategy activation, or trading authority.
