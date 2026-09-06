# ADR 0014: Time-first Structure semantics and bounded namespaces

- Status: Accepted
- Date: 2026-09-06
- Work item: `P3-STRUCT-004`
- Governing design: `../design/structure-facts-v2.md`
- Coverage ledger: `../../config/structure-facts-v2-blueprint.v1.json`
- Authority: semantic/modeling contract; no v2 market-data or investment acceptance

## Context

Facts v1 closed a bounded real-DWD engineering chain. The next proposal mixed modeling axes:
scale, measurement families, reference interactions and lifecycle processes. The owner requested
reviewed modeling boundaries, semantic/category naming, explicit temporal fitness, a complete but
staged blueprint, and separately scheduled dictionary maintenance.

A static stock-to-state graph would hide timing, late confirmation and revisions. A comprehensive
financial ontology or new graph stack is not required for the actual consumer problem. Conversely,
adding independent technical columns without shared definitions lets experts disagree about facts
and silently mix indicator variants or price coordinates.

## Decision

Use a lightweight shared evidence vocabulary plus bounded domain modules. Structure owns anchor,
geometry, event-conditioned activity, interaction and episode concepts. Traditional indicators,
public disclosures, financial reports, market context and future outcomes retain separate owners.
Necessary identity/calendar/company-action/tradability inputs remain required references.

Concept names have exactly three segments: domain.family.concept. Parameters, algorithm/version,
coordinates and clocks belong to a separate evaluation specification, never inferred from display
names such as MA10. Prefix lookup uses complete segment boundaries. Registration, implementation,
verification and trading authority are separate; a reserved name is not an active evaluator.

Represent dynamic knowledge with immutable observation/event versions, stable reference/process
identities, and explicit event/confirmation/public/platform/computation clocks. Distinguish actual
platform-as-known queries, evidence-backed public reconstruction and retrospective diagnostics.
Keep procedural temporal joins/replay and existing storage; adopt no OWL/RDF/graph runtime.

Retain every v1 formula, identifier and acceptance package. New catalog relationships to legacy
features are related-not-equivalent, not silent aliases. Existing retrospective source-view limits
remain disclosed; ontology and deterministic hashes do not manufacture source availability proof.

Maintain a machine-readable blueprint with design, implementation, verification, gaps, dependencies
and next gates. B1 implements the small contract only; B2-B5 define the first runtime route.
Geometry/activity expansion is planned, not automatically activated. `P3-DICT-001` is a separate
backlog and never a prerequisite for that route.

## Alternatives and consequences

- Reject 'ontology replaces time-series/replay': vocabulary cannot enforce arrival/version clocks.
- Reject 'all influential information belongs inside Structure': parallel domains allow independent
  provenance and time rules without pretending news is irrelevant.
- Reject 'all planned facts first': each runtime slice needs a concrete consumer and bounded budget.
- Retain optional future projections/graph views only if an actual consumer justifies them.

B1 adds a naming catalog/resolver and local temporal-envelope checks, not a source-verification
engine. Integrated evidence closure, coordinate handling and as-of consumption remain visible B2
work. No DWD rebuild, data access expansion, policy/threshold change, training, Shadow or deployment
is authorized by this decision.
