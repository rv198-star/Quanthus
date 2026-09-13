# P3-STAGE-011 Post-Closeout Expert Meeting — 2026-09-13

## Provenance and authority

This meeting is one ChatGPT assistant simulating four responsibilities: short-term trader, fund
manager, quantitative researcher, and data/evidence methodology. It is same-origin simulation, not
four independent models and not human-expert validation.

The meeting reviews the already-closed P3-STAGE-011 evidence and discusses a successor concept. It
opens no implementation, market-data read, parameter search, preserved-2021 evaluation, official
label, daily, strategy or trading authority.

Reviewed evidence is limited to already-consumed repository material:

- `../acceptance/stage-orderly-consolidation-joint-d2-development-closeout-20260913.md`;
- `../../config/stage-learning/orderly-consolidation-joint-development-failure-diagnostic.v1.json`;
- `../design/stage-orderly-consolidation-boundary-repair.md`;
- P3-STAGE-008 directional-boundary closeout and P3-STAGE-009 positive-instability semantics;
- the stock-stage qualification principle that the twelve-state vocabulary is still a hypothesis,
  not a mandatory closed class.

No new market rows, preserved 2021 payload, 2022-2025 partition, candidate trial or runtime Job was
opened for this review.

## 1. Quantitative-research responsibility

### Assessment of P011

P011 should be recorded as a valid failed development screen, not as a failed research program.
Governance worked as intended: the 0.70 Stage0 coverage gate was frozen before results, the best
frontier reached only `58/83 = 0.698795`, and the screen stopped with zero eligible pairs instead of
rounding, lowering the gate or adding a 28th pair.

The fresh evidence supports the *topology* of the Stage2 repair. The balanced Stage2 profile reaches
20/21 TP, precision/coverage about 0.9524 and FPR about 0.00637 with zero proposition overlap on the
P011 development denominator. That is strong development evidence, but it is not independent
qualification and does not authorize promoting the observed balanced numeric thresholds as a frozen
production rule.

### Main concern

The joint 9x3 screen coupled three different questions:

1. whether structural instability was detected;
2. whether coherent relocation was present;
3. whether orderly consolidation was present.

The 25 Stage0 false negatives show why this coupling is now costly. Fourteen are missed by the
positive-instability proposition itself, while eleven already have instability positive but are
removed by the relocation veto. The next milestone should therefore qualify atomic propositions and
resolver behavior separately before optimizing a projected Stage0 label.

### Veto

Do not change the 0.70 gate, select the 58/83 frontier, add another P011 candidate, or promote the
P011 balanced Stage2 thresholds because they look good after scoring.

## 2. Short-term-trader responsibility

### Assessment of P011

The Stage2 repair matches observable tape structure better than the legacy rule: a contracting range
can remain active and two-sided without being structurally disordered. Smoothness is not the same as
order.

The fourteen Stage0 misses outside both positive-instability channels also correspond to a real
trading shape: a range may be relatively narrow or contracting while price repeatedly fails to
resolve, reverses across the local structure, or invalidates successive directional attempts. Such a
path can be unstable even though it is neither broadly wide nor currently expanding.

### Main concern

A third Stage0 channel must not be defined as simply `narrow + high churn` or `high counter excursion`.
P011 already demonstrates that valid orderly consolidation can contain substantial in-band churn and
counter movement. The missing observable, if one exists, is closer to **failed directional
resolution / envelope instability**: repeated failed escapes, alternating boundary pressure, or an
envelope that repeatedly re-expands/re-centers instead of coherently compressing.

### Veto

Do not create a compact-disorder channel merely by lowering the broad-width floor or by converting
turn count / turnover into a direct Stage0 truth gate.

## 3. Fund-manager responsibility

### Assessment of P011

The eleven definite Stage0 references that also trigger the frozen P008 relocation guard are not
proof that the guard is wrong. A security can undergo a coherent repricing while also being under
structural stress. P008 and P009 already allowed the atomic propositions `instability=TRUE` and
`relocation=TRUE` to coexist and preserved that as `mixed_directional_instability` reason metadata.

For portfolio interpretation this distinction matters. A direction-neutral unstable box and a
violent directional repricing should not be forced into the same primary stage merely to improve
Stage0 recall.

### Main concern

The current Stage0/2 projection may be hiding an ontology problem. The eleven conflicts can represent
at least three different failure classes:

- numeric relocation guard over-fire;
- reviewer under-recognition of relocation;
- genuine coexistence of instability and relocation, where neither side is wrong and the projected
  Stage0 label is the wrong evaluation target.

These hypotheses must be separated before any relocation threshold is reopened.

### Veto

Do not weaken or retune P008 relocation from P011 aggregate metrics. First determine whether the
conflicts are predicate errors or projection/taxonomy errors.

## 4. Data / evidence-methodology responsibility

### Assessment of P011

P011 has a strong audit trail: candidate-blind review, immutable ledger seal, fixed finite grid,
explicit stop rule and preserved 2021 remaining unread. The D1 `case_budget` failure was a shared
implementation-conformance defect and was repaired at the protocol-budget level rather than hidden by
a rerun.

The reference set is nevertheless same-origin simulated review with manual boundary adjudication. It
is sufficient for governed development and diagnostic attribution, but it is not independent market
truth. Claims about Stage2 should therefore remain “strongly supported on fresh development evidence,”
not “solved” or “qualified.”

### Main concern

