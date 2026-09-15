# P3-STAGE-018 — Instability Channel Decomposition Diagnostic

Design status: approved  
Delivery gate: accepted diagnostic closeout

Parent: `stage-stratum-normalized-instability.md`  
Machine contract: `../../config/stage-learning/stage-instability-channel-decomposition.v1.json`
Upstream review: `../reviews/stage-p017-stratum-normalized-no-pass-four-party-review-20260915.md`

## Goal

Explain the valid P017 no-pass without adding thresholds, labels or candidates. Reuse the exact P017
252-case evidence/reference surface and exact nine cell-local profile instances, but measure the two
already-existing internal instability channels separately:

- `expanding_disorder_20`;
- `broad_reversal_disorder_20`;
- their overlap;
- their existing OR composition, which is the parent P017 prediction.

P018 is diagnostic only. It cannot select a successor mechanism or qualify a candidate.

## Frozen boundaries

- Exact P017 recovery artifact manifest, basis digest and profile-grid digest are immutable inputs.
- Exact 252 development case ids and immutable P014/P015 reference ledgers are reused.
- Exact nine P017 profile identities and all persisted cell-local thresholds are reused byte-semantically.
- No new profile, threshold, quantile, feature, identity, case or reference label is introduced.
- Channel outputs are compared only with the existing parent `structural_instability_20` reference.
- The Job publishes pooled, equal-weight Era×Board macro and per-cell metrics plus channel overlap / false-positive attribution.
- No eligibility gate, profile selection or successor qualification occurs inside the Job.
- No DWD, fixed relocation, order, projection, preserved 2021, 2022–2024 or 2025 read occurs.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-018-A | Freeze diagnostic artifact closure and channel semantics | P3-STAGE-017 | closed `PASS_CHANNEL_DECOMPOSITION_ENGINEERING_FREEZE`; exact P017 grid/bases and parent reference closure, no new semantics |
| P3-STAGE-018-B | Execute channel-decomposition diagnostic | P3-STAGE-018-A | closed `PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE`; sole Job `stage_4e8eedc28b5542c798a8b33c8276755f`, no selection or new labels; `../../config/stage-learning/stage-instability-channel-decomposition-result.v1.json` |
| P3-STAGE-018-C | Four-party attribution review | P3-STAGE-018-B | closed `PASS_RECOMMEND_FRESH_BLIND_MECHANISM_REFERENCE_PILOT`; broad-only is the dominant FP contributor while expansion is high-precision / low-coverage |
| P3-STAGE-018-D | Close and decide whether mechanism-specific references are justified | P3-STAGE-018-C | closed `RECOMMEND_P019_FRESH_BLIND_MECHANISM_REFERENCE_PILOT`; preserved partitions remain unread |

## Interpretation

P018 may reveal that one channel dominates false positives, that one channel contains most true
positives, that overlap is the useful region, or that neither decomposition gives a clean separation.
None of those observations automatically creates a new predicate. A later work item must explicitly
define and blind-review any mechanism-specific semantic reference before it can become a candidate.

## Claim ceiling

P018 can establish only attribution of the existing frozen P017 prediction errors to its already-
defined expansion and broad-reversal channels on the immutable development set. It cannot establish a
new semantic state, preserved validation, economic value, strategy value, production suitability or
trading authority.
