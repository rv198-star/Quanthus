# Strategy Attribution Kernel

| Field | Value |
| --- | --- |
| Parent | `../10-monitoring-attribution.md` |
| Phase | P3+ |
| Design status | approved |
| Delivery gate | blocked |
| Implementation status | bounded synthetic closure accepted with a versioned method registry, resolver-backed receipts, an outcome-blind immutable planner, a resumable synchronous Pipeline, an atomic publisher, five local CLI commands, and a checksum-gated read-only viewer; formal attribution runtime and recurring reporting remain deferred |
| Gate source | `config/p3-gates.v1.json` |
| Counterfactual policy | `config/p0-policy.v1.json` |
| Governing work | `P3-ATTR-001`, `P3-DASH-001` |
| Governing requirements | FR-AUDIT-001 through FR-AUDIT-005 |
| Governing code | `src/quanthus/strategy_attribution.py`, `src/quanthus/strategy_attribution_planning.py`, `src/quanthus/strategy_attribution_pipeline.py`, `src/quanthus/strategy_attribution_publication.py`, `src/quanthus/strategy_attribution_demo.py`, `web/app/` |
| Focused verification | `tests/unit/test_strategy_attribution*.py`, `web/tests/rendered-html.test.mjs` |
| Acceptance evidence | `docs/acceptance/p3-attribution-synthetic-publication-20260731.md`; synthetic plumbing only, with no formal-strategy or gate-resume evidence |

## 1. Purpose

Determine how a versioned strategy component changed a strategy result, under which conditions,
and with what strength of evidence. A component may be a feature or feature group, market-state
router, candidate producer, expert, aggregator, veto, risk rule, allocator, position rule, or exit
rule.

The target is not one universal contribution percentage. The kernel produces a metric vector and
an evidence-backed claim such as:

- removing expert `E7` reduced return and increased drawdown in the frozen replay;
- risk gate `R2` avoided a bounded loss but also rejected profitable opportunities;
- feature group `F3` was useful only in two predeclared market states;
- two experts were individually replaceable but jointly useful; or
- the available evidence is insufficient to distinguish value from redundancy.

Tracking is the evidence substrate. Attribution is the analysis product. Any Web workbench is only
an interface over versioned questions, experiments, claims, and explanation views.

## 2. Scope and invariants

### 2.1 Current scope

The first formal implementation is limited to predeclared paired experiments against one frozen formal
strategy baseline. It supports legal component removal or neutralization, complete
capital-constrained replay, metric deltas, bounded pairwise interaction checks, condition cuts, and
three-layer explanation output. Removal or neutralization changes only the declared component;
every other policy and execution version stays bound to the baseline.

The current `config/p0-policy.v1.json` contract requires the same exit-policy version as the
baseline. Exit-policy replacement and feature deletion followed by retraining are therefore future
sensitivity designs, not legal first-delivery counterfactuals. This child cannot broaden a frozen
policy by naming an intervention.

The attribution design is approved before a formal strategy exists so replay and evidence costs
are visible early, not so that the unknown strategy must adopt this document's component model.
The adapter and its truthful capability manifest are defined after the strategy semantics exist.
Formal-strategy runtime implementation does not start until the resume conditions for `P3-ATTR-001` and
`P3-DASH-001` in `config/p3-gates.v1.json` are met.

### 2.2 Meaning of deterministic

Deterministic means the same bound question, data, code, model, policy, experiment specification,
resolved semantic artifact bytes, method-registry and verifier versions, producer versions, and
random seeds produce the same canonical semantic artifact bytes, verification receipts, metrics,
classifications, and explanation facts. Publication time and transport metadata are excluded from
the semantic content digest and byte comparison. Determinism does not mean that a historical result
is certain to persist or that every simulator contrast is a real-world causal effect.

The kernel fails closed when method dispatch, artifact resolution, registered production,
baseline reproduction, intervention legality, evidence completeness, or comparison identity cannot
be established. A narrative layer cannot repair failed evidence.

### 2.3 Non-goals

This node does not authorize:

- platform, data-pipeline, host, or service monitoring;
- a generic online experimentation platform;
- an attribution-specific strategy topology, module taxonomy, or data schema;
- strategy tuning, promotion, or access to unopened evaluation partitions;
- a formal or recurring dashboard, attribution scheduler, or formal-strategy runtime implementation;
- forced allocation of 100% of return or maximum drawdown across modules;
- automatic module removal based on one backtest; or
- LLM calculation, evidence grading, or causal judgment.

Backtest validity remains a prerequisite. Attribution cannot rescue leakage, invalid point-in-time
inputs, an unrealistic matcher, overfitting, or a non-reproducible baseline.

### 2.4 Bounded prototype status

