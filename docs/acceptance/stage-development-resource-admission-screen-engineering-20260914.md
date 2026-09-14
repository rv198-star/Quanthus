# P3-STAGE-014-A Admission Screen Engineering Acceptance

Design status: approved  
Delivery gate: accepted for exactly one admission-only Job

## Disposition

**PASS_ENGINEERING_READY_FOR_ONE_ADMISSION_ONLY_JOB**

P3-STAGE-014-A implements the pre-semantic input-readiness layer selected after P013-D closed on `PRE_STAR/MAIN` complete-case admission shortfall. It is a new bounded screen, not a retry or supplement of P013-D.

## Frozen input surface

The screen binds the P014 admission-qualified resource model, P013-D closeout, P013 capacity contract/result, P013 exact-plan result, training-node source binding, and the immutable successful P013 exact-plan artifact.

Before any input-readiness market read it freezes:

- eight Era × Board cells;
- deterministic identity ordering with at most 256 candidate identities per cell;
- deterministic temporally spread window ordering with at most four windows per identity;
- exact exclusion of all 256 P013-D consumed `(economic_identity, window_id)` resources;
- preserved-2021 identity-global exclusion;
- the requirement for at least 32 qualified unique identities in every cell before a successor semantic plan can freeze.

## Admission-only boundary

The implementation uses the existing governed Development reader only to reconstruct whether each exact 61-session identity-window satisfies the ordinary DailyInput admission and contiguous segment conditions. Qualification requires:

- exactly the declared 61 sessions through T;
- T equals the frozen window end;
- all 61 session attempts are admitted;
- one stable economic/listing identity;
- one stable coordinate reference with no reset;
- contiguous session indices.

The governed artifact contains only identity/window bindings, deterministic ranks, qualified boolean, bounded reason code and source-binding digest plus aggregate receipts/counts. It does not persist raw OHLC/volume values.

The screen does **not** build Trend/Order/Path Structure facts, morphology, threshold/model basis, reference opinions, labels, candidate outputs, candidate scores or Stage projections.

## Runtime budget

- Worker kind: `quanthus.stage-development-resource-admission-screen.v1`
- maximum Jobs: 1
- automatic retries: 0
- wall limit: 2700 seconds
- helper surface: restricted `submit-p014-a` only; no generic submit/shell/cancel expansion
- the stable root launcher dynamically delegates to the Git-controlled repo helper, so this helper addition requires no new root installation.

If the Job fails technically, no second submission is authorized without a separately frozen technical recovery contract. If it succeeds but any cell has fewer than 32 qualified unique identities, P014 stops before qualified-plan freeze and before semantic development.

## Protected surfaces

- P013-D is not rerun or supplemented.
- P013-D's 256 consumed exact windows remain excluded from overlapping reuse.
- P012 atomic candidate surfaces and numeric gates are unchanged.
- preserved 2021 remains unread.
- 2022-2024 remain closed.
- 2025 remains permanent blind.
- no daily, strategy or trading authority is opened.

## Verification

Focused admission/Worker/helper/governance tests pass before this acceptance. Full repository `make check`, Ruff and `git diff --check` are required immediately before the engineering checkpoint is committed and pushed.

This acceptance grants only the one admission-only Job. It does not qualify the sample, any atomic proposition, any Stage projection, market prevalence or economic value.
