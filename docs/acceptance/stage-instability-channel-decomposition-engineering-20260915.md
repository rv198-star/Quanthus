# P3-STAGE-018 Channel Decomposition Engineering Acceptance — 2026-09-15

Status: `PASS_ENGINEERING_READY_FOR_ONE_CHANNEL_DECOMPOSITION_JOB`

## Accepted scope

P018 is an artifact-only diagnostic successor to the valid P017 semantic no-pass. It replays the exact
P017 nine-profile / eight-cell thresholds over the same immutable 252 reviewer-visible cases and
parent `structural_instability_20` references, exposing expansion-only, broad-only, overlap and parent-
OR attribution metrics. It does not select or qualify candidates.

## Binding and authority checks

- P017 config and P017 durable no-pass result are SHA-bound.
- P017 recovery artifact manifest, basis digest and profile-grid digest are exact-bound.
- P014/P015 evidence artifacts and review ledgers are reached only through the frozen P017 bindings.
- Profile reconstruction must reproduce every persisted P017 cell candidate exactly.
- No threshold/quantile/profile/reference change is authorized.
- No DWD, R/O/projection, preserved 2021, 2022–2024, 2025, daily/strategy or trading authority exists.
- Worker execution budget is one Job with zero automatic retries.
- RX6800 control remains limited to the named `submit-p018-b` helper command.

## Verification

Focused tests cover diagnostic-only config authority, overlap attribution and nine-profile three-view
channel replay. Worker/control-surface tests cover one-shot/idempotent submission, runtime identity,
doctor failure visibility and named helper admission.

Repository-wide `make check` must pass before the execution checkpoint is committed/pushed. Runtime
results are not accepted by this engineering document; P018-B and later review/closeout own them.

## Claim ceiling

This acceptance establishes only implementation readiness for the one preregistered P018 diagnostic
Job. It does not establish any diagnostic outcome or authorize a mechanism-specific successor.
