# P3-STAGE-011-A — Orderly-Consolidation Boundary Adversarial Review

## Provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is same-origin simulation, not four independent
models and not human-expert validation.

The review evaluates only the successor design. It does not authorize market-data reads, runtime
Jobs, numeric threshold selection, 2021 evaluation access, official labels, strategy or trading.

## 1. Short-term trader responsibility

### Support

A contracting structure can remain highly active inside its range. “Orderly” should describe a
bounded, tightening geometry, not require a visually quiet tape. The P010 diagnostic also shows that
using center stability as a mandatory gate rejects many cases both reviewers still regarded as
Stage 2.

### Counterexample

A stock can compress from a very wide range to a still-wide range. Low directional efficiency and a
negative width change do not make that structure an orderly consolidation.

### Veto

Current range narrowness must remain a hard positive requirement. Contraction cannot substitute for
boundedness. No successor may simply delete the old `range_width` condition to improve coverage.

## 2. Fund-manager responsibility

### Support

The economic distinction is between a bounded structure that is organizing and a coherent repricing.
Moderate center drift can occur while a range compresses; forcing the midpoint to remain almost fixed
confuses “same price center” with “orderly.”

### Counterexample

A tightening one-way correction can satisfy low-efficiency and contraction conditions while still
being a directional relocation rather than consolidation.

### Veto

The already frozen P3-STAGE-008 coherent-relocation guard must remain a hard negative Stage-2
condition. P011 may not retune it merely because Stage2 is being reopened.

## 3. Quant-research responsibility

### Support

The consumed P010 evidence identifies a topology failure more clearly than a threshold failure:
all ten missed definite Stage-2 references pass the legacy efficiency, width and contraction gates
and fail only the center-shift hard gate. At the same time the legacy width ceiling admits one
definite Stage-0 conflict.

### Counterexamples

1. Selecting `0.15` now because the observed conflict sits around width `0.163` would be direct
   post-result tuning.
2. Adding path-turnover or counter-excursion thresholds because one conflict looks oscillatory would
   fail against the consumed Stage-2 references, which often have even higher in-band churn.
3. Searching every Stage2 threshold independently would recreate an unnecessarily large surface.

### Vetoes

- P010 2019 and P009 2020 are diagnostic-only for P011 threshold selection.
- Stage2 development uses exactly three bundled quantile profiles.
- P010 Stage0 remains exactly nine profiles; no tenth candidate is added.
- A future joint screen is capped at 27 preregistered pairs and cannot expand after results.
- The preserved 2021 plan remains unread until a pair is frozen on a new disjoint development cohort.

## 4. Data / evidence-governance responsibility

### Support

No new market source or Fact family is needed. Directional efficiency, range width and width change
are already governed; coherent relocation already owns the directional-path evidence. A
projection-local positive contraction magnitude can be reproduced exactly from width change.

### Counterexample

A new “path order score” introduced only inside Stage2 code would be an ungoverned hidden feature and
would duplicate information without a separate Fact decision.

### Vetoes

- `contraction_magnitude20 = max(0, -width_change20)` is projection-local only and may not silently
  become a Catalog Fact.
- center shift remains available for description/attribution but has zero Stage2 truth authority in
  this successor.
- range traversal and counter excursion enter Stage2 only through the unchanged relocation guard.
- no future row, preserved evaluation payload or 2022-2025 partition may be read by A/B/C.

## Cross-review findings

| ID | Finding | Resolution |
| --- | --- | --- |
| OC-001 | Legacy center stability is an over-mandatory Stage2 hard gate | CLOSED — center shift becomes modifier-only |
| OC-002 | Contraction alone can still leave a broad structure | CLOSED — material current narrowness remains mandatory |
| OC-003 | Smoothness/churn is not equivalent to order | CLOSED — no churn/counter hard gate is added |
| OC-004 | Directional tightening can masquerade as consolidation | CLOSED — frozen relocation guard remains mandatory negative evidence |
| OC-005 | P010 result could tempt direct width retuning | CLOSED — no numeric threshold selected from consumed evidence |
| OC-006 | Successor search could grow combinatorially | CLOSED — 3 bundled Stage2 profiles × existing 9 Stage0 profiles maximum |
| OC-007 | Pair precedence could hide a real semantic contradiction | CLOSED — simultaneous Stage0/Stage2 assertion remains a hard failure |
| OC-008 | 2021 could be contaminated by boundary redesign | CLOSED — preserved plan stays unread until fresh pair freeze |

## Verdict

```text
DESIGN: APPROVED
OPEN DIRECTIONAL BLOCKERS: 0
P3-STAGE-011-A: CLOSED
P3-STAGE-011-B: READY / IMPLEMENTATION + SYNTHETIC ONLY
MARKET DATA / RUNTIME / PARAMETER SEARCH: NOT AUTHORIZED
PRESERVED 2021 EVALUATION: STILL UNREAD
2022-2025: CLOSED
```