`src/quanthus/strategy_attribution.py` is an executable conformance prototype for the stable
contracts in this document. `src/quanthus/strategy_attribution_demo.py` supplies only a fabricated
adapter, a registered synthetic artifact producer, and locally resolvable synthetic experiments so
determinism, fail-closed publication, capability negotiation, identity binding, evidence-receipt
derivation, condition and interaction evidence, and the three explanation projections can be
tested before a formal strategy exists. The fixture is permanently labeled
`synthetic_fixture_not_strategy_evidence`; it cannot satisfy a resume condition, be tuned, or grant
delivery authority to `P3-ATTR-001`.

`src/quanthus/strategy_attribution_pipeline.py` persists the outcome-blind request and plan before
execution, writes immutable per-node execution and verification receipts, resumes only incomplete
nodes, and reconstructs the evaluated result from verified bytes. The first implementation is a
local synchronous process; it does not add a scheduler, service API, queue, or database.

`src/quanthus/strategy_attribution_publication.py` independently rebuilds the deterministic kernel
evaluation, materializes the complete lineage and workspace closure in staging, verifies it, and
publishes it atomically with `CHECKSUMS.sha256` and `READY`. The five local CLI commands are
`attribution-plan`, `attribution-run`, `attribution-status`, `attribution-resume`, and
`attribution-verify`; their only executable fixture choice is explicitly synthetic.

`web/` is a thin read-only viewer over the versioned workspace artifact. It may validate the v1
workspace shape, select records, translate registered labels, preserve and download original JSON
text, verify the publication transport chain from `READY` through `CHECKSUMS.sha256` to the exact
release and workspace bytes it reads, and render already-published values. It strictly decodes
`READY` as the expected checksum-ledger digest, hashes the raw ledger before decoding it, and
hashes the raw release/workspace responses before decoding them; lossy replacement-character
decoding cannot satisfy a byte-level checksum and then enter JSON parsing. It does not recompute
effects, resolve evidence, issue verification receipts, validate semantic attribution digests,
rank contributors, aggregate risk and opportunity money, assign evidence, or recommend strategy
changes. An imported file is therefore displayed as unverified unless the authoritative parser
and verifier established its trust outside the browser.

This prototype makes integration costs visible without defining a strategy topology. A future
formal-strategy implementation still begins with its native semantics and a separately versioned
adapter after the machine-readable gate resumes the governing work.

## 3. Stable kernel contracts

The strategy owns its topology, data model, decision semantics, and native artifacts. The kernel
consumes a versioned `StrategyAttributionAdapter`; it does not require strategy code to import an
attribution schema or implement an expert/state/feature-shaped pipeline. Rule systems, trained
models, optimizers, expert ensembles, and monolithic strategies are all valid inputs.

An adapter may expose a detailed native component graph or only one strategy-root component. The
available attribution granularity follows that truthful boundary. The kernel never invents hidden
components from free text, column names, model internals, or file layouts, and it never requires a
fixed state count, expert count, feature table, candidate funnel, action vocabulary, or training
framework.

| Contract or artifact | Required meaning |
| --- | --- |
| `AdapterCapabilityManifest` | Versioned mapping from native strategy artifacts to the optional replay, component, intervention, trace, condition, and metric capabilities the adapter actually supports |
| `AttributionQuestion` | The reusable predeclared estimand, intervention, population roles, outcomes, and decision rule |
| `AttributionRunRequest` | Frozen adapter, question, PIT context, period, seed, metric-calculator, method-registry, and no-outcome-access bindings supplied to the plan compiler |
| `AttributionPlan` | Immutable compiler output containing deduplicated question-neutral work and a complete per-question dependency projection |
| `PlannedExperiment` | One content-identified physical replay or analysis unit without result values, result artifacts, receipts, or caller-asserted validation facts |
| `ComponentContract` | Adapter-declared native component identity, open kind, role, versions, dependencies, legal interventions, and fallback semantics |
| `AttributionMethodRegistration` | Versioned binding from a method id and method kind to the kernel implementation that can actually execute or enforce it |
| `ExperimentManifest` | One exact baseline instance, declared intervention set, estimator, data/period, execution assumptions, and artifact identities |
| `SemanticArtifactEnvelope` | Canonical externally produced evidence bytes with schema, kind, producer identity/version, source kind, exact parent digests, payload, and content digest |
| `ArtifactResolver` | Content-addressed read boundary that returns the exact bytes named by an artifact digest; it grants access, not trust |
| `ArtifactVerificationReceipt` | Verifier-derived record of artifact identity, producer, parents, checked facts, pass/fail result, and reason codes |
| `MetricSpec` | Versioned metric definition with a semantic digest, preference rule, materiality threshold, unit, and display contract |
| `MetricEffect` | Baseline value, intervention value, raw delta, direction-oriented effect, units, scope, and uncertainty |
| `AttributionClaim` | Machine-derived classification, evidence type/validation flags, interaction state, limitations, and evidence references |
| `ExplanationView` | Three projections of the same claim for decision, analysis, and audit readers |
| `AttributionWorkspace` | Canonical closure containing every question, experiment, evidence object, semantic artifact, receipt-backed claim, and reference required to reproduce its claims |
| `AttributionReleaseManifest` | Source-kind-bound publication identity joining one verified Pipeline result to its closed workspace and separating synthetic display claims from formal publishable claims |
| `AttributionPublicationCatalog` | Stable-path index rebuilt only from complete, deeply verified `READY` releases for read-only Web discovery |

