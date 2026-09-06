# Structure Intelligence economic experiment pre-result repair (2026-09-06)

| Field | Value |
| --- | --- |
| Status | pre-result implementation repair; economic preregistration unchanged |
| Work item | `P3-STRUCT-002` |
| Governing preregistration | `structure-intelligence-economic-preregistration-20260906.md` |
| Frozen run-config digest | `sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de` |
| Failed result-bearing revision | `54934086b54f9af3c50c305efbb59ee905561152` |
| Failed Run A | `job_f81ab2d081f7b7d2069c31981a7d2326` |
| Run B | `job_1536c397537ab297157fa67a17cbc83c`; canceled immediately after A failed |
| Economic result access | none; no immutable result package or `result_ref` was published |

## 1. Failure boundary

Run A held one normal Event Worker lease from start through child termination and failed closed with
`structure_mvp_child_exit_1`. It published no Structure-economic manifest, READY receipt, result
reference, comparison, lifecycle disposition, or economic metric. Run B had only just started after
A failed and was canceled before continuing the same known-defective revision.

The failure therefore exposed no C1/C2 economic result that could be used to select dates,
securities, score mappings, thresholds, variants, metrics, or lifecycle rules. The frozen run
configuration and its digest remain unchanged.

## 2. Discovered implementation defect

The experiment intervenes only on **entry scoring**. C0/C1/C2 share the same stable candidate-entry
population, but a changed entry can legitimately change which capital unit is held later, how long
it is held, and therefore which held-review decision identities exist downstream.

The first comparison implementation incorrectly required the complete decision-key set—including
held-review decisions—to be identical between capital-path variants. That would reject exactly the
portfolio divergence that a complete P2 counterfactual is intended to measure.

The corrected comparison pairs final-action changes only on the stable `entry:<event_id>` surface.
It still fails closed if the entry-candidate identities themselves differ. Downstream held/reduce/
exit differences are not discarded: their complete economic effects remain in P2 equity, drawdown,
tail loss, exposure, turnover, fees, fills, settlements, and final ledgers.

This is a comparison-population contract repair, not a score/threshold/data/strategy change.

## 3. Additional failure observability

The Event Worker previously recorded only `structure_mvp_child_exit_<code>` for a Structure child
failure. The repair adds a bounded failure detail using only the final non-empty line from at most
the last 8 KiB of child stderr, capped to the existing 500-character Job error boundary. It does
not publish partial research artifacts or economic results.

## 4. Frozen items explicitly unchanged

The following remain byte/semantically unchanged by this repair:

- `config/structure-economic-run.development.v1.json` and its preregistration digest;
- C0/C1/C2 variant definitions;
- continuous and coarse score mappings;
- `0.50` accepted formal entry threshold;
- 2020-2021 economic decision window and 2019-10-08 structure warm-up;
- candidate population and parent Event Run;
- trend-stability and position-risk semantics;
- capital units, allocation, concentration, P2 matcher, costs, corporate actions and settlement;
- held-position policy, reductions, exits and cooldown;
- primary `C2-C1`, secondary `C1-C0` / `C2-C0` ordering;
- materiality thresholds and 22-session / 2,000-resample paired bootstrap;
- sealed-partition prohibition and all authority ceilings.

## 5. Verification before rerun

Focused tests cover both the repaired stable-entry pairing and rejection of duplicate/drifting entry
identities, plus the existing formal-attribution compatibility path and bounded Worker failure
receipt. A new result-bearing revision may be created only after repository gates pass. Any rerun
remains bound to the original preregistration and is not permission for another same-work-item
parameter repair after economic results become available.
