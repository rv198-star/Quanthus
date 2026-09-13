# P3-STAGE-011-D1 Transport Recovery — 2026-09-13

## Disposition

```text
FIRST D1 JOB: FAILED / IMMUTABLE
FAILURE CLASS: PROTOCOL-BUDGET CONFORMANCE DEFECT
SEMANTIC RESULT: NONE
GOVERNED ARTIFACT: NONE
RECOVERY: ONE BOUNDED TECHNICAL TRANSPORT RECOVERY AUTHORIZED
AUTOMATIC RETRY / SUPPLEMENT: NOT AUTHORIZED
PRESERVED 2021: UNREAD
```

The first P011 D1 Job is retained exactly as failed:

- Job: `stage_e73f7197d1f34b879d8b31e78db800ce`;
- revision: `e91dfbf77e9d62b66a2d11cf064b3fb0538331ec`;
- input digest: `sha256:4a3dfd5da5da9b08c27ff7564ab725d5e968358568dcc2ce9b25e38049545a95`;
- submitted: `2026-09-13T06:09:27.539946+00:00`;
- completed: `2026-09-13T06:11:32.941284+00:00`;
- terminal state: `failed`;
- Worker error: `stage_orderly_consolidation_joint_d1_process_failed`;
- result reference: none.

The process reached the already-frozen 2018 evidence construction and then failed during independent
evidence validation with:

```text
OrderReferenceError: case_budget
```

No governed D1 artifact was published.

## Root cause

The signed 2018 evidence protocol freezes:

```text
max_cases = 192
max_source_rows = 20000
max_artifact_bytes = 33554432
```

`build_evidence()` already consumed the signed protocol `max_cases`, so it could construct a bounded
review denominator above the historical 160-case size. `validate_evidence()` did not consume the same
signed bound: it retained a legacy literal `len(cases) <= 160`. It also retained a literal source-row
ceiling instead of consuming the protocol's declared source-row bound.

Therefore the builder and cold validator implemented different budget contracts. This is an
implementation-conformance defect in the shared evidence mechanism, not evidence that the 2018 cohort,
Stage0/Stage2 semantics, sampling, thresholds, candidate-pair surface or reference protocol failed.

The shared repair makes the validator consume the already-signed protocol bounds for case and source-row
budgets. Evidence-size construction also consumes the signed artifact-byte budget while preserving the
existing global absolute byte ceiling. Default v1 and the 2019 P010 scope retain their existing numeric
budgets; no frozen protocol value changes.

## Exposure boundary

The failed Job did read only its authorized `2017-09` warm-up plus `2018` development scope before the
post-build validator failed. That read remains part of immutable failure history.

Before recovery:

- governed artifact published: false;
- development Primary opinions: 0;
- candidate-pair scores: 0;
- candidate pair frozen: false;
- threshold values exposed to reviewer: false;
- preserved 2021 evaluation read: false;
- P010 2019 development reread: false;
- P009 2020 development reread: false;
- 2022-2024 read: false;
- 2025 read: false.

No sample, review, threshold or candidate information from the failed in-memory run is used to change the
frozen experiment.

## Recovery authority

Machine authorization:
`config/stage-learning/orderly-consolidation-joint-d1-transport-recovery.v1.json`

Digest:
`sha256:a2bbd496830adda80c13bf9a1d61baa0cf3dabcf6439cc53efcc45f6d5f11b2e`

Exactly one new-revision technical recovery is authorized only after the Worker verifies the exact failed
Job binding above. It must retain exactly:

- preregistration digest `sha256:c9b5eff892f3d731170afac052e3810543f7444f5b6025bdef24c1ebfa4c213e`;
- execution digest `sha256:3424f673f11af42d37a454429fb9e624978b18799d10fea21c365f81c0c0d680`;
- 2018 evidence-scope digest `sha256:2400ccd1c025098b0a3d050aebf39cc0ebc7241289d9db2e8425819f9daf76c9`;
- fifth fresh-pool identity exclusions and all sampling seeds;
- 192 planned blind review cases;
- exact 9 Stage0 × 3 Stage2 = 27 candidate-pair surface;
- all development/evaluation gates;
- exact preserved 160-identity 2021 plan.

The recovery does not count as a second semantic/result-bearing experiment because the first Job produced
no governed result and the repair changes only shared evidence budget conformance. This exception is
explicit and one-shot; it is not an automatic retry policy.

If the recovery fails, no further technical retry, supplement or semantic rerun is authorized by this
record. A new decision would be required. D2 scoring remains closed until a valid D1 artifact and a sealed
candidate-blind review ledger exist. Preserved 2021 remains closed until at most one pair is frozen.