Every kernel semantic object has a stable schema or contract version, canonical content digest,
and explicit parent identities where applicable. Every externally produced result or trace that
can affect publication additionally uses `SemanticArtifactEnvelope`; a digest-shaped string without
resolvable canonical bytes is not evidence. A transport wrapper may add creation time and delivery
metadata, but those fields do not change semantic identity. Domain-specific payloads may be
extended, but these core fields and their meanings cannot be replaced by an opaque blob.

### 3.1 Predeclared question

An `AttributionQuestion` is bound before any target experiment starts and before target-period
outcomes are read. It declares:

- subject component roles and compatible version bounds;
- whether the estimand is current-model dependency, declared-pipeline data reliance, routing
  value, veto/risk value, allocation value, or another registered kind;
- legal intervention and replacement semantics;
- registered effect, classification, multiple-comparison, uncertainty, and stability method ids;
- primary and diagnostic metrics, their preference rule, and materiality thresholds;
- target population and predeclared time, market-state, lifecycle, or other condition-cut rules;
- development, replication, and independent-evaluation sample roles;
- hypothesis-family ids, confirmatory/exploratory roles, and any multiple-comparison or independent
  confirmation rule;
- required interaction checks and the rule for selecting additional pairs;
- uncertainty or stability procedure;
- claim classification and decision mapping rules; and
- known assumptions, exclusions, and stop conditions.

Method fields are executable identifiers, not prose instructions. When condition or interaction
specifications exist, the kernel also binds their registered evaluation methods. The question
digest covers these choices; reading outcomes and then changing a method id creates a new question,
not a reinterpretation of the old result.

Each `ExperimentManifest` then instantiates that question with exact strategy, component, model,
policy, data snapshot, eligible universe, period, capital, matcher, fees, seeds, artifact identities,
metric-calculator id/version, and the digest of every metric definition used for its values. One
claim may summarize multiple compatible manifests without pretending they were one run. An
exploratory result whose outcome was read before the question digest was bound remains exploratory;
it may generate a later question but cannot be relabeled as predeclared evidence.

### 3.2 Capability negotiation

Attribution is capability-based, not a conformance template for strategy design. The adapter
declares each capability independently:

| Capability | Enables | Absence means |
| --- | --- | --- |
| `baseline_replay` | Reproduce a native strategy result from an exact manifest | No counterfactual replay claim |
| `component_map` | Attribute below the whole-strategy root | Coarser attribution, not strategy invalidity |
| `legal_intervention` | Compare one native component intervention | That component is `not_intervenable`; no guessed neutral value |
| `decision_trace` | Explain which native decisions first diverged | Portfolio delta may remain valid, but decision-level explanation is unavailable |
| `opportunity_trace` | Analyze rejected, unallocated, or displaced opportunities under native semantics | Avoided-loss/opportunity decomposition is `not_identifiable` or `not_applicable` |
| `condition_map` | Produce predeclared state, regime, or lifecycle cuts | No condition-specific claim |
| `training_replay` | Compare a declared retraining procedure | Retraining remains unavailable sensitivity work |

A paired portfolio claim additionally requires a versioned metric registry and a full replay that
preserves the strategy's own capital competition, matching, costs, settlement, and position state.
Complete candidate and held-review evidence is required only for a claim about opportunities; it is
not a universal shape imposed on strategies without a candidate concept.

The adapter may reuse existing Quanthus audit events, but those events do not define the strategy's
internal architecture. Missing capabilities produce narrower or unavailable claims, never a demand
that the strategy be redesigned around the attribution system. A capability manifest may declare
native semantics, an experiment or check manifest may name artifact digests, and the adapter may
expose an `ArtifactResolver`; none may set claim status or validation flags, issue a trusted
receipt, or make a caller-supplied `true` authoritative.

### 3.3 Evolution boundary

The first adapter is an anti-corruption boundary between two independently evolving modules. A
manifest binds `strategy_version + adapter_version + capability_manifest_version`, so adapter
changes cannot reinterpret an old strategy run silently.

After both the strategy system and attribution runtime have concrete operating evidence, a separate
approved integration change may add native trace hooks or shared contracts. That change must name
the measured gap it closes, preserve old adapter replay, and prove that it does not change strategy
decision semantics by implication. Convenience for attribution alone is not authority to redesign
the strategy.

### 3.4 Versioned method registry

The kernel executes only methods present in its immutable versioned registry. Each registration
binds `method_id + method_version + supported method kind + implementation_id`. A question that
names an unknown method, or uses a registered method under the wrong kind, fails closed. There is no
default estimator behind an unrecognized string. Every valid claim records the registry version
and the method ids actually applied, so a later registry cannot silently reinterpret it.

