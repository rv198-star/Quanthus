# P3-STAGE-017 Semantic No-Pass Four-Party Review — 2026-09-15

Review type: same-origin simulated four-party review  
Status: `PASS_OPEN_P018_INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC`

This is a simulation of four professional perspectives by one assistant origin. It is not an
independent human or model panel.

## Evidence reviewed

- P017 frozen contract: `config/stage-learning/stage-stratum-normalized-instability.v1.json`.
- P017 exact technical recovery authorization and acceptance.
- Recovery Job `stage_5356bab390684fd9ae154e5d14b1a02b`.
- Governed artifact manifest
  `sha256:6bdd7713b246ce7399a45f985f4bf2283a6d7dec6f535e27d0c13bbbe835b8ec`.
- Bounded trial diagnostics only. Cell-local threshold values, case predictions and reference rows
  were not exposed by the diagnostic transport.
- P016 no-pass evidence for comparison.

The review does not reopen 2021, 2022–2024 or 2025 data and does not authorize any new scoring Job.

## Result

P017 is a valid semantic no-pass, not a technical failure. The sole authorized technical recovery
completed the intended nine-profile experiment, and no profile satisfied the unchanged P012
instability quality gates under the frozen P016 pooled-count / Era×Board-macro-rate aggregate.

The important negative result is stronger than P016. A plausible hypothesis after P016 was that one
global absolute scale was the main reason the 3×3 instability topology failed across different market
regimes and boards. P017 directly tested that hypothesis by rebuilding the same profile coordinates
inside eight unlabeled local quantile bases. It did not solve the quality problem.

The closest balanced/strict profile changed from approximately `P=.765 / C=.621 / FPR=.060` in P016
to `P=.611 / C=.650 / FPR=.110` in P017. Coverage rose modestly, but precision fell materially and
FPR crossed the frozen maximum. The only profile whose macro coverage exceeded 0.70 was
lenient/lenient (`C≈.720`), but its precision was only about `.353` and FPR about `.335`. The
strict/strict profile drove FPR close to the `.10` ceiling but collapsed coverage to about `.439` and
precision to about `.544`.

## Short-term trading perspective

The problem is no longer well described as "the same pattern needs different volatility scales." The
current parent instability candidate treats a wide set of choppy, broad or expanding paths as the
same positive condition. In short-horizon trading terms, a volatile two-way range and a genuinely
breaking structural regime can look similar on width/traversal/counter-excursion summaries while
having very different continuation meaning.

Recommendation: stop searching denser thresholds. Separate the mechanisms first.

## Fund-manager perspective

Era/board normalization is sensible as a comparability device, but a local percentile does not make
two economically different structures equivalent. A broad but mature trading range, a violent
reversal regime and active range expansion should not automatically collapse into one parent
"instability" signal merely because each is extreme relative to its local board/era distribution.

Recommendation: retain Era×Board as context/diagnostic metadata, but move the research question from
scale calibration to mechanism identity.

## Quantitative perspective

Two consecutive bounded experiments now reject two simpler explanations:

1. P016: the frozen global-threshold 3×3 topology does not meet quality gates.
2. P017: replacing global raw scales with eight cell-local unlabeled quantile bases still leaves
   0/9 eligible profiles.

The candidate construction itself is an OR of two internally distinct channels:
`expanding_disorder_20` and `broad_reversal_disorder_20`. An OR can increase recall while creating a
large false-positive region if either channel has different precision or reference alignment. Before
introducing new features, thresholds or labels, the cheapest identifiable next question is therefore
channel attribution on the already-frozen surface.

Recommendation: P018 should compute expansion-only, broad-only and overlap metrics for all nine
already-frozen P017 profiles against the existing immutable parent `structural_instability_20`
reference. It must be diagnostic only: no selection, no new thresholds, no new labels and no result-
driven combination rule.

## Data / governance perspective

P017 reused the same 252-case development surface, did not read DWD, and kept all preserved partitions
closed. That property should continue. The P017 artifact already contains the exact cell bases and
9×8 profile grid needed for channel decomposition, while the prior evidence and ledgers remain
immutable.

P018 therefore requires no new data resource allocation and should not rereview the 252 parent
references. Any future introduction of mechanism-specific human/simulated reference labels must be a
later, separately preregistered step after the decomposition diagnostic demonstrates that such a
semantic split is worth opening.

## Decision

Selected disposition:

`PASS_OPEN_P018_INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC`

P018 is intentionally narrower than another candidate-development round:

- exact P017 252-case artifact closure;
- exact nine P017 profile identities and cell-local thresholds;
- compute `expanding_disorder_20` alone, `broad_reversal_disorder_20` alone and their overlap;
- compare each against the immutable parent `structural_instability_20` development reference;
- retain per-cell/macro/micro diagnostics;
- no candidate eligibility, selection, profile/grid change, new semantic labels, DWD read or
  preserved-data read.

Only after this diagnostic is frozen may a later review decide whether a true mechanism-specific
reference experiment is justified.

## Claim ceiling

This review supports only the next development diagnostic. It does not claim that a two-channel split
is correct, that the parent instability concept should be deleted, that any successor is qualified,
or that preserved/economic/production/trading validity has been established.
