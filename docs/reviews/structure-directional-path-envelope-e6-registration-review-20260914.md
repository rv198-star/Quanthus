# P3-STAGE-012-E6 — B6-PATH Envelope Registration Decision Review — 2026-09-14

## Provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is same-origin simulation, not four independent
human experts or independent models.

The review is bound to E5 freeze commit
`a806baaf596f471decd0dd22bec16dc701f7da27` and the frozen E5 machine evidence. It opens no market
data, threshold search, runtime Job, preserved 2021 access, Catalog mutation or Fact registration.

## Decision

```text
SELECTED: REVISE_NEUTRAL_CONCEPT
REGISTER_AS_B6_PATH_EXTENSION: not yet
KEEP_RESEARCH_ONLY: not terminal
PARK: no
```

The E5 algorithm, B6-PATH ownership and synthetic result remain accepted within their existing claim
ceiling. The blocker is now **Catalog-facing object representation**, not the envelope semantic
hypothesis itself.

## Why direct registration is rejected

The approved semantic catalog has authority only over naming and boundaries. Every registered concept
is `reserved`; registration does not grant runtime, market or Stage qualification. Therefore E6 does
not require real-market validation merely to reserve a concept name.

However, the catalog accepts exactly these result kinds:

```text
reference
measurement
event
relation
process
```

E5 proposes:

```text
concept_id  = structure.geometry.close-envelope-resolution-trace
result_kind = structured_path_trace
```

`structured_path_trace` is not an approved result kind. Registering E5 as-is would therefore require
one of two unapproved moves:

1. expand the semantic type system solely for this proposal; or
2. coerce the trace into an existing type without proving that the object semantics still match.

E6 rejects both.

## Short-term trader responsibility

**Support.** E5 describes a useful distinction that existing churn/endpoint summaries do not: a path
can repeatedly escape a prior close envelope and return, while a high-churn orderly contraction can
remain fully inside. Same-side persistent repricing also stays distinguishable from alternating failed
resolution.

**Counterexample.** One escape, two same-side escapes, and alternating upper/lower failed resolutions
must not collapse into the same binary disorder signal merely because a Catalog representation needs
simplification.

**Veto.** Do not replace the trace with `escape_count >= N`, alternation thresholds, or a Stage0 truth
just to obtain a scalar measurement.

## Fund-manager responsibility

**Support.** The semantic owner remains Structure / B6-PATH. The trace says how close-path geometry
organizes through time; it does not infer intent, future reversal, accumulation, distribution or Stage
membership.

**Counterexample.** Mapping the T-ending trace directly to an Episode/process would imply a stable
cross-date process identity with start/progress/end continuity. E5 established no such episode
identity; it established one T-ending retrospective observation.

**Veto.** Do not invent a new peer Stage and do not weaken P008 relocation to make the descriptor win
classification conflicts.

## Quant-research responsibility

**Support.** E5 provides non-degenerate synthetic evidence: exact Stage-E2 parity, scale invariance,
PIT-safe T handling, listing/coordinate/gap reset, equality semantics and adversarial path behavior.
The remaining issue is not an obvious algorithm defect.

**Counterexample.** Synthetic conformance does not prove market separability or justify adding a new
Catalog result kind. A convenient serialization shape is not itself a domain type.

**Veto.** No new type-system member, threshold surface, candidate grid, market read or qualification
claim may be created by E6.

## Data / evidence governance responsibility

**Support.** Reserved Catalog registration is only naming/boundary authority, so after the
representation is reconciled the concept may eventually be registered without claiming market
qualification. B6-PATH remains the correct capability owner.

**Counterexample.** The current `structured_path_trace` result kind cannot be loaded by the approved
semantic catalog. Mapping it to `process` would conflict with the documented Episode model; mapping it
to `event` would tempt backdating re-entry knowledge; silently calling the whole payload a
`measurement` or `relation` needs a dedicated semantic decision first.

**Veto.** Do not mutate `config/structure-semantic-catalog.v1.json`, `_KINDS`, or the active
`config/structure-directional-path-geometry.v1.json` in E6.

## Why the other terminal routes are rejected

### Not `KEEP_RESEARCH_ONLY`

E5 did more than produce an exploratory statistic. It established a stable owner, causal input
closure, deterministic algorithm, reset/coordinate semantics, claim limits, Stage-E2 parity and a
non-degenerate synthetic suite. That is enough to justify one bounded representation-revision step.
Keeping it permanently research-only now would stop for a schema mismatch rather than a failed domain
concept.

### Not `PARK`

The concept itself has not failed. The blocker is representational: the proposed result kind is outside
the approved Catalog vocabulary. Parking is appropriate for a weak or non-separable semantic
hypothesis, not for a proposal whose remaining issue is how one Observation should be typed.

## E7 boundary

If the owner approves a next phase, `P3-STAGE-012-E7` may revise only the **Catalog-facing neutral
representation**. It must preserve:

- B6-PATH ownership;
- E5 close-envelope escape/re-entry semantics;
- T-only knowledge and no backdated re-entry fact;
- Stage-E2 behavioral parity;
- existing reset/coordinate rules;
- no Stage truth or threshold authority.

E7 must not add a new Catalog result kind. It should test whether the T-ending trace can be represented
cleanly within the existing object model, or whether the neutral concept must be decomposed without
losing identity and PIT semantics.

E7 remains synthetic-only and must end before any actual Catalog registration unless a later explicit
registration decision authorizes the final representation.

## Final verdict

```text
P3-STAGE-012-E6: CLOSED
DECISION: REVISE_NEUTRAL_CONCEPT
E5 ALGORITHM: RETAIN
B6-PATH OWNER: RETAIN
ACTIVE B6-PATH CONTRACT: UNCHANGED
SEMANTIC CATALOG: UNCHANGED
P008 RELOCATION: FROZEN
PRESERVED 2021: UNREAD
P3-STAGE-012-E7: BLOCKED PENDING OWNER APPROVAL
```
