# Structure Intelligence economic derived-lifecycle conformance repair (2026-09-06)

| Field | Value |
| --- | --- |
| Status | post-Run-A derived-field conformance repair; economic evidence and preregistration unchanged |
| Work item | `P3-STRUCT-002` |
| Governing preregistration | `structure-intelligence-economic-preregistration-20260906.md` |
| Frozen run-config digest | `sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de` |
| Result-bearing revision exposing defect | `17a34068ab1b07e7203eb03b6724a550e7f4453d` |
| Successful Run A | `job_9fa4eb2d715da397a928d74cf41109c6` |
| Run A result digest | `sha256:f9625cbd5d22dc8b395e8087624ed72a2d63a1c6ee7e88807dcee13eb041e9c3` |
| Run B on defective revision | `job_ef833a62adccedf8185ec58ae3facea9`; canceled after Run A exposed the conformance defect |

## 1. Immutable economic evidence observed

Run A successfully published and deep-verified all three complete-P2 variants. C0 exactly
reproduced the accepted native baseline (`0.06704618` return, `0.07102827` maximum drawdown).
The result package reported:

- C1-C0: return delta `-0.00270718`, drawdown delta `-0.00934550`, entry action-change rate
  `0.0002165908598657136668832575265`, `material=false`;
- C2-C0: return delta `-0.00986242`, drawdown delta `+0.00257879`, entry action-change rate
  `0.0002165908598657136668832575265`, `material=false`;
- C2-C1: return delta `-0.00715524`, drawdown delta `+0.01192429`, entry action-change rate `0`,
  `material=true`.

These values are immutable observed development counterfactual evidence and are not changed by this
repair.

## 2. Conformance defect

The preregistration explicitly orders the questions as:

1. **Primary:** C2-C1, coarse representation versus the same continuous facts;
2. **Secondary:** C1-C0 and C2-C0.

The implementation of `_lifecycle_decision()` incorrectly checked whether both secondary
comparisons were non-material **before** evaluating the primary comparison. Therefore Run A emitted
`no_material_structure_signal` even though its own C2-C1 record was `material=true` and C2 was worse
than C1 on both net return and maximum drawdown.

This is a contradiction between the frozen preregistration and a derived disposition field. It is
not an ambiguity in the economic results.

## 3. Correct preregistered decision order

The repair makes the executable lifecycle order match the already-frozen primary/secondary order:

1. If C2-C1 is material:
   - if C2 is weakly worse than C1 on both return and drawdown and strictly worse on at least one,
     emit `prefer_continuous_structure_facts`;
   - otherwise emit `coarse_state_increment_detected_requires_human_review`.
2. If C2-C1 is not material but either C1-C0 or C2-C0 is material, emit
   `continuous_facts_sufficient_no_material_coarse_increment`.
3. Otherwise emit `no_material_structure_signal`.

All four emitted values were already enumerated and authorized in the preregistration. No new
outcome is introduced after seeing Run A.

Applied to the immutable Run-A values above, the corrected derived disposition is
`prefer_continuous_structure_facts`: the C2-C1 difference is material, C2 return is lower than C1,
and C2 maximum drawdown is higher than C1.

## 4. Explicitly unchanged after result access

This post-result repair does **not** change:

- C0/C1/C2 score mappings or any score value;
- entry threshold, expert mapping, candidate population or candidate order;
- DWD / Structure ADM semantics or warm-up dates;
- 2020-2021 development window;
- capital, allocation, concentration, P2 matcher, costs, corporate actions or settlement;
- held-position management, reductions, exits or cooldown;
- primary/secondary comparison order, materiality thresholds or bootstrap method;
- any Run-A economic metric or artifact;
- sealed-partition restrictions, execution authority, Shadow authority, model authority or
  thirteen-state authority.

Because the published Run-A package contains the incorrect derived lifecycle field, it is retained
as immutable defect evidence and is not the final acceptance package. Run B on that revision was
canceled. A new exact revision must rerun A/B and reproduce the same economic evidence while
publishing the corrected preregistered disposition before `P3-STRUCT-002` can close.
