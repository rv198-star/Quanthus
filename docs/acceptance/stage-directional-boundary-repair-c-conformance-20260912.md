# P3-STAGE-008-C Conformance Acceptance

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-008-C |
| Governing design | `../design/stage-directional-boundary-repair.md` |
| Conformance contract | `../../config/stage-learning/directional-boundary-conformance.v1.json` |
| Conformance config digest | `sha256:defec6a0ec09f209e5608274febdf3a8c76f7907db4bc44c8c2a9061dff022bb` |
| Exposed source | P3-STAGE-007 successful prepare `stage_95f9b48bd96049b184e0cbf856b9a5e8` |
| New market-data read | 0 |
| Runtime Job | 0 |
| Parameter trial / threshold search | 0 / 0 |
| Result | `PASS_DIAGNOSTIC_CONFORMANCE_ONLY` |

## 1. Scope and claim ceiling

P3-STAGE-008-C checks whether the P3-STAGE-008-B implementation faithfully realizes the design that
was already frozen in P3-STAGE-008-A. It is not a new market qualification and is not allowed to
learn a Stage-0 threshold from the exposed Stage-007 cohort.

The run uses two evidence classes only:

1. synthetic/adversarial contract cases already frozen by A and implemented/tested by B;
2. the already exposed, immutable Stage-007 T-only review transport plus the frozen Stage-007 review
   ledger.

No 2022-2024 or 2025 partition was accessed. No new economic identity was selected. No candidate
threshold, weighted score or Stage-2 rule was created.

## 2. Read-only source binding

The conformance runner read the existing Stage-007 prepare through the controlled read-only
`stage_worker_status` transport:

- parent Job: `stage_95f9b48bd96049b184e0cbf856b9a5e8`;
- manifest: `sha256:0331ba6a82db7b4fb60cfbc03f328d899b7939c43936b414d9a4ffbce30b4db7`;
- evidence: `sha256:6a9df1869ff4f532715b212e41b9b6ada34d72835152d7318f4ac662acbf8dcf`;
- review ledger SHA-256: `sha256:493933e614fccd8c84e9066182f29b11ca80570396ea697d014337fda9504fd7`;
- case count: 160;
- page count: 20;
- controlled status reads: 21 including the page index;
- candidate output exposed by transport: false;
- future rows exposed by transport: false.

Raw market evidence was decompressed only in process memory. C did not publish or persist a new raw
market-data package.

## 3. Frozen Stage-007 diagnostic groups

The old frozen R0 Stage-0 predicate and frozen Stage-007 semantic ledger reproduce exactly:

- old Stage-0 true positives: **28**;
- old Stage-0 false positives whose definite reference is `NEITHER`: **12**.

These are the same groups used by the already approved A design diagnostic. They were not selected
again based on the new Fact values.

## 4. Exact diagnostic reproduction

The B implementation reproduces the A-frozen diagnostic medians to four decimal places:

| Diagnostic | Old Stage-0 TP | Directional FP / NEITHER |
| --- | ---: | ---: |
| absolute 20-session window return | 0.0444 | 0.2526 |
| 20-session range traversal | 0.3261 | 0.7695 |
| counter-direction excursion share | 0.6286 | 0.3272 |

The interpretation remains exactly the design claim: the exposed false positives are much more
consistent with endpoint repricing and materially less counter-directional than the true Stage-0
examples. These values do **not** define cutoffs. Stage-007 remains permanently design-exposed and
cannot qualify P3-STAGE-008-D.

## 5. Synthetic conformance

The implementation tests retain the A-frozen adversarial boundaries, including:

- monotonic rise and monotonic decline;
- complete up-down-up reversal ending at a range edge;
- zero-net direction;
- one-step repricing followed by flat trading;
- scale invariance;
- listing/coordinate/session-gap resets;
- missing primary input;
- recent 10-session contradiction that cannot mutate a committed primary Stage-2 opinion;
- mixed positive instability + positive relocation staying outside Stage 0;
- cross-role primary disagreement remaining ambiguous without majority or adjudication.

These are semantic/conformance tests, not empirical Stage accuracy claims.

## 6. Disposition

All P3-STAGE-008-C pass conditions are satisfied:

```text
P3-STAGE-008-C: CLOSED / PASS_DIAGNOSTIC_CONFORMANCE_ONLY
ALL 160 EXPOSED CASES READ: YES
FROZEN 28 / 12 GROUPS REPRODUCED: YES
FROZEN DIAGNOSTIC MEDIANS REPRODUCED: YES
SYNTHETIC DIRECTION CONTRACT: PASS
THRESHOLD SEARCH / PARAMETER TRIALS: 0 / 0
NEW MARKET READ / RUNTIME JOB: 0 / 0
STAGE-007 QUALIFICATION CLAIM: FALSE
STAGE-2 CHANGE: NONE
```

C therefore permits preparation of P3-STAGE-008-D under a separately frozen fresh-development
contract. It does not itself open or consume D data.
