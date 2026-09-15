# P3-STAGE-017-B-R1 Technical Recovery Acceptance

Status: `PASS_AUTHORIZE_ONE_EXACT_FIELD_PATH_RECOVERY`

## Failed parent

The sole initial P017-B Job `stage_f670ede6347e4bd5921b66e61a034a9d` on revision
`10b998766acd72e27fa46b9ecd3fff4ecaf6a556` failed with
`StratumNormalizedInstabilityError: reference_result_shape` and published no artifact.

The failure occurred after immutable evidence/basis construction and reference-ledger binding, but
before `_score_profiles(...)` was entered. Therefore no P017 profile was scored, no candidate was
selected, and no semantic P017 pass/fail result exists from the failed parent.

## Root cause

The runtime attempted to read a nonexistent field:

`p015_result['p012_atomic_reference_resolution']`

The already-frozen P015 durable result stores the same reference-resolution object under:

`p015_result['combined_reference_resolution']`

This is an implementation binding-path defect, not a resource, normalization, candidate, gate or
reference-content defect.

## Recovery authority

Machine authority:
`../../config/stage-learning/stage-stratum-normalized-instability-recovery.v1.json`

Exactly one new-revision recovery Job is permitted. It must keep exact:

- the original P017 config and 252-case evidence/reference surface;
- both immutable review ledgers;
- the eight Era × Board cell-local unlabeled normalization contract;
- the fixed 3×3 profile coordinates;
- the unchanged P012-derived quality gates;
- envelope retirement from P017 eligibility;
- all protected-data boundaries.

The only admitted code repair is switching the reference-resolution field path to the already-frozen
`combined_reference_resolution` field. A third revision is rejected.

## Verification before recovery

Focused runtime/Worker/contract tests must pass, Ruff and `git diff --check` must pass, and the full
repository `make check` must pass before the recovery revision is committed, pushed and deployed.

## Claim ceiling

This acceptance authorizes one technical recovery only. It does not imply P017 semantic success,
permit profile/gate tuning, reopen P016, or authorize preserved 2021 / 2022-2024 / 2025 reads.