A successor must not automatically add a new Fact. P009 correctly required evidence of a missing
observable before expanding the Fact surface. P011 now provides a reason to investigate that question,
but it does not yet prove which observable is missing.

The next design should compare compact-disorder cases with valid Stage2 cases using existing T-only
Facts first. Only if existing dimensions cannot express the distinction should a new threshold-free
Fact be proposed. A plausible concept is range-envelope persistence / failed-resolution geometry,
but its exact identity must be justified by counterexamples rather than chosen from the P011 misses.

Freshness is also now a scarce resource. 2018 P011, 2019 P010 and 2020 P009 are consumed; other
partitions have their own taint/authority history. The successor must inventory unconsumed identity,
time and evidence surfaces before naming another development cohort. Preserved 2021 must not be
converted into development data simply because the current screen failed.

## 5. Cross-panel findings

| ID | Finding | Panel resolution |
| --- | --- | --- |
| ER-001 | Did P011 actually fix Stage2? | **Strong development support, not qualification.** Retain the repaired Stage2 topology as the leading design; do not freeze post-result numeric profile thresholds. |
| ER-002 | Is 58/83 close enough to pass? | **No.** The one-TP gap is still a preregistered failure and cannot justify gate relaxation or reference editing. |
| ER-003 | Do the 14 compact/contracting misses prove a third Stage0 channel is needed? | **They prove a shared admission blind spot, not the exact repair.** First test observational sufficiency and counterexamples. |
| ER-004 | Do the 11 relocation-veto conflicts prove P008 is too strict? | **No.** They require atomic semantic adjudication among guard over-fire, reviewer under-call and genuine mixed state. |
| ER-005 | Should the next screen again optimize projected Stage0 directly? | **No.** Qualify atomic instability / relocation / order propositions first; evaluate stage projection afterward. |
| ER-006 | Should mixed directional instability become Stage0 automatically? | **No.** Preserve coexistence explicitly; primary-stage ownership remains a taxonomy decision. |
| ER-007 | Should a new path Fact be added now? | **Not yet.** Existing Facts get first right of refusal; only a demonstrated missing observable may open a new Fact design. |
| ER-008 | Can preserved 2021 be opened now because Stage2 looks good? | **No.** P011 froze no pair; preserved 2021 remains sealed. |

## 6. Red-team challenge to the successor recommendation

The panel then reviewed its own recommendation adversarially.

### RT-1 — atomic propositions could become an abstention escape hatch

Separating propositions is useful only if the downstream projection remains measurable. The successor
must report single-stage coverage, mixed-state rate, unresolved-conflict rate and candidate-set size.
It may not improve apparent proposition recall by moving every difficult case into a multi-label or
ambiguous bucket.

### RT-2 — `mixed_directional_instability` could become a catch-all

Coexistence must require independent positive evidence for both instability and relocation. It cannot
mean “the two classifiers disagree” or “the case is hard.” A disagreement remains a disagreement
unless both propositions are positively supported.

### RT-3 — a new envelope Fact could encode the P011 misses by construction

Any range-envelope / failed-resolution Fact must be defined from synthetic and semantic
counterexamples before another fresh result-bearing read. P011 cases may motivate the question but
cannot select the eventual formula, thresholds or profile.

### RT-4 — Stage2 success could be overgeneralized

The balanced P011 profile is development evidence from a same-origin reference set. The successor
must retain Stage2 counterexamples and cannot treat the 20/21 result as a permanent truth that no
longer needs compatibility testing.

### RT-5 — research decomposition could grow indefinitely

The successor needs explicit exits. A-C must end with a finite route decision, including the valid
outcome `INCONCLUSIVE / PARK`. It may not open a new Fact family, relocation redesign and taxonomy
rewrite simultaneously merely because all three remain imaginable.

### RT-6 — fresh evidence may be unavailable

The project has consumed multiple time/identity surfaces. If the freshness inventory cannot provide a
credible development/evaluation separation while preserving 2021, the correct result is `BLOCKED /
INSUFFICIENT_FRESH_EVIDENCE`, not reuse of tainted partitions.

## 7. Meeting verdict on P011

```text
P011 DEVELOPMENT DISPOSITION:        REVISE_OR_PARK_DEVELOPMENT_FAIL
P011 GOVERNANCE / STOP RULE:         PASS
STAGE2 TOPOLOGY:                     STRONGLY SUPPORTED ON DEVELOPMENT EVIDENCE
STAGE2 NUMERIC PROFILE:              NOT QUALIFIED / NOT FROZEN
STAGE0 AMPLITUDE-ADMISSION BLIND SPOT: CONFIRMED DIAGNOSTIC
STAGE0 <-> RELOCATION ALIGNMENT:     UNRESOLVED SEMANTIC CONFLICT
PRESERVED 2021:                      UNREAD
NEW DATA / PARAMETER SEARCH:         NOT AUTHORIZED
```

## 8. Successor recommendation

The panel recommends one bounded umbrella successor, provisionally named
`P3-STAGE-012 — Atomic Proposition Decomposition and Compact-Disorder Boundary`, but **not yet opened
as an implementation work item**.

Its first job is not another threshold search. It should:

1. separate atomic proposition quality from final Stage projection;
2. adjudicate the relocation conflicts before changing the guard;
3. determine whether compact/contracting disorder can be represented by existing Facts;
4. only then decide whether the eventual repair is projection-only, relocation-proposition repair,
   a third instability channel, a new threshold-free Fact, or a combination.

The detailed non-governing concept is recorded in
`../design/stage-atomic-proposition-successor-concept.md`.
