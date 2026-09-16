# P3-STAGE-028 Stage Proposition Bundle MVP acceptance

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-028` |
| Disposition | **PASS_FIRST_MILESTONE_STAGE_PROPOSITION_MVP** |
| Contract | `config/stage-learning/stage-proposition-bundle.v1.json` |
| Implementation | `src/quanthus/stage_proposition_bundle.py` |
| Examples | `config/stage-learning/stage-proposition-bundle-examples.v1.json` |
| Design | `docs/design/stage-proposition-bundle.md` |
| Four-party review | `docs/reviews/stage-p028-layered-bundle-four-party-review-20260916.md` |
| Result-bearing market read | none |

## 1. Phase goal achieved

The current stock-stage research phase now has a concrete executable output format instead of another
open-ended per-label experiment. The Stage Proposition Bundle represents one security and one knowledge
cutoff as layered assertions:

```text
context / structural state
+ event / nested conditions
+ modifiers
+ epistemic status
+ control state
+ exact Fact/provenance references
```

Historical stage numbers remain traceability metadata only. `primary_stage` is null in v1.

This is the agreed phase boundary: **individual-stock stage recognition MVP, phase 1, is achieved at
the representation/governance level**. It is not a claim that every historical Stage is qualified.

## 2. What is actually executable

The implementation provides:

- strict loading of the P028 contract and exact P027 evidence binding;
- immutable proposition catalog metadata for historical codes 0..12;
- construction and deep validation of layered bundles;
- parent/coexistence/ambiguity validation;
- strict separation of neutral 0, `ABSTAIN` and control `X`;
- deterministic canonical JSON serialization and digesting;
- exact Fact-reference closure;
- `development_shadow` expert-facing filtering;
- hard-null `primary_stage`;
- fail-closed rejection of evidence-grade, authority or primary-stage mutation.

The module performs no market read and contains no threshold-learning or stage-truth algorithm.

## 3. Consumer proof

The accepted nested Stage10/11 example produces, in the same Bundle:

```json
{
  "context_assertions": [
    {"historical_stage_code": 10, "verdict": "TRUE", "use_authority": "development_shadow"}
  ],
  "condition_assertions": [
    {"historical_stage_code": 11, "verdict": "TRUE", "use_authority": "development_shadow"}
  ],
  "modifier_assertions": [],
  "primary_stage": null
}
```

The `development_shadow` consumer view retains both assertions. This proves that a bounded expert
interface can consume the layered representation without waiting for Top-1 arbitration.

By contrast, the research example containing Stage5 advancing context plus Stage8 acceleration modifier
is valid on the audit surface but the `development_shadow` view contains neither assertion. That proves
representation does not promote research authority.

## 4. Neutral / abstain / control proof

The published examples include three distinct outputs:

| Case | Market assertion | Epistemic status | Control state |
| --- | --- | --- | --- |
| neutral 0 | Stage0 research context TRUE | ASSERTED | ACTIVE |
| abstain | none | ABSTAIN | ACTIVE |
| X | none | NOT_EVALUATED | X |

The validator rejects attempts to encode ABSTAIN while also asserting neutral 0 TRUE, and rejects X
with ordinary stage assertions. These meanings are therefore structurally separated rather than only
documented as prose.

## 5. Governance proof

Only evidence grade `accepted_development_projection` is admitted to `development_shadow`, currently
historical Stage10 and Stage11. Stage3/4 development candidates and all exploratory/research/no-pass/
parked propositions remain audit-only.

Evidence grade, layer, historical code, version and use authority are loaded from the frozen catalog.
A serialized caller cannot change them. Any mutation fails deep validation.

No LabelCard, production expert authority, daily inference, strategy consumption or trading authority
is created by this milestone.

## 6. Verification

Focused P027/P028 contract and unit verification before repository-wide acceptance:

```text
14 passed
ruff: all checks passed
```

Repository-wide `make check` is required before final closeout; its final result is recorded in the
closing commit/review rather than treated as evidence of market validity.

## 7. Stop rule

P028 closes the current large iteration. It does not automatically create P029.

The next step is a separate phase/value decision based on the delivered result. Possible future work
may include generating Bundles from real as-known runtime Facts, deepening selected missing propositions,
external semantic validation or bounded expert integration, but none is authorized merely because the
MVP representation now exists.
