# Layered Stage Proposition Bundle

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted first-milestone MVP |
| Work item | `P3-STAGE-028` |
| Parent architecture | `stock-stage-vocabulary-portfolio.md` |
| Machine contract | `../../config/stage-learning/stage-proposition-bundle.v1.json` |
| Acceptance | `../acceptance/stage-proposition-bundle-mvp-20260916.md` |
| Review | `../reviews/stage-p028-layered-bundle-four-party-review-20260916.md` |
| Scope | representation/governance only; no new market or reference read |

## 1. Purpose

The Stage Proposition Bundle is the first executable representation of the P027 layered vocabulary.
It replaces the abandoned assumption that every useful stage concept must collapse to one mutually
exclusive `primary_stage`.

A bundle represents one security at one point-in-time knowledge cutoff using three proposition layers:

- context / structural-state assertions;
- event / nested-condition assertions;
- modifiers.

It also carries epistemic status, control state, exact Fact references and proposition provenance.
Historical stage numbers remain traceability metadata. They do not define topology or precedence.

`primary_stage` is always `null` in v1.

## 2. Bundle shape

The canonical v1 surface contains:

```text
schema_version
contract_version
security_id
knowledge_cutoff
control_state
epistemic_status
status_reasons
context_assertions[]
condition_assertions[]
modifier_assertions[]
fact_refs[]
primary_stage = null
```

Every assertion contains:

```text
proposition_id
historical_stage_code
layer
verdict
proposition_version
evidence_grade
use_authority
reason_codes[]
fact_refs[]
provenance_refs[]
```

The contract owns proposition id, historical code, layer, version, evidence grade and maximum use
authority. A caller supplies only observed verdict/evidence references and cannot promote a proposition
by changing metadata in an output object.

## 3. Evidence authority

Evidence grade is governance metadata, not a probability and not a trading score.

Only `accepted_development_projection` may appear on the v1 `development_shadow` consumer view. That
currently means the accepted Stage10/11 projection pair only. The first-group Stage3/4 development
candidate and every exploratory, research, no-pass, parked or missing-Fact proposition remain
`audit`-only.

This distinction is intentionally strict: **representation is not promotion**.

## 4. Coexistence and dependency rules

Cross-layer coexistence is legal. A structural-failure context and a nested rebound condition may both
be true, which is the accepted Stage10/11 case.

Same-layer multiple TRUE assertions require bundle-level `AMBIGUOUS` status. Neutral code 0 TRUE may
not coexist with another TRUE context; uncertainty must be represented as ambiguity or abstention,
not by treating neutrality as an else-class.

When a proposition has a declared same-bundle parent, a TRUE child requires the TRUE parent. The v1
catalog therefore enforces the accepted relationships for 1 under 12, 6/8/9 under the advancing
context, and 11 under 10. Stage4 instead requires explicit prior Stage3 episode provenance because its
parent is temporal rather than a simultaneously true context.

## 5. ABSTAIN, neutral 0 and X

These three meanings remain separate:

- neutral code `0` is a market proposition and can only be represented as a catalogued context;
- `ABSTAIN` is an epistemic status for usable inputs that support no sufficiently definite TRUE
  proposition;
- `X` is control flow for an invalid/frozen ordinary stage evaluation.

`ABSTAIN` requires zero TRUE assertions. `X` requires `NOT_EVALUATED` and empty assertion layers. Neither
is converted into historical stage code 0.

## 6. Fact and provenance closure

Every assertion must carry at least one reason code, one Fact reference and one provenance reference.
The top-level `fact_refs` set must equal the exact union of assertion Fact references. The bundle
therefore cannot advertise evidence that no assertion consumes, and an assertion cannot depend on an
unpublished Fact reference.

Stage10/11 TRUE assertions additionally require immutable Stage10 confirmation provenance. Stage4 TRUE
requires a prior same-anchor Stage3 episode reference.

## 7. Determinism

Assertions are serialized in ascending `proposition_id` order and top-level Fact references are unique
and lexicographically ordered. Canonical JSON serialization and digesting are deterministic for the
same semantic bundle regardless of caller insertion order.

## 8. Expert consumption boundary

P028 exposes a non-production `development_shadow` view so expert interfaces can consume the accepted
10/11 proposition structure without waiting for a future primary-stage arbiter. The view remains
non-authoritative for production, strategy or trading. Audit-only assertions are filtered rather than
silently upgraded.

This proves representation compatibility only. It does not turn an expert preview into a production
signal path.

## 9. First-milestone acceptance

The milestone is complete when contract tests prove:

1. all historical market codes 0 plus 1..12 map to unique stable proposition ids;
2. P027 evidence grade and authority cannot be mutated by bundle construction;
3. 10+11 coexistence is legal and visible to `development_shadow`;
4. research/parked assertions are excluded from that view;
5. neutral 0, ABSTAIN and X remain distinct;
6. parent, provenance and Fact-closure violations fail closed;
7. canonical output is deterministic and `primary_stage` is always null;
8. no new market/reference/holdout read occurs in this milestone.

## 10. Claim ceiling

P028 v1 is a representation/governance milestone. It does not calculate stage truth from market data,
qualify a new stage, publish a LabelCard, choose a primary stage, run daily inference, connect the
production expert path, authorize strategy consumption or grant trading authority.