The bounded prototype registry is `quanthus.strategy-attribution-method-registry.v1` and currently
contains only the methods it implements or explicitly enforces:

| Method id | Registered use |
| --- | --- |
| `paired_full_replay_effect_v1` | Baseline-minus-intervention metric effects |
| `predeclared_condition_difference_v1` | Predeclared condition effects |
| `pairwise_interaction_contrast_v1` | Bounded two-intervention interaction contrast |
| `primary_metric_dominance_with_materiality_v1` | Overall classification from primary metric effects |
| `no_cross_module_ranking:synthetic_fixture_v1` | Fixture rule that forbids an undeclared cross-module ranking |
| `not_required:synthetic_contract_fixture_v1` | Fixture-only marker for an explicitly non-required uncertainty or stability check |

The last two registrations do not authorize a formal multiple-comparison, uncertainty, or
stability procedure. Adding or changing a real method requires an approved estimand, implementation,
failure rules, tests, and a versioned registry change. Appendix A remains unregistered research
input until that separate change occurs.

### 3.5 Resolvable evidence and verifier receipts

Evidence trust is one directional chain:

1. A registered executor or analysis producer emits canonical `SemanticArtifactEnvelope` bytes.
2. An experiment or analysis-check manifest references their content digest, and the adapter exposes
   a resolver that can return those exact bytes.
3. The kernel verifier parses the bytes and checks schema, content digest, artifact kind, source
   kind, registered producer id/version, exact parent set, and the expected payload binding.
4. The verifier emits a deterministic `ArtifactVerificationReceipt` containing its own id/version,
   subject id, artifact and producer identities, parent digests, verified facts, pass/fail state,
   and reason codes.
5. The evaluator derives publishability, validation flags, and claim evidence references from those
   receipts and deterministic validators; the adapter does not provide those conclusions.

Boolean fields in a manifest are expected facts to bind, not proof. For example,
`reproduced=true`, `reconciled=true`, or `stability passed=true` contributes to a claim only when the
same fact is read from an exact, registered-producer artifact and appears in a passing verifier
receipt. A missing resolver, a nonexistent but well-formed digest, modified bytes, a wrong producer,
a cross-question parent set, or a payload mismatch produces a failed receipt and cannot yield a
`valid` directional claim. Receipts are canonical semantic records; this prototype does not claim
that they are cryptographic signatures.

The bounded prototype recognizes only its explicitly registered synthetic producer. Registering a
formal replay or analysis producer is part of a later formal-strategy implementation and does not
resume either P3 gate by itself.

A published `AttributionWorkspace` must be a closed evidence package. Every artifact digest named
by its experiments and checks must resolve inside the package, extra unreferenced artifacts are
rejected, and every claim must resolve its question, baseline, intervention, supporting evidence,
and receipt digests. The authoritative parser reconstructs an in-memory resolver, deterministically
re-evaluates each claim, and requires the rebuilt claim digest to match. This closure is established
before Web publication; the thin browser viewer does not recreate or upgrade it.

### 3.6 Outcome-blind plan compilation

`src/quanthus/strategy_attribution_planning.py` compiles frozen definitions into a deterministic
dependency graph before any executor or result resolver is available. Its only input is an
`AttributionRunRequest`: the exact capability manifest and its expected binding, the complete
predeclared questions, nine replay-context bindings including seeds, the period, the metric
calculator id/version, and the method-registry version. The request mandates point-in-time and
capital-constrained execution and explicitly forbids target-outcome access until the canonical plan
has been persisted. Those requirements are instructions, not evidence that execution later passed.

`PlannedExperiment` is deliberately not `ExperimentManifest`. It contains metric-definition
digests but no metric values, artifact or trace digest, replay/reconciliation boolean, analysis
result, receipt, or validation flag. A
planned node may be a baseline replay, one legal intervention, one adapter-declared joint
intervention, a predeclared condition analysis, or a registered required uncertainty/stability
check. Physical nodes are content-identified from adapter semantics, replay context, metric
definitions, calculator version, and the minimum capabilities implied by their node kind; they do
not include a question id or consumer-derived capability set. Derived analysis identities also bind
their exact parent experiment identities, while physical replay identity remains reusable across
questions with the same execution semantics.

`PlannedQuestion` supplies the other half of the contract. It binds every ready question to the one
shared baseline, its subject intervention, each required partner and joint intervention, every
condition analysis, and each required check. This permits one physical baseline or intervention to
serve multiple questions without losing any Claim parent. A non-ready question has one of two
closed dispositions and no executable edge:

- `unavailable` means a required capability or implemented registered method is absent;
- `rejected` means the frozen request is semantically invalid or drifted, such as an unbound
  question, adapter/version mismatch, undeclared intervention, joint-member mismatch, composition
  order conflict, or conflicting metric definition.

