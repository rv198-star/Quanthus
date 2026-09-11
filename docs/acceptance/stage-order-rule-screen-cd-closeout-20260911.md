# P3-STAGE-007 C/D closeout — fixed rule screen

Status: CLOSED / REVISE_TARGET_OR_PARK

This record closes the single preregistered Stage-007 development screen. It does not qualify an
official stage label, authorize daily/strategy use, open a sealed partition, or permit trading.
The one fixed candidate was scored only after the 320 raw opinions were frozen and sealed.
No threshold tuning, sample replacement, supplement round, or semantic retry occurred after result access.

## 1. Runtime receipt

The only C result-bearing Worker Job was:

- job: `stage_e8d174dd13db412e94c8f4bdbf70c7f5`
- kind: `quanthus.stage-order-rule-resolve-score.v1`
- revision: `38b965aea678d180a12299b53db3f4db4a1ce256`
- input digest: `sha256:d169d5673430e3ca4fd5962d8f2e834e4ccb5676c0db51f54ce0cfd0491a7cc3`
- started: `2026-09-11T13:45:17.270315+00:00`
- completed: `2026-09-11T13:45:36.734284+00:00`
- state / exit: `succeeded / 0`
- run: `stage-order-rule-resolve-score-20260911T134519Z`
- artifact manifest: `sha256:a7dbf622fce034b7b5d88d1bf3cee46b5e6e52786ea786a56e97bd0069e1a931`
- sealed reference manifest: `sha256:d87572762af7c6397290cf7ca731f213f9ce5674e4034aa2cb17427c07fbce40`

The Job consumed the accepted prepare artifact from
`stage_95f9b48bd96049b184e0cbf856b9a5e8`. It performed no new market-data read,
no new Fact materialization, no opinion regeneration and no parameter search.

## 2. Frozen references and candidate output

The immutable same-origin self-simulated reference ledger contains 160 cases and 320 opinions.
Reference counts after the two-role resolution are:

- Stage 0: 30
- Stage 2: 46
- Neither: 36
- Ambiguous: 48
- Input unavailable: 0

The frozen candidate output counts are:

- Stage 0: 61
- Stage 2: 44
- No assertion: 55
- Input unavailable: 0
- Rule conflict: 0

The formal gate calculation returned:

`REVISE_TARGET_OR_PARK`

with three reasons:

- `STAGE_0:precision_fails`
- `STAGE_0:definite_negative_false_positive_rate_fails`
- `ambiguous_assertion_rate_fails`

## 3. Definite-reference metrics

### Stage 0

- definite positive references: 30
- definite negative references: 82
- definite predictions: 40
- true positives: 28
- false positives: 12
- false negatives: 2
- precision: 0.70
- positive-reference coverage: 0.9333333333
- definite-negative false-positive rate: 0.1463414634

Stage 0 therefore has high recall but materially over-asserts. The 12 definite false positives are all
reference `NEITHER`, not Stage 2. The two reviewers independently describe every one of those 12 as
a directionally dominated advance, decline or correction rather than low-direction disorder. The old
R0 rule nevertheless asserts Stage 0 because `|directional_efficiency20| <= 0.40` plus either width
expansion or center migration is sufficient. This is a systematic target/representation failure, not
random noise.

Quarterly Stage-0 precision is also unstable: Q1 0.8462, Q2 0.8333, Q3 0.5882 and Q4 0.50.
These are descriptive slices only; no population confidence claim is made.

### Stage 2

- definite positive references: 46
- definite negative references: 66
- definite predictions: 33
- true positives: 33
- false positives: 0
- false negatives: 13
- precision: 1.00
- positive-reference coverage: 0.7173913043
- definite-negative false-positive rate: 0

Stage 2 therefore clears the frozen class-specific numeric gates on definite references. The challenge
panel also has precision 1.00 and positive-reference coverage about 0.758. The coverage panel has only
8 definite Stage-2 predictions, so it does not satisfy the predeclared minimum count for a coverage-panel
precision veto/claim. This is a promising exploratory sub-result only; the batch is same-origin and is not
independent semantic validation.

## 4. Reference-quality defect discovered in D attribution

A-stage semantics explicitly froze that 10-session recent facts remain a separate local description and
must not become a hidden veto for the 20-session primary Stage-0/Stage-2 decision.

Post-result attribution found that the raw immutable opinion ledger did not fully respect that rule:

- 48 references resolved as ambiguous;
- 21 are `short-term UNKNOWN/NEITHER` versus `fund Stage 2` where the short-term reason explicitly uses
  recent expansion or recent movement to downgrade the 20-session primary decision;
- at least 17 are `UNKNOWN / Stage 2` and 4 are `NEITHER / Stage 2` for this clear recent-veto pattern.

Those 21 opinions remain immutable evidence of the defect; they are not rewritten after seeing scores.
Consequently the observed ambiguous assertion rate of `32 / 48 = 0.6667` is not a clean rule-quality
measure and must not be used alone to reject the candidate.

However this reference defect does not rescue the combined candidate. Stage-0 precision and
false-positive-rate failures are calculated only on definite references and remain failed independently.
The combined `REVISE_TARGET_OR_PARK` decision is therefore robust even after downgrading the authority
of the ambiguity metric.

## 5. D attribution and decision

The result is attributed as follows:

1. **Stage-0 target/representation failure — primary blocker.** The four summary Facts cannot reliably
   distinguish coherent directional relocation from genuinely low-direction structural instability.
   Center migration and range expansion occur in both. Retuning the existing efficiency threshold on
   this batch is explicitly prohibited and would not solve the underlying representation problem.
2. **Reference semantic-guard failure — secondary blocker.** The review process allowed recent-window
   evidence to affect the primary decision in at least 21 ambiguous cases, contrary to the frozen target.
3. **Stage-2 hypothesis remains viable.** Its definite-reference metrics are strong, but no promotion is
   authorized because the evidence is same-origin and the combined batch contains the reference defect.
4. **No evidence of source, PIT, sample-selection or runtime failure.** Source isolation, fixed selection,
   T-only evidence, artifact sealing and Worker execution all remained valid.

Final decision: **REVISE_TARGET_OR_PARK** for the combined Stage-0/Stage-2 rule.

## 6. Successor direction — no automatic rerun

Do not tune R0 and do not rerun these 160 cases as a new qualification batch.

The next design work should be abstract, not case-specific:

- For Stage 0, add a same-T, primary-scale continuous path-shape fact that distinguishes **coherent
  directional relocation** from **bidirectional/oscillatory structural instability**. A migration or wider
  range should count as Stage-0 evidence only when its path shape supports instability rather than a
  directional transition. This should be a new semantic/Fact proposal, not a lower or higher threshold on
  the current four values.
- Repair the reference protocol so recent-window observations can be recorded as modifiers/contradictions
  but cannot silently downgrade the primary 20-session assertion. Add an explicit semantic audit before
  sealing future opinions.
- Preserve the current Stage-2 rule unchanged as a promising exploratory hypothesis. Do not consume the
  unopened 2025 partition merely to compensate for the Stage-0 redesign; decide the next fresh validation
  partition only after the semantic repair is frozen.

P3-STAGE-007 is complete as a decision experiment even though the candidate is not accepted. The outcome
is evidence-backed rejection/revision, not an engineering failure and not a request for another in-run
iteration.
