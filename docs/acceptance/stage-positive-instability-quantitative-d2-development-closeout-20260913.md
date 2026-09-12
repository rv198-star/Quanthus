# P3-STAGE-010-D2 Development Closeout — 2026-09-13

## Disposition

```text
D2 DEVELOPMENT RESULT: REVISE_OR_PARK_DEVELOPMENT_FAIL
SEALED DEVELOPMENT CASES: 160
SEALED PRIMARY OPINIONS: 320
EXACT CANDIDATE TRIALS: 9
ELIGIBLE CANDIDATES: 0
CANDIDATE FROZEN: FALSE
PRESERVED 2021 EVALUATION READ: FALSE
POST-RESULT GRID EXPANSION: FALSE
```

P3-STAGE-010 therefore stops before D3. The preserved 2021 evaluation plan remains unread and cannot
be opened by this work item.

## 1. Review was sealed before scoring

D2 consumed the independently verified D1 artifact from
`stage_8f2bc43d4b1e4943bca4c3e31214c8a5`. All 160 Primary cases were reviewed under the frozen
short-term/fund same-origin protocol before any candidate or threshold-basis visibility.

The ledger was sealed first at:

`sha256:e9d93b43903f8753ad8d7f8859493b8a279ec6aad4101856ebeb4d2bcb5e204e`

with 19 atomic cross-role disagreements. After Primary projection, 18 cases remain cross-role
`AMBIGUOUS`; one atomic disagreement resolves to the same final Primary status in both roles. Final
reference counts are:

```text
STAGE_0    54
STAGE_2    16
NEITHER    72
AMBIGUOUS  18
```

No review opinion was changed after scoring began.

## 2. Exact nine-candidate scoring

The D1 threshold basis and candidate grid were checked against their immutable artifact digests before
scoring:

- threshold basis: `sha256:97c53f1551bf62a0b444a523bb57689ae44f5c918f0ad15774ca0ace6ea3233e`;
- candidate grid: `sha256:2781679a81a96a5c4adaa488ee555ec13ad53b0361a712b77050e04048fe6332`.

Reconstructing the grid from the frozen threshold basis reproduced the D1 candidate-grid payload
exactly. Exactly nine candidates were scored; no candidate was added, replaced or retuned.

The frozen P3-STAGE-008 baseline on this new denominator is:

| Metric | Baseline |
| --- | ---: |
| TP | 36 |
| FP | 12 |
| FN | 18 |
| Precision | 0.750000 |
| Positive coverage | 0.666667 |
| Definite-negative FPR | 0.136364 |

The full nine-trial digest is:

`sha256:39b6262c3ae6b570a821a8ffbb8efe96ff2da2e5884df5d5f4ec340fad5d0a24`.

## 3. Near-pass frontier

Two candidates expose the decisive trade-off.

### A. expansion-lenient × broad-lenient

Candidate:
`sha256:d906419b59468e8fb480d0320f7d99bad4f409092895189794cfc31ebaaf813e`

| Metric | Observed | Gate |
| --- | ---: | ---: |
| TP | 38 | — |
| FP | 1 | — |
| Precision | **0.974359** | >= 0.78 PASS |
| Coverage | **0.703704** | >= 0.70 PASS |
| FPR | **0.011364** | <= 0.10 PASS |
| Stage-0 predictions | 39 | >= 15 PASS |
| Candidate-instability × frozen-Stage2 overlap | **2** | = 0 **FAIL** |

It also improves precision and FPR versus the frozen baseline, loses no positive coverage versus that
baseline, and removes more than one baseline false positive. The **only** frozen gate it fails is the
Stage0/Stage2 non-overlap requirement.

### B. expansion-lenient × broad-balanced

Candidate:
`sha256:3f29aa90f71717c07fa0f1c0fb5301d4b7b0e8447d1a097dbaaa8d4920974f5c`

| Metric | Observed | Gate |
| --- | ---: | ---: |
| TP | 34 | — |
| FP | 0 | — |
| Precision | **1.000000** | >= 0.78 PASS |
| Coverage | **0.629630** | >= 0.70 **FAIL** |
| FPR | **0.000000** | <= 0.10 PASS |
| Candidate-instability × frozen-Stage2 overlap | 0 | = 0 PASS |

This profile removes the Stage2 overlap but loses four Stage0 true positives needed to reach the 0.70
coverage floor.

## 4. Root cause is now narrower than P3-STAGE-009

The consumed P3-STAGE-009 failure was a shared-core bottleneck that broadly suppressed positive
coverage. P3-STAGE-010 successfully removed that failure mode: the lenient/lenient candidate reaches
0.974 precision, 0.704 coverage and 0.011 FPR.

The remaining failure is concentrated in a narrow broad-range boundary. The four Stage0 references
rescued by `broad-lenient` have `range_width20` between approximately **0.1548 and 0.1660**. The two
Stage2-overlap cases have widths **0.16271 and 0.16275**. The preregistered broad floors are:

```text
broad-lenient   0.1498501498501498501498501499
broad-balanced  0.1703601108033240997229916898
```

So the decisive six cases occupy the same mid-width band. A broad-width floor alone cannot retain all
four rescued Stage0 positives while excluding both observed overlaps.

More importantly, one overlap case (`index=65`, security `300155`, 2019-04-26) is unanimously judged
`STAGE_0` by both Primary roles while the frozen numeric `unchanged_stage2()` predicate also returns
true. Its path has extremely low traversal (`0.0548`) and very high counter-direction excursion
(`0.9452`), direct evidence of two-sided disorder. The old Stage2 predicate does not consume either
path-geometry fact; it uses only low efficiency, `range_width <= 0.18`, contraction and small center
shift.

The second overlap case is reviewed `NEITHER`, yet the frozen Stage2 predicate also returns true. The
fresh evidence therefore exposes a **Stage0/Stage2 truth-boundary conflict**, not another broad failure
of the new Stage0 projection.

Machine diagnostic:
`config/stage-learning/positive-instability-quantitative-development-failure-diagnostic.v1.json`

## 5. Stop rule and claim ceiling

Because `eligible_count=0`, the preregistered stop rule is mandatory:

- no candidate freeze;
- no D3 evaluation preparation;
- no preserved 2021 market-data read;
- no tenth candidate or profile interpolation;
- no review-ledger revision;
- no threshold relaxation;
- no P010 Stage2 repair;
- no relocation-guard retune;
- no 2022-2025 access;
- no official label, strategy, daily or trading authority.

P3-STAGE-010 closes as **`REVISE_OR_PARK_DEVELOPMENT_FAIL`**. The bounded diagnostic supports a
successor direction only: if work continues, it should first reopen the **joint Stage0/Stage2 semantic
boundary** rather than perform another P010-only threshold search.

Machine closeout:
`config/stage-learning/positive-instability-quantitative-development-selection-result.v1.json`