The compiler never branches on component `kind`, `role`, code, table, column, or free text. It reads
only question-declared identities and adapter-owned legal intervention contracts, so a one-node
opaque strategy and an arbitrary future component vocabulary use the same algorithm. Canonical
question ordering and mapping ordering do not change request or plan bytes. The compiler itself
does not prove that the question preceded outcome access: the local Pipeline in the next delivery
must persist the canonical request and plan before its first executor call and record that state
transition as audit evidence. Plan experiments are emitted in stable topological order with content
identity as the same-level tie-break. An `AttributionPlan` also recompiles its embedded request and
requires byte-identical nodes and question bindings, so callers cannot promote an unavailable
question or substitute different adapter semantics by reconstructing the dataclasses.

### 3.7 Publication and read-only consumption

`AttributionPublisher` accepts only an evaluated Pipeline result whose persisted request, plan,
node receipts, evaluation bundle, claims, and source identity reconstruct successfully. It reruns
the deterministic kernel instead of trusting an executor's conclusion, materializes the full
workspace closure in unique staging, writes the canonical `CHECKSUMS.sha256`, writes `READY` last,
deep-verifies the staged tree, and atomically renames the release. A duplicate publication is
idempotent only when the complete semantic release is identical; conflicting bytes fail closed.

Catalog refresh discovers releases from disk rather than trusting caller-supplied entries. It
ignores incomplete or symlinked directories, fails the whole refresh when any discovered `READY`
release is malformed, tampered, or fails deep verification, and atomically writes stable release,
workspace, checksum-ledger, and readiness paths only after the complete discovered set passes.
Synthetic source identity uniquely forces `synthetic_fixture=true`,
`formal_strategy_evidence=false`, and an empty set of formal publishable claims; changing those
flags and resealing transport checksums does not pass semantic verification.

The Web loader treats the catalog as discovery metadata and establishes transport trust separately
for every release. It restricts paths to the catalog directory, reads `READY` and the checksum
ledger, hashes the exact `ArrayBuffer` response bytes, strictly decodes UTF-8 only after the
applicable byte check passes, and then reconciles catalog, release, and workspace identities. A
failed release remains absent and visible as a load error without contaminating other valid
entries. Browser publication trust means that an authoritative publisher previously produced the
artifact and that transport bytes match; it does not mean the browser independently reproduced
semantic evidence verification.

## 4. Evidence model

Evidence type states what kind of question an artifact can answer. Validation flags state which
checks that evidence passed. These axes remain separate: repetition does not turn association into
causation, and a sophisticated estimator does not repair an invalid comparison.

### 4.1 Evidence types

| Type | Answers | Maximum claim wording |
| --- | --- | --- |
| `accounting` | Where observed baseline PnL, costs, exposure, or risk came from | "was observed in" or "accounted for"; not component causality |
| `association` | Which scores, states, experts, or decisions co-occurred with outcomes | "was associated with"; not incremental or unique value |
| `sensitivity` | How outputs respond to a bounded perturbation, scenario, or retraining procedure | "changed under this perturbation"; not a valid portfolio counterfactual unless the full replay contract also passes |
| `counterfactual_replay` | How the frozen simulator changes under one legal intervention | "changed in this frozen historical replay"; not future real-world causality or additive ownership |

### 4.2 Validation flags

| Flag | Meaning |
| --- | --- |
| `baseline_reproduced` | The exact bound baseline reproduced from canonical semantic artifacts |
| `paired_comparable` | Only the declared intervention differs and full capital-constrained paths reconcile |
| `interaction_checked` | Every interaction required by the question has a valid compatible joint experiment |
| `condition_checked` | Every predeclared applicable condition cut was evaluated without post-hoc relabeling |
| `stability_checked` | The question's declared resampling or repeat-period checks passed |
| `independent_evaluation_completed` | A compatible manifest on an untouched, non-overlapping, or genuinely external sample completed, regardless of outcome |

A claim also has `valid`, `invalid`, or `unavailable` status with reason codes. Missing required
flags make a claim incomplete or cap its wording; they do not silently become `false`. In
particular, `baseline_reproduced`, `paired_comparable`, and `stability_checked` are kernel-derived
only after their required artifact receipts pass. Condition and interaction flags are likewise
assigned by their registered deterministic validators over fully identity-bound evidence. A
capability declaration, manifest boolean, or adapter-supplied claim cannot self-certify any flag.
For non-specialist display, a deterministic formatter may summarize a counterfactual claim as
"full replay", "interaction checked", or "independently evaluated" while retaining its evidence
type and every flag. A separate replication outcome is `confirmed`, `refuted`, `heterogeneous`, or
`inconclusive`; failed replication remains visible. An independently evaluated association remains
an association.

Evidence type, validity, flags, and claim wording are assigned by deterministic rules. A language
model may translate those fields but cannot calculate, merge, or upgrade them.

## 5. Minimum attribution method

For a declared component `M` and its exact legal intervention `I_M`, the current estimator is a
registered paired full replay:

1. resolve every declared method id against the evaluator's versioned method registry;
2. resolve and verify registered-producer evidence that frozen baseline `B` reproduced from its
   exact manifest;
