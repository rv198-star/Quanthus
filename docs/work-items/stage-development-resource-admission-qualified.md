# P3-STAGE-014 — Admission-Qualified Era × Board Reservoir

Design status: approved  
Delivery gate: open for bounded admission-only engineering

Parent: `stage-development-resource-model.md`  
Machine design: `../../config/stage-learning/stage-development-resource-admission-qualified-model.v1.json`

## Goal

Preserve P013's Era × Board × exposure-aware resource model while moving input-readiness qualification ahead of semantic development. The successor must know that each selected 61-session resource can support the existing Structure fact contracts before any morphology, threshold, reference opinion or candidate output is computed.

This is not a larger random stock pool and not a retry of P013-D.

## Why P013-D closed

The sole P013-D Job bound the exact 256-resource plan and read only those declared 61-session windows. It failed before blind review because the frozen `PRE_STAR/MAIN` cell could not produce 24 complete cases from its 24 primary + 8 fallback resources. The fact builders require uninterrupted admitted history; a non-admitted session or coordinate/listing reset breaks the segment.

Because threshold-basis construction had already been reached in-process before the failure, all 256 P013-D exact windows are conservatively treated as L3-consumed for overlapping-window reuse. Whole identities are not globally burned; later preregistered disjoint-window development remains allowed.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-014-A | Freeze and run deterministic admission-only resource screen | P3-STAGE-013-D | closed `PASS_ADMISSION_CAPACITY_READY_FOR_QUALIFIED_PLAN`; sole Job `stage_bcaf8263cb724a2798d250909c42331f` found all 8 cells >=32, minimum early-STAR=36; no semantic computation; `../acceptance/stage-development-resource-admission-screen-engineering-20260914.md` |
| P3-STAGE-014-B | Freeze exact admission-qualified 8×32 plan | P3-STAGE-014-A | engineering accepted `PASS_ENGINEERING_READY_FOR_ONE_QUALIFIED_PLAN_JOB`; immutable admission-ledger only, no market read; `../acceptance/stage-development-resource-qualified-plan-engineering-20260914.md` |
| P3-STAGE-014-C | Freeze exact qualified 8×32 plan | P3-STAGE-014-B | 24 primary + 8 fallback per cell, global identity uniqueness, no cross-cell fill |
| P3-STAGE-014-D | Run atomic semantic development on qualified plan | P3-STAGE-014-C | same P012 9/1/3 candidate surfaces and numeric gates; blind review before scoring |
| P3-STAGE-014-E | Independent I/R/O selection and projection compatibility | P3-STAGE-014-D | preserved 2021 remains unread unless all frozen development gates pass |

## Admission-only boundary

The screen may inspect the exact inputs required to reproduce ordinary `DailyInput` admission and contiguous-history eligibility. It may retain only resource identity, window identity, pass/fail, bounded reason code and source binding digests.

It must not persist raw OHLC values or compute:

- Structure Trend/Order/Path facts;
- morphology features;
- quantiles or thresholds;
- reference opinions or labels;
- instability/order candidate outputs;
- candidate scores or Stage projections.

A qualified window must have all 61 declared venue sessions admitted, a stable listing/coordinate segment and contiguous session indices through T, with T fixed to the window end.

## Candidate ordering

Candidate order is frozen from metadata and exposure only. Each cell considers at most 256 unique candidate identities (`min(256, available identities)`) and at most four deterministic temporally spread windows per identity. P013-D's exact consumed identity-windows are excluded from overlap; the same identity may appear only through a disjoint window.

The screen can stop once a later frozen plan has enough qualified resources, but semantic outcomes never influence the order or stop condition. If a cell cannot produce 32 qualified unique identities within the frozen candidate budget, Stage 014 stops without opening semantic development.

## Claim ceiling

The qualified sample is a balanced semantic stress sample conditional on technical input readiness. It is not an estimate of A-share board prevalence, suspension frequency, investability, economic value or production suitability.

Preserved 2021 remains globally protected and unread. 2022-2024 remain closed. 2025 remains permanent blind. No daily, strategy or trading authority is opened.
