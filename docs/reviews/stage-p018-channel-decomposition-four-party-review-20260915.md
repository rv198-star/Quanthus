# P3-STAGE-018 Channel Decomposition Four-Party Review — 2026-09-15

Review type: same-origin simulated four-party review  
Status: `PASS_RECOMMEND_FRESH_BLIND_MECHANISM_REFERENCE_PILOT`

This is one assistant simulating four professional perspectives. It is not an independent human or
multi-model panel.

## Evidence reviewed

P018 sole Job `stage_4e8eedc28b5542c798a8b33c8276755f` completed the preregistered artifact-only
diagnostic over the exact P017 252-case surface and exact nine P017 profile identities. The governed
manifest is `sha256:9e246625a68b7454df7f8356f1111e561850be49dfbac3e258a3071c814d55b0`.
No selection, new semantic labels, DWD, R/O/projection or preserved-data reads occurred.

## Core finding

The two existing internal channels have very different error structures.

The balanced expansion channel is relatively precise and conservative: macro precision about `.792`,
coverage about `.422`, FPR about `.013`, with 14 pooled TP and 2 FP. The lenient expansion version
raises coverage only to about `.472` while retaining relatively low FPR; the strict version is even
narrower.

The broad-reversal branch supplies more recall but is much noisier. Broad-balanced is approximately
`P=.458 / C=.578 / FPR=.170` with 20 TP / 26 FP; broad-lenient is approximately
`P=.341 / C=.634 / FPR=.313` with 22 TP / 46 FP. Even broad-strict is only about
`P=.525 / C=.372 / FPR=.097` with 15 TP / 14 FP.

For the closest P017 parent profile, expansion-balanced / broad-strict, the exclusive attribution is:

- expansion-only: 8 TP / 2 FP;
- overlap: 6 TP / 0 FP;
- broad-only: 9 TP / 14 FP.

Thus broad-only contributes useful parent-reference positives, but contributes even more false
positives. The high-quality overlap and expansion-only regions are obscured by the parent OR.

## Short-term trading perspective

A range that is actively expanding while showing path disorder is materially different from a merely
wide, two-way range. The first is a plausible instability mechanism; the second can describe mature
chop, distribution, post-move digestion or even orderly high-volatility consolidation. Treating both as
one proposition explains why loosening broad thresholds buys recall at an unacceptable false-positive
cost.

Recommendation: stop treating broad width + path disorder as automatically equivalent to structural
instability. Do not simply delete it either; its 9 broad-only true positives in the closest profile show
that it can contain real instability cases.

## Fund-manager perspective

The diagnostic supports a taxonomy problem rather than a scale problem. `Structural instability` is a
parent concept that likely contains several economically different mechanisms. A broad two-way regime
needs additional semantic context before it can be declared instability; expansion is cleaner but
cannot be expected to cover every parent instability case.

Recommendation: preserve parent instability as an umbrella research concept, but qualify children
independently before recomposing them.

## Quantitative perspective

P016 rejected global absolute thresholds; P017 rejected local scale calibration as a sufficient fix;
P018 identifies the OR composition as the primary source of quality loss. Importantly, the clean
expansion branch fails the parent coverage gate because that gate measures coverage of the *umbrella*
reference. That is not evidence that expansion is a bad child mechanism.

Using the already-diagnosed 252 cases to invent child labels now would be post-result semantic tuning.
The next valid experiment should therefore create a new preregistered blind mechanism-reference
surface and evaluate child reference sufficiency before any child candidate is scored.

## Data / governance perspective

The exposure-aware resource model established in P013 gives enough unused development capacity to
build a new balanced Era×Board review surface without touching preserved 2021 or permanent-blind 2025.
The new sample should be selected from metadata/admission rules before reviewer-visible semantic facts
are generated. It should exclude the P014/P015 reviewed identity-window units, while retaining the
existing rule that development resources may be reused only under explicit non-overlapping-window and
exposure provenance.

Reviewer packets must remain candidate-blind. P017/P018 thresholds, candidate outputs and diagnostic
attribution must not be visible during mechanism labeling.

## Decision

Selected successor direction:

`P3-STAGE-019 — Fresh Blind Instability-Mechanism Reference Pilot`

The successor should **not** begin by fitting thresholds. Its sequence should be:

1. freeze a fresh balanced Era×Board development review surface from the exposure-aware reservoir;
2. deterministic admission qualification before semantic review;
3. define reviewer-facing mechanism propositions in ordinary structural language, not threshold
   language;
4. blind-review the fresh cases for at least an expansion-instability proposition and a broad two-way
   disorder proposition, while retaining the parent instability question for relationship diagnostics;
5. evaluate reference prevalence, role agreement, cell coverage and parent/child relationship;
6. only if the child references are sufficiently coherent, open a later candidate-development gate.

The 252 P014/P015 cases remain useful diagnostic history but must not be the primary qualification set
for the newly introduced child semantics.

## Claim ceiling

This review recommends a fresh semantic-reference experiment only. It does not establish that the two
proposed child mechanisms are correct, does not qualify expansion or broad reversal as Stage labels,
and does not authorize preserved evaluation, strategy use, production use or trading.
