# B6-PATH Envelope Escape / Re-entry Extension Proposal

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted for synthetic-only proposal |
| Registration gate | blocked pending E8 review |
| Work item | `P3-STAGE-012-E5 / E7` |
| Parent | [Structure Facts v2](structure-facts-v2.md) |
| Existing owner | `B6-PATH / Directional Path Geometry Fact Family` |
| Existing family contract | `../../config/structure-directional-path-geometry.v1.json` |
| Proposed extension contract | `../../config/structure-directional-path-envelope-extension.v1.json` |
| Synthetic contract | `../../config/structure-directional-path-envelope-synthetic-contract.v1.json` |
| Synthetic result | `../../config/structure-directional-path-envelope-synthetic-result.v1.json` |
| Implementation | `../../src/quanthus/structure_directional_path_envelope.py` |
| Acceptance | `../acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md`; `../acceptance/structure-directional-path-envelope-e7-representation-acceptance-20260914.md` |
| E7 representation | `../../config/structure-directional-path-envelope-catalog-representation.v1.json` |
| E7 result | `../../config/structure-directional-path-envelope-e7-representation-result.v1.json` |

## 1. Decision

P3-STAGE-012-E5 moves the successful Stage-learning envelope prototype under the correct semantic
owner **without registering a new Fact**.

The proposed neutral concept is exactly one structured T-ending observation:

```text
structure.geometry.close-envelope-resolution-trace
```

It describes close-path escapes from a frozen prior-close envelope and whether those escapes have
re-entered that same frozen envelope by T. It emits no Stage truth, no relocation truth, no compact-
disorder truth, no actor intent and no learned threshold.

The proposal belongs to `B6-PATH` because it is path geometry over causal daily closes, alongside
range traversal and counter-direction excursion. Creating a Stage0-only family would duplicate
Structure ownership and hide a reusable observation behind one consumer.

## 2. Minimal concept rather than descriptor sprawl

Only the trace is a proposed semantic concept. The following are deterministic fields of the trace,
not independent Fact identities:

- escape count;
- resolved / unresolved counts;
- upper / lower counts;
- resolved side sequence;
- side-alternation count;
- whether resolved escapes occur on both sides;
- unresolved side set;
- event-trace digest.

A later registration review may still reject the entire concept. Synthetic acceptance does not grant
Catalog authority.

## 3. Frozen 20/10 EvaluationSpec semantics

The proposal uses one exact profile for synthetic qualification:

```text
T-ending path:           20 sessions / 21 closes, T-20 ... T
prior close envelope:    previous 10 closes at each evaluated session
sessions evaluated:      indices 10 ... 20 in the 21-close path
upper escape:            current close > frozen prior-10 maximum
lower escape:            current close < frozen prior-10 minimum
equality:                inside, not escape
re-entry:                first later close inside/equal frozen escape envelope, through T only
```

The prior envelope is frozen at the escape session. A later rolling envelope does not rewrite that
event. `resolved_by_t` is knowledge available at the T-ending observation; it is **not backdated** as
knowledge available on the escape day.

No close after T may resolve an event in the T observation.

## 4. PIT, reset and coordinate semantics

E5 reuses the existing B6-PATH segment boundary rather than inventing a second one:

- same listing and coordinate epoch are required;
- hidden session gaps reset and require rewarm;
- non-admitted rows or missing close reset and require rewarm in a later builder;
- listing or coordinate changes reset and require rewarm;
- cross-coordinate comparison is forbidden;
- the pure E5 evaluator accepts only one already-admitted contiguous segment.

The proposed observation belongs to T. Event components inside the trace are not separately backdated
Fact observations.

## 5. Identity proposal

If registration is later approved, one observation identity must bind:

- security / listing subject;
- proposed concept/version;
- 20-session path profile;
- 10-close prior-envelope profile;
- escape equality rule;
- re-entry rule;
- coordinate policy;
- numeric policy;
- exact spec digest;
- coordinate reference;
- T business time;
- complete 21-row source closure;
- result digest.

This is a proposal only. E5 does not modify the current B6-PATH family contract, semantic catalog or
blueprint concept inventory.

## 6. Relationship to P3-STAGE-012-E2

The original Stage-learning prototype remains immutable evidence of why this dimension was proposed.
E5 moves the algorithmic core into `quanthus.structure_directional_path_envelope` and keeps the old
Stage module as a compatibility adapter. Re-running the frozen E3 synthetic suite produces the exact
same canonical result digest.

Therefore there is one envelope-detection implementation, not two drifting copies.

## 7. Synthetic qualification

E5 adds a broader Structure-owned suite beyond the original E3 cases. It verifies:

- high-churn orderly contraction stays inside the envelope;
- one-leg repricing followed by a hold remains a one-side unresolved escape;
- alternating failed resolution yields repeated resolved escapes on both sides;
- stable narrow noise produces no escape;
- equality at the frozen envelope boundary is inside;
- repeated same-side resolved escapes remain same-side, not artificial alternation;
- an escape first observed at T remains unresolved at T;
- missing close fails closed;
- insufficient history fails closed;
- positive price scaling preserves event geometry;
- listing/coordinate changes and hidden gaps require rewarm;
- frozen Stage E2 descriptors remain compatible.

The machine result closes `PASS_SYNTHETIC_QUALIFICATION` with no failed checks.

## 8. Claim ceiling and next gate

E5 proves a deterministic, neutral and owner-correct synthetic specification. It does **not** prove:

- market separability;
- that 20/10 is an optimal or useful market scale;
- a compact-disorder predicate;
- a Stage0 rule;
- predictive or economic usefulness;
- formal B6-PATH qualification;
- Catalog registration readiness.

The next required decision is a post-E5 review: register this proposal into B6-PATH, keep it research-
only, revise the neutral concept, or park it. That review itself opens no market data.

Fresh-cohort research, preserved 2021, threshold search, P008 relocation retuning, daily/strategy use
and trading remain separately blocked.

## 9. E6 registration review and E7 representation repair

E6 retained the E5 semantics and B6-PATH ownership but rejected direct registration because the E5
provisional `result_kind=structured_path_trace` is not a valid result kind in the approved semantic
Catalog. E6 explicitly rejected expanding the Catalog type system or coercing the T-ending observation
into an event/process merely to make registration pass.

E7 resolves only that representation mismatch. The concept identity remains:

```text
structure.geometry.close-envelope-resolution-trace
```

The proposed Catalog-facing result kind becomes **`measurement`**. This is one T-ending geometry
Observation whose structured value contains the exact E5 status, profile, ordered escape/re-entry
explanation records and deterministic descriptors. The underlying envelope detector and frozen 20/10
profile are unchanged.

This representation is compatible with the existing Structure object model because
`ObservationRevision` binds an exact `value_digest`; it does not require a measurement value to be a
scalar. Escape records remain explanation components inside the T-ending measurement and are not
separately registered events. Re-entry remains known as of T and is not backdated. The rolling prior
close envelopes remain EvaluationSpec context rather than independently governed anchor identities.

E7 synthetic representation conformance replays all nine frozen E5 cases and preserves their complete
value semantics exactly. `measurement` is already an allowed Catalog result kind, the concept remains
inside the owned `structure.geometry` family, and `catalog.resolve()` still rejects it because E7 does
not register it.

E7 therefore closes `PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION`. The next gate is E8 registration
decision review only. The active semantic Catalog and B6-PATH family contract remain unchanged until a
later explicit registration action is approved.
