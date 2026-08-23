# P3-EXPERT-004: Candidate-Detail Expert Evidence

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-EXPERT-004` |
| Design status | approved |
| Delivery gate | ready after `P3-ATTR-001` and `P3-EXPERT-002` acceptance |
| Implementation readiness | trusted evaluation and attribution artifacts are available |
| Authority | read-only projection; no calculation, approval, or execution rights |

## 1. Outcome and boundary

Add one unframed expert-evidence section to the existing candidate detail workflow. It shows the
formal topology, assessment scorecard, expert decision surfaces, bounded economic effects,
disagreement cases, limitations, and the current append-only lifecycle decision.

This is not a new dashboard, approval axis, expert leaderboard, or analysis engine. The browser
does not calculate a metric, settle an outcome, rank experts, issue a lifecycle decision, or change
review, governance, or execution state.

## 2. Inputs and owned paths

The projection reads a sealed formal strategy card, its predeclared product-catalogue candidate
id/version, a trusted expert-evaluation Run, a trusted formal attribution release, and an optional
lifecycle-decision artifact. Every value is resolved through an exact digest chain.

`config/product-expert-evidence.v1.json` is the serving binding from one exact candidate
id/version to one immutable expert-evidence package manifest and digest. The package references the
evaluation Run, attribution release, and optional lifecycle decision. The view never chooses the
latest Run or release by timestamp, database insertion order, or directory scan.

This Issue consumes the `ExpertLifecycleDecision` contract already frozen by `P3-EXPERT-001` and
publishes the binding schema plus the initial package with
`lifecycle_decision = null`. `P3-EXPERT-005` later publishes a new immutable package manifest that
adds the exact decision and Shadow-disposition digests, then atomically advances only this serving
entry. Neither Issue rewrites the scorecard, attribution release, or prior package.

The issue owns or extends:

- `src/quanthus/expert_evaluation/product_view.py`
- `config/product-expert-evidence.v1.json`
- `src/quanthus/platform/product_query.py`
- `src/quanthus/platform/api.py`
- `product-web/src/api.js`
- `product-web/src/views/CandidateDetail.vue`
- `product-web/src/styles.css`
- `tests/platform/test_product_expert_evidence.py`
- `product-web/tests/product-contract.test.mjs`
- `product-web/tests/e2e/product-web.spec.ts`

No POST/PUT/PATCH route is added for expert evidence or lifecycle decisions.

## 3. API view model

The existing candidate-detail response gains an optional `expert_evidence` object with exact
top-level fields:

- `state`: `not_applicable`, `not_available`, or `available`;
- `strategy_binding`: strategy/card/topology ids, versions, and digests;
- `experts`: ordered expert identity, role, active/Shadow mode, assessment type, target, policy
  mappings, applicability, coverage, abstention, and decision-change summary;
- `assessment_scorecards`: primary metric, baseline, uncertainty/stability, sample-ledger summary,
  and source digests;
- `economic_effects`: the two single and one joint effect summaries from the published attribution
  release;
- `disagreement_cases`: bounded preselected case references and reason codes, never browser-ranked;
- `limitations`: declared evidence, dependence, partition, and causal limitations;
- `lifecycle_decision`: exact decision or `null`;
- `source_digests`: all parent Run, scorecard, attribution, and decision digests.

The Python projection copies only registered fields from verified artifacts and formats no derived
financial fact. Display labels and date/decimal formatting may be presentation-only. The API schema
does not expose artifact filesystem paths.

## 4. Resolution and trust behavior

For a catalogue candidate with no formal expert-strategy binding, the response is
`not_applicable`. For a bound strategy whose predecessors have not published evidence, it is
`not_available` with stable missing-artifact reason codes.

Once the serving config declares an exact evidence binding, any missing file, `READY` failure,
checksum mismatch, cross-strategy identity, binding drift, incomplete scorecard ledger, or untrusted
attribution release makes the request fail closed. The API must not fall back to an older bootstrap
JSON, choose the latest record, or combine values from different Runs.

The candidate id/version in the serving entry must equal the product binding sealed by the formal
strategy card. A mismatch is rejected rather than treated as an operator-selectable presentation
choice.

The lifecycle decision is an immutable domain artifact from `P3-EXPERT-005`, not a candidate
approval event. Existing evidence-review, governance, and execution projections and transitions
remain byte-for-byte and behaviorally unchanged.

## 5. Web interaction

The candidate detail page adds these scan-oriented groups below existing replay evidence:

1. bound expert topology and active/Shadow roles;
2. per-expert assessment quality with coverage and abstention adjacent to the primary metric;
3. decision-change and complete-strategy effect summaries;
4. selected disagreement cases and limitations;
5. lifecycle decision and its exact evidence binding.

The layout uses existing table, status, disclosure, and tooltip conventions. It has no action
button. On mobile, each expert row becomes a labelled definition group without horizontal
overflow. Long ids and digests wrap or truncate with an accessible full-value affordance and never
resize action controls.

## 6. Implementation route

1. Implement exact serving-config and evidence-package decoding with checksum, identity, and
   parent-graph verification.
2. Build a pure Python projection whose output fields retain source-digest provenance and contain
   no analytical calculation.
3. Add the optional candidate-detail API field and all availability/error states without adding a
   mutation route or changing approval services.
4. Add the unframed Web section using existing table, disclosure, status, loading, error, and mobile
   conventions.
5. Publish one initial undecided package for the exact strategy-card candidate binding and capture
   API, browser, provenance, and authority-boundary evidence.

## 7. Failure semantics

`not_applicable` and `not_available` are product states, not synthetic zero values. An invalid
trusted binding produces a typed API error and error panel; it never renders partial evidence. A
missing lifecycle decision is displayed as undecided, not rejected, approved, or pending
governance.

The UI may retry a failed read. It cannot trigger expert evaluation, attribution, or a decision as
a side effect of loading the page.

## 8. Verification and acceptance

API tests cover all three availability states, exact digest resolution, cross-strategy rejection,
missing/partial artifacts, stale references, schema drift, role-independent reads, unchanged
approval state, and absence of expert mutation routes. Contract tests prove every displayed
numeric field originates in a verified artifact and the projection performs no aggregation.

Desktop and mobile browser tests cover topology, scorecards, economic effects, limitations,
undecided lifecycle display, a digest-bound decided fixture, loading, empty, invalid, and retry
states. The live post-decision serving update and Shadow-disposition display are accepted by
`P3-EXPERT-005`. Tests also prove no expert evidence action can authorize an Order or change
review/governance/execution counts.

Acceptance artifacts are API/OpenAPI fixtures, projection provenance tests, desktop/mobile
screenshots, browser E2E output, and an authority-boundary review.

## 9. Rollback

Remove the optional projection/section and its serving binding while leaving candidate detail,
approval workflow, and all analytical artifacts intact. Because the response field is additive and
optional, older Web clients continue to work. A rollback cannot delete lifecycle decisions or
trusted evidence.
