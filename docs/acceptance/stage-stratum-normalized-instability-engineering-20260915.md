# P3-STAGE-017 Stratum-Normalized Instability Engineering Acceptance

Status: `PASS_ENGINEERING_READY_FOR_ONE_STRATUM_NORMALIZED_INSTABILITY_JOB`

## Scope

This acceptance covers only the result-free P017 engineering freeze. It does not claim that a P017
candidate passes. P017 is a new development experiment after the valid P016 semantic no-pass; it is
not a retry or reinterpretation of P016.

## Frozen development surface

P017 is artifact-only. It binds the exact immutable P015/P016 lineage:

- 252 existing development cases and Era × Board strata;
- P014 192-case sealed review ledger;
- P015 60-case sealed review ledger;
- unchanged P012 instability profile coordinates and absolute numeric quality gates;
- preserved 2021 remains `UNREAD`; 2022–2024 and 2025 remain unopened.

No DWD market read, new identity discovery, random supplementation, case replacement, rereview or
reference rewrite is admitted.

## Result-free normalization contract

Before either review ledger is opened by the runtime, it must:

1. reconstruct the 252 already-published reviewer-visible cases;
2. partition them by the eight frozen Era × Board cells;
3. build an unlabeled quantile basis independently inside every cell;
4. instantiate the same fixed P012 3 × 3 expansion/broad profile coordinates against every cell basis;
5. freeze the complete 9-profile × 8-cell threshold surface.

Label/reference visibility during basis construction is forbidden. Empty required unlabeled
subpopulations fail closed before scoring; there is no cross-cell fallback or post-result basis
change.

## Envelope disposition

P016 observed zero incremental TP and zero incremental FP from the fixed synthetic close-envelope
additive channel in all nine trials. P017 therefore removes that mechanism from successor eligibility.
It does **not** lower the P012 envelope-specific incremental-TP number to zero, and it does not search
for a replacement envelope threshold. The mechanism-specific gate is structurally not applicable in
P017 because the mechanism itself is absent.

The remaining absolute instability gates keep the P012 values: pooled minimum predictions 18,
Era × Board macro precision >=0.78, macro positive-reference coverage >=0.70 and macro definite-
negative FPR <=0.10.

## Runtime and failure semantics

The admitted Worker kind is `quanthus.stage-stratum-normalized-instability.v1` with exactly one Job,
zero automatic retries and a 900-second wall budget. Same-revision submission is idempotent; a
different revision has no retry/replacement authority.

P017 scores instability only. It cannot score fixed relocation, order profiles or projection and
cannot read preserved evaluation. A no-pass is a semantic result and must not trigger profile/grid,
quantile or gate changes inside P017.

## Verification

Focused Worker/helper/module/document contracts must pass, Ruff and `git diff --check` must be clean,
and the repository-wide `make check` must pass before this checkpoint is committed and pushed.

## Claim ceiling

This engineering acceptance authorizes only one bounded artifact-only P017 development scoring Job.
It does not establish candidate quality, projection compatibility, preserved out-of-time validity,
market prevalence, economic value, strategy value, production suitability or trading authority.
