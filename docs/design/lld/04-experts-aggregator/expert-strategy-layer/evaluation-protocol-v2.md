# P3-EXPERT-001: Expert Evaluation Protocol v2

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-EXPERT-001` |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | accepted by `docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md` |
| Compatibility boundary | additive sidecars only; accepted v1 objects and bytes remain frozen |

## 1. Outcome and boundary

Implement the domain-only artifact family that lets an expert assessment be observed, settled,
scored, connected to a separately versioned policy effect, and later receive one append-only human
lifecycle decision. This issue establishes identity, serialization, and validation. It does not
choose a target or decision, calculate a scorecard, execute a Job, define a strategy, or expose a
Web view.

The implementation must not add fields or new meanings to `ScoringOutput`, `ConstraintOutput`,
`VetoOutput`, `DecisionEvent`, or any accepted v1 Run. A new Run may reference a v2 sidecar, but an
old Run is never backfilled or resealed.

## 2. Inputs and owned paths

Inputs are the approved parent design, the accepted v1 protocols in
`src/quanthus/expert_protocols.py`, and canonical identity conventions already used by Quanthus.

The issue owns:

- `src/quanthus/expert_evaluation/__init__.py`
- `src/quanthus/expert_evaluation/contracts.py`
- `src/quanthus/expert_evaluation/serialization.py`
- `src/quanthus/expert_evaluation/validation.py`
- `tests/expert_evaluation_fixtures.py`
- `tests/unit/test_expert_evaluation_contracts.py`
- `tests/contract/test_expert_evaluation_compatibility.py`
- `tests/fixtures/golden/expert_evaluation_v2/`

Changes to `src/quanthus/expert_protocols.py`, `src/quanthus/aggregator.py`, or existing v1 golden
fixtures are out of scope. If an adapter is required, it belongs in the new package.

## 3. Contract set

All seven artifacts are frozen dataclasses with exact-field decoders and explicit schema versions.

| Artifact | Required binding |
| --- | --- |
| `ExpertEvaluationSpec` | assessment id/version, canonical ordered policy-mapping references, their admitted effect kinds, evidence route, population, partition, primary metric, baselines, slices, stop conditions, and optional `ForecastSpec` digest |
| `ForecastSpec` | target card id/version/digest, subject kind, eligibility rule, observation anchor, trading-session horizon, outcome type, resolver/scorer versions, exclusions, missing rules, and censoring rules |
| `ExpertObservation` | deterministic prediction id, subject, observation anchor, PIT cutoff, context/evidence digests, applicability, typed raw value, and evidence quality; it contains no policy mapping |
| `PolicyEffect` | observation digest, mapping id/version, effect kind, the complete accepted v1 output payload or explicit abstention, and its own evidence digests; at most one effect exists for each observation/mapping/effect-kind tuple |
| `OutcomeSettlement` | observation digest, settlement state, realized value when settled, observed window, future DWD identity, resolver version, target score, and typed missing/censor reason |
| `ExpertScorecard` | exact expert/target/population/partition binding, sample-ledger digest, metric definitions and values, uncertainty/stability evidence, baseline comparison, and decision-change counts; it contains no portfolio-effect value or attribution-release reference |
| `ExpertLifecycleDecision` | one of the six admitted decisions, a compatible enabled/disabled Shadow disposition, actor/time, rationale, limitations, complete assessment/policy/target/resolver/DWD/split/strategy/scorecard/attribution/package digests, and optional bounded observation budget |

Every `PolicyEffect` references an immutable observation digest. A policy-only expert emits an
observation with `raw_value_type=policy_only`, no forecast value, and its own PIT evidence. The
observation never references a mapping, so remapping unchanged evidence cannot change assessment
identity. This one-way relationship also avoids an identity cycle. A settlement references an
immutable forecast observation and never mutates it; policy-only observations are not settleable.
One evaluation spec may bind several separately versioned policy mappings for one assessment, as
required by the position-risk expert's entry and held-review surfaces. Specs with the same
assessment binding reuse the same observation identities; a mapping addition cannot duplicate or
rewrite those observations.

## 4. Identity and serialization

- Canonical content is UTF-8 JSON with ASCII escaping, sorted object keys, no insignificant
  whitespace, and explicit schema versions.
- Finite decimal values serialize as normalized decimal strings. Binary floating-point values,
  NaN, infinity, locale formatting, and implicit timezone conversion are rejected.
- Dates use ISO calendar dates. Datetimes must be timezone-aware and serialize in UTC with an
  explicit `Z` suffix. Transport timestamps, Job ids, claim generations, Worker ids, and artifact
  paths live outside canonical content.
- Collections that are mathematical sets are sorted by their declared stable key. Sequences whose
  order has meaning, such as horizon windows or decision traces, preserve declared order.
- Unknown fields, missing fields, duplicate identities, mixed schema versions, and non-canonical
  encoded bytes fail decoding.
- Every artifact digest is `sha256:` over canonical semantic content excluding only its own digest
  field. Parent digests and producer id/version are part of semantic content.
- `ExpertEvaluationSpec` exposes an `assessment_binding_digest` over only assessment id/version,
  forecast spec, population, partition, observation semantics, and assessment stop conditions. It
  excludes policy-mapping identity, economic interventions, and portfolio metrics.
- `prediction_id` is derived from that assessment-binding digest, subject identity, observation
  anchor, knowledge cutoff, and evidence-context digest. It contains no policy mapping, outcome,
  settlement, future DWD, score, transport time, or retry identity.

## 5. Cross-artifact validation

Validation is explicit and fail-closed:

1. Match assessment, target, the complete policy-mapping set, population, partition, and producer
   versions to the bound evaluation spec while validating the independent assessment-binding
   subdigest.
2. Prove the observation cutoff is not after its observation anchor and every evidence reference
   is visible at that cutoff.
3. Enforce exactly one raw value shape: probability, continuous, ordinal, or policy-only.
4. Require every policy effect to reference the exact observation, declared mapping, and admitted
   effect kind; reject reverse, duplicate, or dangling bindings. Policy-only observations cannot be
   settled or assigned forecast metrics.
5. Allow probability language and calibration metrics only for probability observations.
6. Require settlement state/value consistency. `pending`, `censored`, and `unavailable` cannot
   carry a fabricated target score.
7. Require the scorecard ledger to account for every eligible observation exactly once across
   applicable, abstained, settled, pending, unavailable, not-executable, and censored states.
8. Reject portfolio-effect values and attribution-release references inside a scorecard. A later
   immutable evidence-package manifest composes assessment scorecards with separately published
   economic releases by digest without rewriting either artifact.
9. Require lifecycle decision/disposition compatibility: `keep` is enabled; `inconclusive` may be
   enabled only with a finite budget; replacement, merge, and retire decisions are disabled. Reject
   an incomplete parent-evidence chain or an automated actor.

## 6. Implementation route

1. Add enums, value objects, exact validation, and reason-code errors in `contracts.py`.
2. Implement a single canonical encoder/decoder and digest helper in `serialization.py`.
3. Implement graph-level binding, PIT, ledger-closure, and metric-admission checks in
   `validation.py`.
4. Add golden bytes for one artifact of each type and negative fixtures for every typed failure.
5. Run all existing expert, aggregator, governed-strategy, DecisionEvent, and replay golden tests
   without updating their expected bytes.

## 7. Failure semantics

| Reason family | Required result |
| --- | --- |
| `unsupported_schema` / `field_mismatch` | reject before constructing a domain object |
| `invalid_identity` / `digest_mismatch` | reject the complete artifact graph |
| `future_evidence_at_observation` | reject publication; never downgrade to unavailable |
| `value_type_mismatch` / `invalid_probability` | reject the observation |
| `settlement_state_conflict` | reject the settlement without mutating the observation |
| `ledger_not_closed` | reject scorecard publication |
| `metric_not_registered` | reject scorecard publication |
| `lifecycle_disposition_conflict` / `automated_lifecycle_actor` | reject the lifecycle decision |
| `v1_compatibility_drift` | block the issue; existing golden files are not rewritten |

## 8. Verification and acceptance

The focused suite must prove:

- exact round trips and canonical bytes for all seven artifacts;
- input-map and set ordering do not change identities;
- future settlement, policy remapping, or adding another mapping cannot change `prediction_id` or
  observation digest, and duplicate mapping/effect-kind tuples fail closed;
- horizon edges, abstention, missing outcomes, censoring, suspension, and delisting retain distinct
  typed states;
- malformed decimals, timestamps, digests, unknown fields, and mixed versions fail closed;
- accepted v1 expert outputs, aggregator results, DecisionEvents, and replay digests are byte
  identical before and after the package is installed.
- no scorecard carries a portfolio-effect value or gains a new digest when a later attribution
  release is composed into an evidence package.
- incompatible lifecycle/Shadow states, incomplete evidence chains, or non-human actors fail
  validation before a decision object is constructed.

Acceptance evidence consists of the golden fixture manifest, focused test output, a v1 before/after
digest inventory, and the compatibility record in
`docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md`. No human semantic decision was
required for this protocol-only issue.

## 9. Rollback

The package is additive and has no migration. Rollback removes new imports and producers while
leaving any already published v2 artifacts immutable and unreadable by older code. Existing v1
execution continues unchanged. A rollback must never delete or rewrite accepted v2 evidence.
