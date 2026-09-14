# P3-STAGE-013-C successor atomic-first preregistration acceptance

## Disposition

`PASS_EXACT_PLAN_AND_SUCCESSOR_PREREG_FROZEN`

This acceptance closes the resource-planning portion of P3-STAGE-013-C before any successor result-bearing market read.

## Exact plan binding

The immutable metadata plan is bound to recovery Job `stage_060f6edc80774b6b802e56b6c21025bc` at revision `2cd26b00a90402c7be3256dfb3d341dc3b9abe18`.

- manifest: `sha256:853ade679e1107db238ec68daa8a26755e1d48ef334b9b85d525a8fcd0a3adef`
- plan: `sha256:2aff2d250cb58baae9775b23cc53d2f0ac073f3408af577e17102f67cb54f371`
- identities: `sha256:63c82ab26784bb2407ae76e0f42ed5685b29ca110727d26b20b510e7e4bc7c80`
- 256 rows / 256 globally unique economic identities
- eight structurally present era x board cells, 32 reserves per cell
- 24 primary plus eight pre-frozen fallback rows per cell, for 192 primary plus 64 fallback rows

The original failed plan Job remains retained. R1 changed only the metadata temporal-spread allocator from a rigid near-equal window slot requirement to capacity-constrained max-min waterfill. Cell quotas, global identity uniqueness, seeds, candidate surfaces and blind boundaries were not relaxed.

## Successor question

`config/stage-learning/stage-atomic-proposition-successor-development-screen.v1.json` freezes the next result-bearing development question.

The successor keeps the P012 atomic question intact:

- exactly nine enhanced instability candidates;
- one fixed, non-selectable P008 relocation guard;
- exactly three order profiles;
- zero joint-pair search;
- P012 numeric atomic quality gates unchanged;
- P012 resolver and projection compatibility numeric gates unchanged;
- thresholds are recomputed from the new unlabeled development basis rather than copied from P012;
- no post-result candidate/profile expansion or reselection.

The only deliberate analysis change is the P013-B pre-result sampling decision: primary aggregate metrics are macro equal-weighted across the eight era x board cells, while pooled micro metrics are descriptive. This prevents MAIN from dominating simply because it has more identities.

## Input and fallback rules

Only the exact identity-window rows may be read. No identity history outside its frozen 61-session window is authorized.

Each cell targets 24 complete review cases. Primary rows are attempted before the cell's pre-frozen fallbacks. A fallback can be used only for input-admission unavailability and only inside the same cell. No cross-cell fill, post-result identity replacement, supplement or random append is authorized.

Case T is selected deterministically only among input-complete dates after the primary 20-session history. Selection cannot use morphology values, labels/reference opinions or candidate outputs.

## Blind boundary

At this freeze point:

- successor market read: not yet authorized;
- candidate output / threshold basis visibility during review: forbidden;
- preserved 2021: unread;
- 2022-2024: closed;
- 2025: permanent blind closed;
- daily/strategy/trading: closed.

Preserved 2021 can be opened only after development sufficiency, independent I/R/O quality and projection compatibility all pass and the exact I/R/O choices are frozen.

## Next gate

P3-STAGE-013-D may now implement a bounded one-shot runtime against this preregistration. Runtime engineering must pass repository verification and be committed/pushed before any result-bearing Job is submitted.
