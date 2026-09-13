# P3-STAGE-012 — Atomic Proposition Decomposition and Compact-Disorder Boundary

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | `P3-STAGE-011 / REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Concept | `../design/stage-atomic-proposition-successor-concept.md` |
| P011 post-closeout review | `../reviews/stage-orderly-consolidation-p011-post-closeout-expert-review-20260913.md` |
| A-C machine result | `../../config/stage-learning/stage-atomic-proposition-successor-abc-result.v1.json` |
| Current state | A/B/C/D and E1-E8 closed; E8 selects `REGISTER_RESERVED_MEASUREMENT` for the E7 measurement representation, but Catalog mutation is not performed; E9 is blocked pending owner approval; fresh data, preserved 2021, threshold search and runtime qualification remain blocked |

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-012-A | Atomic ontology and resolver audit | P3-STAGE-011 | closed `PASS_ATOMIC_PROPOSITION_FIRST`; atomic propositions become the next qualification unit before Stage0/2 projection; `../acceptance/stage-atomic-proposition-a-ontology-audit-20260913.md` |
| P3-STAGE-012-B | Consumed relocation-conflict adjudication | P3-STAGE-012-A | closed `PASS_CONSUMED_SEMANTIC_ADJUDICATION`; 0 guard-overfire / 2 reference-undercall / 9 genuine-coexistence / 0 unresolved; P008 retune not supported; `../acceptance/stage-atomic-proposition-b-relocation-adjudication-20260913.md` |
| P3-STAGE-012-C | Compact-disorder observational sufficiency audit | P3-STAGE-012-A | closed `INCONCLUSIVE`; current Facts/composition do not positively express compact disorder, but a new Fact is not yet proven; `../acceptance/stage-atomic-proposition-c-observable-audit-20260913.md` |
| P3-STAGE-012-D | Expert review and bounded repair-route decision | P3-STAGE-012-A/B/C | closed `RECOMMEND_SYNTHETIC_ONLY_ATOMIC_AND_ENVELOPE_ROUTE`; atomic-first architecture approved for successor design, P008 relocation stays frozen, no new Fact/fresh-data authority; `../reviews/stage-atomic-proposition-d-expert-review-20260913.md` |
| P3-STAGE-012-E | Atomic reference/resolver + envelope synthetic package | P3-STAGE-012-D | closed synthetic-only; E1/E2/E3 accepted, no market data; `../acceptance/stage-atomic-proposition-e-synthetic-acceptance-20260913.md` |
| P3-STAGE-012-E1 | Atomic Primary review/resolver contract | P3-STAGE-012-D | closed; existing protocol-v2 reused, role atomics and mixed-state/projection denominators made explicit |
| P3-STAGE-012-E2 | Threshold-free range-envelope prototype | P3-STAGE-012-D | closed synthetic prototype only; close-envelope escape/re-entry evidence implemented; not a Fact and no compact-disorder truth |
| P3-STAGE-012-E3 | Adversarial synthetic conformance | P3-STAGE-012-E1/E2 | closed `PASS_SYNTHETIC_CONFORMANCE`; orderly high-churn, one-leg relocation, failed-resolution disorder, stable narrow noise, mixed atomic state and unavailable inputs all pass |
| P3-STAGE-012-E4 | Post-E expert review / Fact-projection route decision | P3-STAGE-012-E1/E2/E3 | closed `RECOMMEND_B6_PATH_ENVELOPE_FORMALIZATION_SYNTHETIC_ONLY`; current prototype stays research-only; `../reviews/stage-atomic-proposition-e4-post-synthetic-review-20260913.md` |
| P3-STAGE-012-E5 | Structure-owned B6-PATH envelope formalization design + synthetic qualification | P3-STAGE-012-E4 | closed `PASS_SYNTHETIC_QUALIFICATION`; neutral trace design approved as a proposal, algorithm core moved to Structure owner, existing B6-PATH/Catalog remain unchanged; `../acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md` |
| P3-STAGE-012-E6 | Post-E5 registration/revision/research-only/park review | P3-STAGE-012-E5 | closed `REVISE_NEUTRAL_CONCEPT`; E5 algorithm/owner retained, direct registration rejected because `structured_path_trace` is outside the approved Catalog result kinds; `../reviews/structure-directional-path-envelope-e6-registration-review-20260914.md` |
| P3-STAGE-012-E7 | Catalog-facing neutral concept/result-kind revision | P3-STAGE-012-E6 | closed `PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION`; same concept/algorithm/profile retained, Catalog-facing kind revised to existing `measurement`, active Catalog/B6-PATH unchanged; `../acceptance/structure-directional-path-envelope-e7-representation-acceptance-20260914.md` |
| P3-STAGE-012-E8 | Reserved registration decision review | P3-STAGE-012-E7 | closed `REGISTER_RESERVED_MEASUREMENT`; four-responsibility review approves naming/boundary registration only and leaves Catalog unchanged; `../reviews/structure-directional-path-envelope-e8-registration-review-20260914.md` |
| P3-STAGE-012-E9 | Additive reserved semantic registration + metadata/planning alignment | P3-STAGE-012-E8 | blocked pending owner approval; if approved may add the reserved measurement concept and reviewed business metadata without runtime binding or market data |
| P3-STAGE-012-F+ | Fresh-cohort inventory / preregistration / development | P3-STAGE-012-E9 | blocked until E9 closes and a later owner approval explicitly opens fresh work |

## Hard boundaries

- P011 remains closed and immutable; no 28th pair and no coverage-gate relaxation.
- The sealed P011 review ledger is not rewritten.
- P008 relocation implementation remains frozen; B provides no authority to retune it.
- `mixed_directional_instability` is preserved atomic coexistence, not a newly approved peer Stage.
- C does not register `range-envelope resolution/persistence` as a Fact; it remains a hypothesis.
- P008-P011 evidence is consumed diagnostic evidence only.
- Preserved 2021 remains unread.
- 2022-2025, official labels, daily activation, strategy and trading remain closed.

## Next decision

E5 is complete. The neutral envelope escape/re-entry semantics have one Structure-owned algorithm core, a frozen 20/10 EvaluationSpec proposal, explicit PIT/reset/coordinate semantics and `PASS_SYNTHETIC_QUALIFICATION`. E6 then closes `REVISE_NEUTRAL_CONCEPT`: the semantic hypothesis and B6-PATH ownership remain accepted, but the proposed `result_kind=structured_path_trace` cannot be registered into the approved Catalog, whose result kinds are limited to reference/measurement/event/relation/process.

E7 is complete. The proposed concept keeps the exact E5 identity and semantics but now uses the existing Catalog `measurement` result kind. The structured event/descriptors payload remains one T-ending measurement value, not a set of independently registered events/Facts. E7 closes `PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION`; active Catalog and B6-PATH remain unchanged.

E8 is complete and selects `REGISTER_RESERVED_MEASUREMENT`. The review finds the E7 concept mature enough for `reserved` naming/boundary registration under B6-PATH: the identity, owner, `measurement` kind, EvaluationSpec, PIT/reset semantics and claim ceiling are stable, and P3-STAGE-008-B provides the precedent that reserved registration may precede runtime qualification.

The required next owner decision is whether to approve **P3-STAGE-012-E9 additive reserved registration**. E9 may add the concept to the semantic Catalog as `measurement / reserved`, add reviewed Chinese business metadata, refresh deterministic Catalog/planning expectations and record the B6-PATH extension as registered-but-unqualified. It must not rewrite the frozen P008 family contract, add a runtime accepted binding, read market data or preserved 2021, or change the E5 algorithm/profile. P008 relocation stays frozen and fresh research remains closed.