3. validate that `I_M` is legal and changes no undeclared contract;
4. replay the complete strategy as `B[I_M]` under the same PIT inputs, eligible security universe,
   capital, matching, costs, settlement, and comparison horizon;
5. resolve and verify the intervention result and every native trace required by the question,
   then compare the regenerated native candidate and decision streams supported by the adapter;
6. compute every predeclared applicable metric effect and condition cut using registered methods;
7. run required pairwise experiments or attach an explicit untested-interaction caveat; and
8. publish the claim only if every required receipt, identity, count, and monetary path reconciles.

An `ExperimentManifest` remains the comparison specification and expected fact set. Its own
`reproduced`, `reconciled`, PIT, authorization, trace, or analysis-check booleans never satisfy a
validation rule without matching resolver bytes and a verifier-issued receipt. The resulting claim
binds the method-registry version, applied method ids, and receipt digests.

For metric `k`, the raw effect of the baseline relative to `I_M` is:

`raw_effect_k(I_M) = metric_k(B) - metric_k(B[I_M])`

Every `MetricSpec` declares one preference: `higher`, `lower`, `target_range`, or `unoriented`.
For `higher` and `lower`, the stored direction-oriented effect is respectively `raw_effect` and
`-raw_effect`. For `target_range`, a positive effect means the baseline is closer to the
predeclared target range than the intervention. An `unoriented` metric has no oriented effect.

Both original metric values and the raw effect remain visible; the oriented value cannot replace
them. Percentage-point and relative-percent changes are never conflated. A component receives a
global `beneficial` or `harmful` label only when all primary metrics dominate in one direction or
the question predeclares an auditable utility rule. Otherwise the overall result is `tradeoff`.

### 5.1 Open metric vector

The kernel does not impose a universal metric list. A question selects a versioned metric vector
from the strategy adapter and report profile. Useful metric families include, when they have valid
native meanings:

- return and PnL: ending PnL, total return, annualized return, and benchmark excess;
- path risk: maximum drawdown, recovery duration, volatility, and a registered tail-risk metric;
- opportunity: captured opportunities, losses avoided, profitable opportunities rejected, and
  capital-displacement effects;
- trade quality: win rate, payoff ratio, profit factor, holding time, and concentration in the
  largest trades; and
- implementation: exposure, capital use, turnover, fees, slippage, and unfilled decisions.

The three parent-LLD north stars are the current Quanthus report profile, not required fields in the
kernel schema. An adapter maps native strategy evidence to them only when their semantics hold;
otherwise it returns `not_applicable` with a reason. The question names primary metrics before
execution. A favorable return delta cannot hide a materially worse drawdown, and a lower drawdown
cannot hide the return or opportunity cost paid for it. There is no default formula that collapses
the vector into one score or weight.

### 5.2 Risk avoided and opportunity cost

The diagnostic hypothetical ledger may identify candidate examples but is not official money. A
full capital-constrained replay identifies the net difference of each declared portfolio metric;
it does not automatically identify additive gross buckets for protected losses, forgone profits,
and capital reallocation.

For a risk-policy neutralization `I_R` and a registered nonnegative adverse metric `L` where higher
is worse, official risk avoided is:

`risk_avoided_L(I_R) = L(B[I_R]) - L(B)`

For a registered benefit metric `G` where higher is better, the opportunity cost of retaining the
baseline policy is:

`opportunity_cost_G(I_R) = max(0, G(B[I_R]) - G(B))`

Each value keeps its metric name, unit, horizon, and raw baseline/intervention values. Risk avoided,
opportunity cost, and net return effect are not summed unless a separate accounting identity proves
that they reconcile.

When `decision_trace` and `opportunity_trace` exist, the explanation may link first decision
divergences to later paths using a declared matching rule. Protected losing paths, forgone
profitable paths, execution/cost changes, and capital-reallocation effects remain diagnostics with
an explicit residual. If one-to-many path divergence prevents identification, the component is
`not_identifiable` at that layer rather than assigned invented money.

## 6. Adapter-declared intervention semantics

Removal is not one universal operation. The adapter records a strategy-native intervention and
rejects undeclared fallback behavior. The following rows are non-exhaustive examples, not a module
taxonomy that a strategy must implement.

| Native component example | Current legal first choices | Meaning and guardrail |
| --- | --- | --- |
| Feature or feature group | `mask_without_retrain` | Measures current pipeline dependency. Zero is neutral only when the native contract says so; affected candidate and decision streams are regenerated. |
| Observational market state | condition cut only | A label that does not change a decision has no module-removal effect. It may describe where another component works. |
| Active state router | `neutralize_native_route` | Use the strategy's predeclared native neutral route or mixture; deleting a state name without route semantics is invalid. |
| Candidate producer | `disable_native_branch` | The exogenous PIT eligible universe stays fixed; the endogenous candidate set is regenerated and may differ. |
| Scoring expert | `abstain`, `neutral_opinion` | Native aggregation behavior after abstention or neutrality is frozen in the intervention and cannot change implicitly. |
| Veto or policy risk gate | `bypass_policy_gate` | Only the attributed policy gate changes. PIT, data-quality, market, accounting, capital, and execution invariants remain active. |
| Aggregator or allocator input | `neutralize_native_input` | The strategy's native fallback and capital limits remain fixed. An unbounded allocator is not neutralization. |
| Position or exit rule | `not_intervenable` under the current P0 policy | The same exit-policy version remains bound to both replays; only accounting or association claims are currently allowed. |

