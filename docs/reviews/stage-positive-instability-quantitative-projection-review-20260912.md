# P3-STAGE-010-A — Positive Instability Quantitative Projection Adversarial Review

## Review provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is **same-origin simulation**, not four independent
experts and not human expert validation.

The review evaluates only the projection design. It does not authorize market-data reads, runtime
Jobs, threshold selection, 2021 evaluation access, Stage-2 changes, relocation retuning, strategy or
trading.

## 1. Short-term trader responsibility

### Why the direction is right

A rapidly expanding trading range can become structurally unstable before its absolute width reaches
a broad-range cross-sectional median. Forcing it through the same broad-width gate as a mature
round-trip box delays recognition and explains the P3-STAGE-009 coverage loss.

### Counterexample

A penny-width range doubles from 1% to 2% while price remains almost monotonic. Relative expansion is
large but the structure is not meaningfully unstable.

### Veto

`width_change` alone cannot establish expanding disorder. The channel must also require material
amplitude or absolute width gain and at least one path-disorder corroborator.

### Response to quant objection

Removing the shared width floor is not equivalent to allowing all small ranges. The channel-specific
amplitude/growth clause remains a hard semantic protection.

## 2. Fund-manager responsibility

### Why the direction is right

A broad structure that repeatedly gives back moves can be unstable even when endpoint efficiency is
not in the lowest half of the cross-section. Requiring both broad width and an extremely low
`directional_efficiency` percentile as a common gate conflates “not strongly directional” with “must
be among the weakest directions in the whole universe.”

### Counterexample

A broad range advances overall but makes two large reversals. Endpoint efficiency can be moderate,
while low traversal and large counter-excursion jointly show that the path is not orderly.

### Veto

No single reversal statistic may establish broad disorder. Material current width plus at least two
independent path-disorder signals are required.

### Response to trader objection

This does not make broad reversal permissive. A broad trend with only low efficiency but no round-trip
or counter movement still fails because one-of-three is insufficient.

## 3. Quant-research responsibility

### Why the direction is right

P3-STAGE-009 produced a clean structural diagnosis: 15 false negatives all failed before either
channel could activate, and the three expansion profiles were metric-identical within each shared
core/reversal profile. The next design should therefore alter parameterization topology, not lower a
single observed cutoff.

### Counterexamples

1. A large relative expansion from a tiny base can produce unstable ratios.
2. A nine-dimensional free search could recreate overfitting under a different name.
3. Reusing P3-STAGE-009 2020 labels to choose lower quantiles would contaminate the successor.

### Vetoes

- P3-STAGE-009 2020 development is diagnostic-only for P010.
- Thresholds must come from a new development cohort.
- Candidate search is capped at **nine bundled profiles**, not independent combinatorial tuning of
  every threshold.
- No result-dependent grid expansion.
- The preserved 2021 evaluation plan cannot be opened before one candidate is durably frozen.

### Response to evidence-governance objection

Projection-local absolute width gain is deterministic from already governed Facts. It does not create
an additional source-data degree of freedom and can be reproduced exactly from the same T-time input.

## 4. Data / evidence-governance responsibility

### Why the direction is right

The successor separates an implementation/projection defect from observational authority. No new
market source or new Fact identity is required. This keeps lineage smaller than adding another
indicator merely to fix a gating topology problem.

### Counterexample

A hidden projection-derived measure can become an ungoverned feature if its formula, availability
semantics or source bindings are not explicit.

### Vetoes

- `absolute_range_width_gain` must be versioned in the P010 projection contract with exact formula
  and fail-closed availability.
- It remains projection-local and must not silently appear as an accepted Catalog Fact.
- `center_shift` remains excluded from truth computation.
- The new 2019 development pool must exclude all previously exposed pools, including the unopened
  Stage-009 evaluation identities so development cannot overlap later evaluation.
- 2022–2025 remain closed.

### Response to fund objection

The two-of-three rule is acceptable because each primitive remains separately recorded; it is not an
opaque weighted score. Evidence can show exactly which two signals established the channel.

## Cross-review findings

| ID | Finding | Resolution |
| --- | --- | --- |
| QP-001 | Shared broad-width gate suppresses expanding disorder | CLOSED — expansion channel gets its own amplitude/growth clause |
| QP-002 | Shared very-low-efficiency gate suppresses both channels | CLOSED — expansion uses any explicit disorder corroborator; broad reversal uses two-of-three |
| QP-003 | Relative expansion can explode from a tiny base | CLOSED — amplitude OR absolute-growth materiality is required |
| QP-004 | Two-of-three could become an opaque score | CLOSED — fixed Boolean count, separate primitive ledger, no weights |
| QP-005 | Zero-net path makes counter-excursion unavailable | CLOSED — low efficiency + low traversal can satisfy two-of-three; no direction is invented |
| QP-006 | Consumed 2020 development could leak into new threshold choice | CLOSED — diagnostic-only; P010 uses new 2019 identities |
| QP-007 | Preserved 2021 evaluation could be contaminated | CLOSED — exact frozen plan stays unread until candidate freeze; no reselection/backfill |
| QP-008 | Search surface could expand again | CLOSED — exactly 3×3 bundled profiles / 9 candidates, no expansion |

## Verdict

```text
DESIGN: APPROVED
OPEN DIRECTIONAL BLOCKERS: 0
P3-STAGE-010-A: CLOSED
P3-STAGE-010-B: READY / IMPLEMENTATION ONLY
MARKET DATA / RUNTIME / PARAMETER SEARCH: NOT AUTHORIZED
PRESERVED 2021 EVALUATION: STILL UNREAD
2022-2025: CLOSED
```
