# P3-STAGE-013-D input-admission failure review

Design status: approved review disposition  
Delivery gate: P3-STAGE-013-D closed before blind review

This is a same-origin simulated four-perspective review. It is not independent human or external-model validation.

## Evidence reviewed

- Exact P013-C metadata plan: 256 globally unique identities, eight Era × Board cells, 32 resources per cell, 24 primary + 8 same-cell fallback.
- Sole P013-D result-bearing Job: `stage_71fe94875bbe428fb464ffc6017af313`, revision `72a961aa84c23c43e1daf5fe4febe215400ad84f`.
- Terminal failure: `cell_complete_case_shortfall:PRE_STAR/MAIN`.
- No governed artifact was published and no blind-review ledger or candidate scoring was reached.
- The runtime source order shows that exact-window market data and Structure fact construction were reached before the complete-case check, and threshold basis/candidate-grid construction occurred in-process before the terminal shortfall. No values from those unpublished objects were consumed by the reviewer or user.
- Preserved 2021, 2022-2024 and permanent-blind 2025 remained unread.

## Short-term trading perspective

A 61-session Structure window is not equivalent to 61 usable trading observations for an individual stock. Suspensions, missing bars and corporate-action coordinate resets are economically common enough in older A-share history that requiring every frozen identity-window to remain one uninterrupted admitted segment can remove a material fraction of a cell.

The failure should not be repaired by adding random replacement names after seeing semantic results. However, input usability is a legitimate pre-semantic sampling dimension. A trader would rather know before semantic review that the packet has a complete observable path than silently let older-main-board names disappear and distort the intended Era × Board balance.

The successor should therefore qualify input readiness before semantic case publication, while retaining an explicit warning that this creates a continuously-observable subpopulation rather than an estimate of market prevalence.

## Fund-manager perspective

The original equal-cell design remains valuable because market era and listing board are structural context. The failure does not justify collapsing back to a pooled A-share sample dominated by the main board.

At the same time, filtering for uninterrupted data can favor more continuously traded securities. That bias must be visible. The successor should report, by Era × Board cell, how many candidate identities/windows were examined, how many were rejected for input-readiness reasons and how many qualified. The resulting semantic sample can support balanced structural stress testing, but not prevalence or investability claims for the full market.

The 24 complete cases per cell target should remain unchanged unless a new pre-result review explicitly re-derives it. The current evidence does not justify weakening the semantic denominator.

## Quantitative research perspective

The critical separation is between three surfaces:

1. metadata resource eligibility;
2. input-admission / contiguous-history eligibility;
3. semantic morphology, threshold, reference and candidate results.

P013 froze (1) and attempted to discover (2) only inside the semantic Job. That ordering caused the experiment to consume result-bearing market inputs and construct a threshold basis before learning that one cell could not satisfy its complete-case denominator.

The successor should insert a deterministic admission screen between (1) and (3). Candidate identity/window order must be frozen before the screen. The screen may evaluate only the conditions required for the ordinary Structure fact builders to have a contiguous T history: admitted daily inputs, stable listing/coordinate segment and exact session continuity. It must not calculate Structure facts, morphology, thresholds, labels, reference opinions or candidate outputs.

Stopping after a preregistered number of admission-qualified resources is acceptable because the stopping variable is input readiness, not semantic outcome. Candidate ordering and maximum scan budget must be frozen first. Any semantic Job must consume only the subsequently frozen qualified plan.

## Data and evidence governance perspective

The failed P013-D run is not reusable as a fresh experiment. All 256 frozen exact windows were market-read; because threshold-basis computation was reached before the terminal failure and the exact contributing subset was not durably published, conservative exposure accounting should mark all 256 windows as L3-consumed for overlapping-window reuse. This avoids a second diagnostic read merely to reclaim capacity.

This does not require whole-identity burn-down. The accepted exposure-aware model still allows the same economic identity to participate later through a preregistered disjoint window, provided the current batch counts an identity only once and does not claim cross-run identity independence.

The admission screen should publish only immutable identity/window bindings, pass/fail status, bounded reason codes, counts and source digests. Raw OHLC values should not be copied into the admission artifact. Protected 2021 and 2025 surfaces remain closed.

## Joint disposition

**CLOSE_P013_D_PRE_REVIEW_INPUT_ADMISSION_SHORTFALL_AND_OPEN_P014_ADMISSION_QUALIFIED_RESERVOIR**

The four perspectives agree on the following successor rules:

- keep eight Era × Board semantic cells and 24 complete cases per cell;
- do not change the frozen P012 atomic candidate surfaces or numeric gates;
- do not rerun P013-D or enlarge its fallback pool after the observed failure;
- conservatively exclude the 256 P013-D exact identity-windows from overlapping reuse;
- build a new deterministic candidate order from remaining exposure-aware identity-windows;
- perform an admission-only screen before any Structure morphology/threshold/reference/candidate computation;
- allow same economic identities only on disjoint windows under the accepted exposure model, with one selected window per identity in the new scored batch;
- freeze the new qualified 24+8 per cell plan before the next semantic market run;
- report admission attrition per cell and never reinterpret the complete-case sample as market prevalence.

This review does not authorize preserved-2021 access, 2022-2024 access, 2025 access, strategy/daily activation, trading, candidate retuning or gate relaxation.