Non-intervenable regulatory, point-in-time, identity, accounting, settlement, and matcher
invariants may be varied only as separately labeled scenario or sensitivity analysis. They are not
strategy-module attribution.

`drop_and_retrain` is future sensitivity work. If later authorized, it measures conditional system
value under one declared training algorithm, feature set, search/early-stop budget, data roles, and
seed protocol; it is not intrinsic or unique feature information value. Its derived model identity
must be bound independently from the deployed-model masking experiment.

## 7. Interaction and condition handling

Single-intervention effects are not additive. For compatible interventions `I_A` and `I_B`, the
metric-specific pairwise interaction contrast is:

`interaction(I_A,I_B) = V(B) - V(B[I_A]) - V(B[I_B]) + V(B[I_A,I_B])`

The joint `InterventionSpec` must declare compatibility, composition order, and conflict handling.
If the interventions are order-dependent, each reported contrast names its order and cannot be
presented as a symmetric interaction. It is reported in the metric's original units and preference
direction, and is not automatically a percentage share. Material single-component claims receive
predeclared pairwise checks against known dependencies and the largest plausible co-controller;
otherwise `interaction_checked=false` remains visible.

Condition cuts use only state definitions and thresholds frozen before results are inspected.
Post-hoc slices are labeled exploratory. A metric-scoped effect may be `beneficial`, `harmful`, or
`negligible`; a component may additionally be `synergistic`, `substitutable`, `state_dependent`,
`tradeoff`, or `inconclusive`. Classification thresholds and the mapping to actions such as retain,
simplify, route, remove-candidate, or gather-more-evidence live in the question, not in prose
generation.

Selecting the largest contributor across modules, metrics, slices, or interactions is a hypothesis
family, not a free ranking. Without a predeclared correction or an authorized independent
confirmation, that ranking is exploratory even when each underlying replay is valid.

Maximum drawdown is path-dependent and may change peak/trough identity after an intervention. Its
delta is valid as a paired portfolio outcome but is not required to decompose additively across
modules.

## 8. Three-layer explanation contract

All three layers are projections of one `AttributionClaim`; they do not run separate calculations.

### Layer 1: decision summary

For a non-specialist reader, show:

- a plain-language conclusion: helped, hurt, state-dependent, replaceable, or insufficient;
- the two or three primary metric changes with baseline and comparison values;
- evidence type, validity/validation flags, and applicable strategy, period, and conditions;
- the largest known trade-off or caveat; and
- a bounded next action such as retain, test a pair, simplify, or collect more evidence.

An illustrative, fabricated rendering could say: "In this frozen six-month full replay, expert E7
appears beneficial on both primary metrics. With E7, return was 99% and maximum drawdown was 5%;
under its declared abstention intervention, return was 72% and drawdown was 9%. E7 therefore
improved return by 27 percentage points and reduced drawdown by 4 percentage points in this
simulator. Baseline reproduction and paired comparability passed; interactions are not yet checked,
so test E7 with the state router before a broader decision." This does not say E7 owns 27/99 of
total return or that the effect will persist in live trading.

### Layer 2: reason analysis

Show the selected metric vector, available opportunity/risk diagnostics, first decision
divergences, important trades and drawdown windows, condition cuts, interactions, stability
results, unavailable capabilities, and the reason the classification rule fired.

### Layer 3: method and evidence

Show question and experiment manifests, component/intervention semantics, baseline and replay
identities, metric definitions, method-registry version and applied method ids, comparison formula,
uncertainty method, producer and verifier versions, verification receipts, reconciliation results,
workspace-closure status, evidence type, validity/validation flags, replication outcome, residuals,
assumptions, failures, and artifact links.

### Translation guardrails

The explanation translator may select, order, define, and paraphrase structured facts. It must:

- preserve values, signs, units, ids, time scopes, evidence types/flags, and uncertainty;
- distinguish observed association from paired simulator counterfactuals;
- say "in this frozen historical replay" for counterfactual claims;
- expose a material adverse metric even when the primary metric improved; and
- render missing or invalid evidence as unknown rather than infer a result.

It must not invent a cause, calculate a new metric, hide a residual, choose a preferred strategy,
upgrade evidence, or turn a module delta into a percentage of total contribution. A deterministic
template remains the fallback when no language model is available.

## 9. Publication failures

An experiment or claim is `invalid` and cannot publish a directional conclusion when any of these
holds:

- a declared method is unregistered, registered for a different method kind, or not the method
  actually applied;
- an artifact resolver is missing, a digest cannot be resolved, canonical bytes or schema are
  invalid, or artifact kind, source, producer, parents, or payload do not match the declaration;
