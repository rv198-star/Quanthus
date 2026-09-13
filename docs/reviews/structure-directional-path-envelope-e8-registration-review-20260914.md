# P3-STAGE-012-E8 Reserved Registration Decision Review — 2026-09-14

## Provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is same-origin simulation, not four independent
models or human-expert validation.

The review is bound to frozen commit
`d977886473ad7bfe5d42c97423bb9d3abb7194c0` and E7 result digest
`sha256:60e8961761c6f70661fb2faafa53fc2447ac6d66d7d13d518520f1529be2855e`.
It opens no market data, threshold search, runtime Job, Catalog mutation, preserved 2021, Stage,
strategy or trading authority.

## Decision question

E7 resolved the E6 object-model blocker without changing the E5 algorithm. The proposed concept is:

```text
structure.geometry.close-envelope-resolution-trace
result_kind = measurement
registration = proposed / not yet registered
owner = B6-PATH / Directional Path Geometry
```

E8 chooses exactly one route:

```text
REGISTER_RESERVED_MEASUREMENT
REVISE
KEEP_RESEARCH_ONLY
PARK
```

The question is only whether the concept meaning/boundary is mature enough for **reserved semantic
Catalog registration**. Reserved registration is naming/boundary authority; it is not runtime,
market, Stage or economic qualification.

## Precedent

P3-STAGE-008-B registered `structure.geometry.range-traversal` and
`structure.geometry.counter-direction-excursion-share` as `reserved` semantic concepts with:

- market-data read = 0;
- runtime Job = 0;
- parameter search = 0;
- runtime binding still separately gated.

That acceptance explicitly states that semantic Catalog registration does not create runtime accepted
bindings. E8 therefore does not require market qualification merely to decide stable naming/boundary
registration.

## Short-term trader responsibility

**Verdict: REGISTER_RESERVED_MEASUREMENT.**

E7 preserves the complete path organization needed to distinguish no escape, one-side persistence,
same-side failed attempts and alternating failed resolution. Registration would stabilize a neutral
name for this reusable observation without collapsing it into a count threshold or Stage0 signal.

**Counterexample.** A single escape is not equivalent to alternating failed resolution. The structured
measurement payload must remain intact.

**Veto.** Registration cannot create a binary disorder predicate, Stage label or independently
registered descriptor Facts.

## Fund-manager responsibility

**Verdict: REGISTER_RESERVED_MEASUREMENT.**

The concept describes neutral price-path organization and belongs to Structure/B6-PATH. Stable semantic
identity is useful independently of whether later evidence proves the 20/10 profile economically useful.

**Counterexample.** Reserved registration cannot be read as evidence that 20/10 is an optimal scale or
that escape/re-entry predicts reversal, accumulation or distribution.

**Veto.** No runtime binding, strategy consumption or new primary Stage follows from registration.

## Quant-research responsibility

**Verdict: REGISTER_RESERVED_MEASUREMENT.**

E5/E7 establish deterministic synthetic expressibility, exact E5 parity, positive-scale invariance,
PIT/reset semantics, fail-closed unavailable/insufficient history behavior, structured value-digest
compatibility and a pre-existing Catalog result kind. The identified E6 representation defect is closed.

**Counterexample.** None of this establishes market separability, window optimality, predictive value,
economic usefulness or Stage threshold validity.

**Veto.** No parameter search, candidate surface, fresh experiment or qualification claim may be
opened by the registration decision.

## Data / evidence-governance responsibility

**Verdict: REGISTER_RESERVED_MEASUREMENT.**

The concept now has:

- stable three-segment identity under the owned `structure.geometry` family;
- valid existing `measurement` result kind;
- exact EvaluationSpec/profile and coordinate/reset semantics;
- ObservationRevision-compatible structured value digest;
- explicit PIT and no-backdating rule;
- deterministic synthetic conformance;
- explicit claim ceiling and negative authorities.

The P3-STAGE-008-B precedent shows that this is sufficient for `reserved` semantic registration while
runtime qualification remains separate.

**Counterexample.** The P008 B6-PATH v1 contract is a frozen historical contract and must not be
rewritten in place merely to add this later extension.

**Veto.** Actual mutation must be additive: semantic Catalog + reviewed Chinese business metadata +
planning/blueprint evidence alignment. It must not create a runtime binding or invalidate the frozen
P008 contract digest.

## Resolution

All four responsibilities select:

```text
SELECTED: REGISTER_RESERVED_MEASUREMENT
concept: structure.geometry.close-envelope-resolution-trace
result_kind: measurement
registration: reserved
owner: B6-PATH / Directional Path Geometry
```

The alternatives are rejected as follows:

- `REVISE`: E7 removed the only identified object-model defect without semantic or algorithm drift.
- `KEEP_RESEARCH_ONLY`: leaving a stable, owner-correct neutral concept unnamed in the Catalog adds no
  governance benefit once its meaning/boundary is mature.
- `PARK`: the concept is non-degenerate and no representation blocker remains.

## Claim ceiling

Reserved registration means only that Quanthus recognizes the semantic name and boundary. It does
**not** claim:

- runtime accepted B6-PATH binding;
- market separability or market qualification;
- optimality of 20/10 windows;
- Stage0/Stage2 qualification;
- threshold authority;
- predictive/economic usefulness;
- daily, strategy or trading authority.

P008 relocation remains frozen and preserved 2021 remains unread.

## Next gate

E8 itself does not mutate the Catalog. The selected mutation is routed to a separately owner-approved
`P3-STAGE-012-E9`:

```text
E9: BLOCKED PENDING OWNER APPROVAL
```

If approved, E9 may only perform additive reserved registration, reviewed Chinese business metadata,
deterministic Catalog/contract refresh and Structure blueprint/planning alignment. It must keep the
frozen P008 `structure-directional-path-geometry.v1.json` contract immutable and must not add a runtime
accepted binding, read market data, open preserved 2021, select thresholds/candidates or change the E5
algorithm/profile.
