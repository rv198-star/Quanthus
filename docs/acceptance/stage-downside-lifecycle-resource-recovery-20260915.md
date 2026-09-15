# P3-STAGE-020-B-R1 Pre-Admission Capacity Artifact Recovery

Design status: accepted technical recovery  
Delivery gate: ready for exactly one recovery Job

## Failure binding

The original P020-B Job `stage_5b87f865e36c4ce0af09f6e9bd4e8daa` at revision
`2fd8a71003bdc993943e2f264a6692d3c4198633` failed before admission-market reads and before any
Stage1/12 semantic work. The failure was:

```text
DevelopmentResourceAdmissionScreenError: candidate_cell_missing
```

The error occurred in the reused strict P014 candidate-surface helper after P020-B had applied its
frozen exact-window exclusions. At least one Era × Board cell had no remaining metadata candidate.
That is a legitimate resource-capacity outcome; the implementation defect was failing to persist it
as a governed artifact.

## Authorized repair

One technical recovery is authorized only to add a metadata preflight before the strict admission
helper. If any cell has no remaining fresh exact-window, the recovery must publish:

```text
CLOSED_FRESH_WINDOW_CAPACITY_SHORTFALL_BEFORE_ADMISSION
```

and stop before 61-session admission, price/fact semantics, review opinions, thresholds or candidate
scoring.

The recovery may not:

- change the P020-B config;
- change the 8×32 / 24+8 quota;
- add years, boards or identities;
- reuse P013 exact-plan windows;
- reuse any exact window read by the P014 admission screen;
- burn whole identities instead of exact windows;
- read preserved 2021, 2022-2024 or 2025;
- open Stage1/12 review or candidate work.

## Verification

Focused recovery verification:

```text
122 passed
Ruff PASS
git diff --check PASS
```

Tests prove the preflight shortfall produces a valid no-market-read artifact, the original failed Job
is exact-bound, only one different-revision recovery is possible, and a third revision is rejected.
A repository-wide `make check` remains required before the recovery checkpoint is committed.

## Claim ceiling

This recovery can establish only the exact fresh-window resource capacity remaining under the already
frozen P020-B exclusions. It cannot justify relaxing those exclusions or infer anything about Stage12
or Stage1 semantics.