- a required verifier receipt fails or cannot be deterministically rederived for the exact subject
  and parent identities;
- baseline reproduction or bound artifact digest differs;
- the intervention is undeclared, changes multiple unlisted components, or violates an invariant;
- any trace required by the question and claimed adapter capabilities does not reconcile;
- capital, costs, matching, horizon, or exogenous eligible universe changed without being the
  declared estimand; endogenous candidate and decision streams may change and must be regenerated;
- PIT or partition authorization fails;
- a required metric, condition cut, interaction, or uncertainty result is missing;
- the question digest was not bound before the target run or target-period outcome read;
- a largest-contributor or other familywise claim lacks its declared correction or independent
  confirmation;
- the workspace has unresolved or unreferenced artifacts/evidence, or deterministic reconstruction
  yields a different claim digest; or
- `READY` does not bind the canonical checksum ledger, the ledger does not cover the published file
  closure, or any consumed release/workspace byte differs from its checksum; or
- the explanation differs numerically or semantically from its claim artifact.

Valid negative and inconclusive results are published. Failure or low evidence is not converted to
zero contribution.

## 10. First delivery acceptance

`P3-ATTR-001` may resume only for a formal strategy with predeclared questions. Its first bounded
acceptance case must demonstrate without prescribing the strategy's internal topology:

1. one versioned adapter capability manifest, one versioned executable method registry, and
   canonical reproduction of one frozen native strategy baseline over a declared historical period;
2. one legal intervention for one component the adapter truthfully exposes, or the whole-strategy
   root when no finer native boundary exists;
3. a complete capital-constrained rerun with every applicable primary metric, while unavailable
   condition, decision, or opportunity capabilities remain explicit;
4. a compatible pairwise experiment only when the predeclared question requires one, and a
   condition cut only when the strategy has applicable native condition semantics;
5. deterministic verifier receipt, validity, and validation-flag assignment, including missing,
   unresolved, tampered, foreign-parent, foreign-producer, and unregistered-method failure fixtures;
6. a canonical workspace closure that resolves every claim to native evidence bytes, manifests,
   producer/verifier versions, parent identities, receipts, and semantic digests, then reproduces
   the same claim bytes; and
7. proof that changing the adapter alone does not alter native baseline strategy decisions.

The frozen P3 capability fixture may test plumbing but cannot satisfy this resume condition, be
tuned from the results, or become a formal strategy by implication.

`P3-DASH-001` remains separately blocked until recurring formal-strategy attribution evidence and a
reporting cadence exist. Its first acceptance adds equivalent facts across all three explanation
layers and a non-LLM rendering path; those presentation requirements do not reshape the strategy or
block the attribution runtime contract.

The bounded synthetic prototype described in section 2.4 is plumbing evidence only. Passing its
unit, artifact, documentation, or Web rendering tests does not satisfy any item in this section.

## Appendix A. Non-governing future estimator notes

This appendix preserves research directions requested during design. It is non-normative: these
methods are not stable kernel fields, current dependencies, backlog authority, or reasons for a
strategy to expose a particular topology. A later approved proposal may register one only after a
real question and adapter capability require it.

| Direction | Candidate methods | Activate when |
| --- | --- | --- |
| Portfolio accounting | Brinson-style allocation/selection, factor exposure, multi-period linking, transaction-cost analysis | The question is where observed return, exposure, or execution loss came from, not module causality |
| Risk decomposition | Euler/component risk, Expected Shortfall, conditional drawdown/CED | A valid risk model exists; Euler full allocation additionally requires a positive degree-one homogeneous and differentiable risk measure or an explicit subgradient convention |
| Model reliance | permutation reliance, SHAP, SAGE, feature masking and retraining comparisons | A trained model exists and local/model-level explanation is kept separate from portfolio contribution |
| Global sensitivity | Morris screening, Sobol indices | Inputs have meaningful perturbation distributions and simulator cost is controlled |
| Interaction allocation | Shapley, Owen values, Shapley-Taylor approximations | Stakeholders explicitly require a convention for shared credit and accept its coalition assumptions and compute cost |
| Causal heterogeneity | randomized shadow interventions, mediation analysis, causal forests | Treatment, outcome, confounder, and identification assumptions can be declared and audited |
| Sequential policy value | off-policy evaluation and delayed-reward credit assignment | Logged propensities, support/overlap, and sequential action semantics exist |
| Backtest robustness | block bootstrap, stronger multiple-testing controls, deflated Sharpe, probability of backtest overfitting | Repeated strategy search or promotion claims require correction for selection bias beyond the kernel's current family declaration rule |

No estimator may upgrade a claim merely because it is mathematically sophisticated. It must state
the exact estimand, assumptions, evidence type, validation flags, uncertainty, failure conditions,
and relationship to the full capital-constrained replay. Until a candidate has an approved
registration and implementation under a new method-registry version, its name is not an executable
method id and cannot authorize publication.
