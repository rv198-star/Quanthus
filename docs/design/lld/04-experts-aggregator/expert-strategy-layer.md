# Expert Strategy Layer and Evaluation

| Field | Value |
| --- | --- |
| Parent | `../04-experts-aggregator.md` |
| Design status | approved |
| Delivery gate | accepted; all seven `P3-EXPERT-000` children closed with immutable evidence |
| Milestone | Expert Strategy Layer MVP |
| Governing work | `P3-EXPERT-000` |
| Effective date | 2026-08-19 |
| Compatibility | Existing P3 expert/action protocol v1 and accepted Run digests remain frozen |
| Partition authority | Development only; arbitration and permanent-blind partitions remain unopened |

## 1. Decision

Quanthus uses trusted DWD/P2 evidence as the factual referee and treats a versioned, composable,
evolvable expert-decision topology as its primary strategy research and decision-expression unit.
Every expert version must have a quantitative, falsifiable evaluation contract. Depending on the
expert's semantics, evidence comes from future-target settlement, frozen-policy counterfactual
replay, forward Shadow observation, or an explicitly declared combination of those routes.

An expert's market assessment and the policy mapping from that assessment to a score, constraint,
veto, or abstention are independently versioned. A declared market assessment passes an assessment
quality gate. Every expert or policy mapping that can change an action also passes an economic-value
gate in the complete capital-constrained strategy replay. The common contract standardizes audit,
settlement, and composition; it does not make unlike targets numerically comparable and does not
create one universal accuracy score.

The expert topology is not the sole controller of profit. Candidate generation, expert assessments,
aggregation, position policy, exits, capital allocation, matching, and costs form the complete
strategy topology and remain the final unit of economic acceptance.

## 2. Scope and non-goals

This child owns:

- the separation between expert assessment and policy effect;
- the additive v2 evaluation artifacts and their compatibility with accepted v1 decisions;
- target-settlement, counterfactual, and forward-Shadow evidence routes;
- expert scorecard, sample-ledger, lifecycle, and failure semantics;
- the minimum Worker and immutable-artifact path for the first formal expert strategy;
- the bounded MVP sequence and its work-item acceptance boundaries.

This child does not:

- change veto, constraint-intersection, score, or action precedence in the accepted v1 aggregator;
- reinterpret existing `signal` or `confidence` values as a forecast or probability;
- make the original nine candidate experts a delivery checklist;
- authorize tuning, arbitration/blind access, promotion, notification, or capital deployment;
- create a generic expert DSL, plugin market, model registry, feature platform, or expert service;
- authorize exhaustive combinations, automatic weights, automatic tuning, automatic retirement, or
  AI-authored promotion;
- create per-expert ADS datasets, a second pricing engine, Redis, Celery, RabbitMQ, or another
  approval state machine.

### 2.1 Implementation designs

`04-experts-aggregator/expert-strategy-layer/README.md` routes the seven blocking work items to
their independently owned implementation designs. Those children define package paths, exact
contracts, failure behavior, verification, human decisions, and rollback. The readiness review in
that directory narrows the first formal topology to exactly two active strategy experts so every
action-changing expert is covered by the bounded attribution budget.

After the original MVP closed, `P3-EXPERT-006` was separately owner-authorized as a bounded
research-only consumer increment. It does not reopen the seven-child MVP epic or change the formal
two-expert topology. It only tests whether the already accepted Stage10/11 development semantic
surface can be consumed as optional advisory context over authoritative Continuous Facts and whether
that consumption exposes a concrete downstream semantic gap. Governing child:
`expert-strategy-layer/stage-semantic-consumption.md`.

## 3. Authority and expert classes

### 3.1 Strategy experts

A strategy expert may produce one or both of these independently versioned results:

1. **Assessment**: a probability, continuous forecast, ordinal assessment, or explicit abstention
   about a declared subject, population, target, and horizon.
2. **Policy effect**: a versioned mapping to an accepted scoring, constraint, veto, or abstention
   output consumed by the existing aggregator.

One expert may be hybrid, but the two results never share identity implicitly. Changing a target,
population, horizon, abstention rule, raw assessment, or assessment algorithm creates a new
assessment version. Changing only the action threshold, weight, veto rule, bound, or fallback creates
a new policy-mapping version and leaves the assessment record intact.

### 3.2 System guardians

Point-in-time visibility, tradability, data quality, accounting, and funding invariants are system
guardians. They may use the existing system-veto or constraint contracts, but they are not alpha
experts and are never optimized away because a return comparison is unfavorable. Their acceptance
criterion is invariant correctness and fail-closed coverage.

### 3.3 First-delivery subject boundary

The MVP supports security/opportunity assessments made after the T close for the accepted
`close-to-next-open-v1` profile. Portfolio-, funding-, and allocation-level general subject models
remain out of scope. News and company-quality experts remain blocked until their exact historical
publication and correction semantics pass PIT admission.

## 4. Evaluation artifacts

The v2 protocol is an additive artifact family. It does not add new meanings to the accepted v1
dataclasses or rewrite an accepted `DecisionEvent`.

| Artifact | Required responsibility |
| --- | --- |
| `ExpertEvaluationSpec` | Bind one expert assessment version, its ordered policy-mapping set, evaluation route, population, partition, primary metric, baselines, slices, and stop conditions |
| `ForecastSpec` | When a future target is claimed, bind target/version/digest, subject, eligibility population, observation anchor, trading-session horizon, outcome type, resolver/scorer versions, exclusions, missing rules, and censoring rules |
| `ExpertObservation` | Bind deterministic prediction id, expert/target versions, PIT context and evidence digests, applicability or abstention, typed raw value, and evidence quality; it contains no policy mapping |
| `PolicyEffect` | Reference one observation and carry the accepted score, constraint, veto, or abstention semantics plus its own mapping identity; a policy-only observation is not a forecast |
| `OutcomeSettlement` | Record `pending`, `settled`, `censored`, or `unavailable`, the realized result, observation window, future DWD identity, resolver version, and target-specific score |
| `ExpertScorecard` | Aggregate one exact expert/target/population/partition binding, sample ledger, assessment metrics, uncertainty, stability, and decision-change counts; economic effects remain separate attribution artifacts |
| `ExpertLifecycleDecision` | Bind one human decision, compatible enabled/disabled Shadow disposition, rationale, limitations, actor/time, and the complete accepted evidence chain without mutating any parent artifact |

Every artifact has a schema version, deterministic content identity, producer version, parent
digests, and canonical serialization. Transport timestamps, Job ids, claim generations, Worker ids,
and filesystem paths live outside canonical content. Reproducibility means byte-identical semantic
artifacts; operational envelopes may differ between attempts.

### 4.1 Typed values

An observation declares one raw value type:

- `probability`: a probability for one frozen event and eligible population;
- `continuous`: a numeric target estimate with unit and scoring rule;
- `ordinal`: an ordered state whose bins and monotonic expectation are frozen;
- `policy_only`: no independently observable forecast is claimed.

Only `probability` may use probability language or calibration metrics. Existing v1 `confidence`
remains an aggregation weight/evidence expression under its frozen policy and is not silently
upgraded to probability.

## 5. Evidence routes

| Route | Appropriate use | What it can establish | What it cannot establish |
| --- | --- | --- | --- |
| Future-target settlement | Forecast, probability, continuous, or ordinal assessment with an independently resolvable outcome | Discrimination, calibration or target loss, coverage, and stability | Portfolio value or causal profit ownership |
| Frozen-policy counterfactual replay | A score mapping, veto, constraint, allocation, hold, or exit policy that changes the capital path | Metric changes under one legal intervention in the frozen simulator | Simultaneously observed real-world truth, additive ownership, or future causality |
| Forward Shadow | Any assessment or policy effect recorded without execution authority | Prospective coverage, stability, disagreement, and eventual target outcomes | Live-fill claims or automatic promotion |

"Independent settlement" means the resolver does not call the evaluated expert or policy. It does
not mean that overlapping horizons, securities, dates, or market regimes are statistically
independent.

## 6. Decision semantics and normalization

The accepted action order remains:

1. evaluate system and strategy vetoes;
2. select the strictest veto action;
3. intersect applicable constraints;
4. aggregate policy-mapped scoring outputs;
5. map the aggregate to a position action;
6. enforce final position, concentration, accounting, and funding invariants.

This is not expert voting. Raw assessments for different target cards are never averaged merely
because they share a numeric range. Cross-expert composition occurs only through a versioned
`PolicyEffect` mapping. A normalized policy influence is an action input, not a claim that the
underlying assessments have a common empirical unit.

## 7. Scorecards and gates

### 7.1 Sample ledger

Every eligible observation enters the sample ledger. It separately counts eligible, applicable,
abstained, settled, pending, unavailable, not-executable, and censored observations. Coverage and
abstention are always shown beside quality metrics; silently deleting difficult or untradeable
cases fails publication.

### 7.2 Assessment-quality gate

The scorecard records the base rate or naive baseline, target-appropriate loss or discrimination,
coverage and selective-abstention behavior, temporal/regime stability, and uncertainty based on
event, time-block, or clustered resampling. Probability forecasts use Brier or log loss and
calibration; rare risks emphasize precision, recall, PR behavior, false-veto cost, and minimum event
count. Raw accuracy is supplementary and never the sole gate.

Predeclared primary target, horizon, metric, hypothesis family, market slices, and stop conditions
are frozen before result access. Overlapping horizons and common market shocks are not counted as
independent rows. Exploration cannot be relabeled as confirmation.

### 7.3 Economic-value gate

Every action-changing expert or mapping records how often and how it changed a decision. A bounded,
complete P2 replay compares the frozen baseline with one legal intervention and reports return,
drawdown, tail loss, exposure/capital use, turnover, costs, missed opportunity, and relevant risk
metrics. The two-expert MVP requires both single-expert interventions and their one predeclared
joint intervention. A later proposal may still request at most two singles and one necessary pair
until a separate resource and multiple-comparison budget is approved. Single-intervention effects
are not additive; unchecked interactions remain visible.

### 7.4 Product-value gate

The MVP must produce at least one evidence-backed human decision to keep, narrow, remap, create a
new version, merge, or retire an expert. It also reports Worker time, artifact storage, and human
review cost. A technically complete scorecard that cannot support a lifecycle decision is not an
accepted product loop.

There is no cross-target expert leaderboard or universal composite score. Assessment quality cannot
hide negative portfolio value, and favorable portfolio results cannot validate a false or
unsettleable assessment claim.

## 8. Version and lifecycle governance

Evidence state and lifecycle decision are separate:

- evidence state: `development`, `replication`, `independent`, or `forward_shadow`;
- lifecycle decision: `keep`, `narrow_with_new_version`, `remap_policy`, `merge`, `retire`, or
  `inconclusive`.

No schema, scorecard, or agent automatically changes lifecycle state. A human-approved append-only
decision references the exact assessment, policy, target, resolver, DWD, split, replay, and
scorecard digests.

Development evidence may create a new version. A revealed arbitration or permanent-blind failure
permits rollback or retirement but never retuning against that period. Narrowing applicability,
changing abstention, changing a target, or merging experts is a new hypothesis and version, not an
in-place repair. Insufficient rare-event evidence remains `inconclusive` and may continue in
bounded Shadow only when the lifecycle decision records an observation budget and no stop condition
has fired.

## 9. Worker and artifact boundary

Web/API are the control plane. Historical observation, target settlement, scorecard construction,
counterfactual replay, and forward settlement run in an independently deployed Worker through the
existing PostgreSQL queue, lease, generation-fencing, cancellation, progress, and terminal-state
contracts.

The MVP adds one admitted `EXPERT_EVALUATION_JOB` handler with explicit stages: bind inputs, build
or reuse the DWD/feature projection, produce observations, settle authorized mature outcomes,
construct the assessment scorecard, run only predeclared optional interventions, and publish. A
stage may be skipped only with a typed reason. Forward settlement reuses the same contract with a
different operation and never mutates a prior observation.

One baseline run records all active and Shadow observations from one shared provider session and
feature panel. Future-target settlement is a result join and does not rerun the strategy. Full
capital-constrained replay runs only for predeclared interventions after sample and decision-change
conditions pass.

The Job input carries a canonical non-empty evaluation-spec set. The technical proof binds one
spec; the formal topology binds its complete two-expert set and rejects missing, extra, or duplicate
members before opening DWD.

Immutable compact columnar artifacts own observation and settlement rows. PostgreSQL owns Job/Run
state, versions, summaries, and artifact references; it does not store one row per daily expert
opinion. Inputs, feature projections, and baseline replay artifacts are content-addressed and shared
where their manifests are identical.

## 10. PIT, partition, and failure behavior

- Prediction inputs stop at the declared observation cutoff. Future settlement bytes cannot enter
  the observation digest, feature lineage, policy effect, or action.
- The evaluation manifest names exact DWD Base/correction/increment identities and declares whether
  per-decision publication semantics are proven for every consumed domain.
- A domain without sufficient PIT publication evidence is `unavailable` and cannot be silently
  evaluated through the retrospective current-record view.
- Development is the only historical partition admitted by the MVP. Arbitration and permanent
  blind remain physically and procedurally unopened.
- A target that extends beyond the authorized partition is censored or excluded by its frozen rule;
  it never reads across a sealed boundary.
- Missing resolver data, delisting, suspension, unexecutable action, or tail truncation remains a
  typed sample-ledger state. It is not silently treated as a correct abstention or neutral result.
- Reruns with the same bindings must reproduce canonical bytes. Identity drift, mixed versions,
  partial artifacts, or an unregistered metric fails publication.

## 11. Compatibility and migration

Accepted v1 expert outputs, aggregator behavior, capability-fixture strategy, DecisionEvents, and
Run digests are immutable. The v2 evaluation family is published as a sidecar bound to a new expert
and strategy version. Existing opaque expert-output mappings may carry references for new Runs, but
old records are never backfilled or resealed as v2 evidence.

The first implementation must prove:

- accepted v1 golden and replay bytes remain unchanged;
- future outcome material is absent from observation identity and prediction-time evidence;
- input reordering does not change v2 identities;
- horizon edges, suspension, delisting, unavailable data, and partition-tail censoring fail as
  designed;
- Worker retry, lease loss, cancellation, and generation fencing cannot duplicate publication;
- two identical runs produce byte-identical canonical artifacts.

## 12. MVP boundary

### 12.1 Technical proof

The first proof freezes one daily market-data scoring expert, one target, one trading-session
horizon, and one resolver. `trend stability` is the default expert candidate because its required
market observations are already inside the admitted DWD/P2 path, but the exact target and horizon
are a human decision in `P3-EXPERT-003` before implementation. The proof uses development data only
and produces an assessment scorecard. It establishes settleability, not portfolio value; the first
baseline-versus-abstain replay waits for the formal topology and `P3-ATTR-001`.

### 12.2 Product proof

After the technical proof, one formal strategy card binds exactly two active complementary strategy
experts plus candidate, aggregation, position, exit, funding, and execution versions. The first is
the market assessment expert from `P3-EXPERT-003`; `P3-STRAT-002` implements the second,
policy-only position-risk expert. Together they cover entry assessment, strategy risk boundary, and
hold/exit review. System guardians remain outside the count. The original nine experts are a
candidate catalogue rather than a mandatory set.

Both action-changing expert mappings receive a mandatory single-expert fallback replay, and their
one joint fallback is predeclared to expose interaction. This exhausts the MVP intervention budget
without leaving an active expert economically unevaluated. One baseline Run records all active and
Shadow opinions; expanding beyond two active experts requires a later proposal and budget.

The existing candidate detail page gains an unframed, read-only expert evidence section showing the
bound topology, assessment scorecards, decision-change counts, economic effects, disagreement
cases, limitations, and append-only research decision. It does not calculate results, create a new
approval state, or authorize execution.

MVP closure requires a development-only lifecycle decision and an explicit forward-Shadow
disposition for the frozen version. `keep` enters daily forward Shadow; `inconclusive` may enter it
only under a bounded observation budget; `narrow_with_new_version`, `remap_policy`, `merge`, and
`retire` keep the current version unscheduled. An enabled handoff must pass one operational drill;
a disabled disposition must prove that no Job can be submitted. Forward evidence accrues after MVP
closure and remains a P6 input rather than retroactive proof of the development result.

## 13. Falsification and stop conditions

The project must shrink or reject the claim that multi-expert topology is a core advantage when one
or more of these conditions persist across untouched or forward evidence:

- most assessment experts cannot outperform a frozen naive or simple-rule baseline;
- apparent quality collapses after coverage, abstention, dependence, or regime adjustment;
- action-changing experts add no cost-adjusted economic value or materially worsen risk;
- outputs and errors are redundant, removal changes few decisions, and a simpler topology is
  non-inferior;
- effects repeatedly reverse across time or regime and survive only through repeated scope
  narrowing;
- rare-event experts cannot collect enough independent evidence within an approved observation
  budget;
- one complete cycle cannot support any expert lifecycle decision;
- Worker, storage, and review cost exceed the decision value available to a personal research
  platform.

When a stop condition fires, do not add experts, search weights, or automate tuning to rescue the
result. Return to the formal strategy question, simplify the topology, or pause platformization.

## 14. Work-item sequence

```text
P3-EXPERT-001 --> P3-EXPERT-002 --> P3-EXPERT-003 --> P3-STRAT-002 --> P3-ATTR-001
                                      ^                              |
                                      |                              v
                                P4-LABEL-001                 P3-EXPERT-004
                                                                      |
                                                                      v
                                                              P3-EXPERT-005
```

`P3-EXPERT-000` tracked every node above and is accepted after all seven children closed with
linked evidence. The terminal lifecycle decision is `inconclusive` with forward Shadow disabled;
creating or closing an Issue alone never satisfied a delivery gate.

### `P3-EXPERT-000` - Expert Strategy Layer MVP epic

- Objective: deliver one formal, evidence-backed expert lifecycle loop without opening later data
  or capital gates.
- Acceptance: every blocking child closes; one development lifecycle decision and its
  forward-Shadow disposition are recorded; an enabled or disabled disposition is operationally
  proved; v1 evidence remains reproducible; arbitration/blind access remains zero.
- Non-goals: all nine experts, production promotion, live notification, or automated evolution.

### `P3-EXPERT-001` - evaluation protocol v2

- Objective: implement the additive evaluation artifacts, canonical identities, validation, and
  v1 compatibility boundary from Sections 4 and 11.
- Acceptance: schema/unit/contract tests cover every artifact and typed failure; accepted v1 golden
  bytes remain unchanged; future settlement cannot affect observation identity.
- Delivery: accepted by
  `docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md`.
- Non-goals: concrete target, expert, Worker handler, score calculation, or UI.

### `P3-EXPERT-002` - Worker evaluation pipeline

- Objective: admit `EXPERT_EVALUATION_JOB`, share DWD/feature inputs, publish immutable artifacts,
  and persist only operational state, summaries, and references in PostgreSQL.
- Acceptance: lease loss, retry, cancellation, generation fencing, partial publication, input
  reordering, and identical-run tests pass; no Redis, new service, or opinion-row table appears.
- Non-goals: a formal strategy conclusion or access to sealed partitions.

### `P3-EXPERT-003` - first target, resolver, and technical expert

- Objective: reference an accepted `P4-LABEL-001` target card when semantics match, or record a
  human-approved non-reuse decision, then implement the first assessment expert, resolver, sample
  ledger, and development scorecard.
- Acceptance: eligibility, horizon, outcome, exclusions, censoring, primary metric, naive baseline,
  and stop conditions are frozen before results; the accepted Worker is used for every DWD read;
  PIT leakage and boundary tests pass; coverage, abstention, base rate, quality, stability, and
  uncertainty are reported.
- Non-goals: treating the result as a formal strategy, universal ranking, or target optimization on
  arbitration/blind data.

### `P3-STRAT-002` - first formal two-expert topology

- Objective: approve and implement one formal strategy card with the assessment expert plus one
  position-risk expert, and bind the exact product candidate, candidate generator, aggregation,
  position, exit, funding, execution, assessment, target, and policy-mapping versions.
- Acceptance: the second expert, active/Shadow roles, expected decision changes, controls, two
  single fallbacks, one joint fallback, resource budget, and rollback criteria are versioned; only
  development data is authorized.
- Non-goals: copying the frozen capability fixture into a promotable lineage or implementing all
  original expert candidates.

### `P3-ATTR-001` - bounded formal-strategy attribution

- Objective: resume the existing deferred attribution work only after `P3-STRAT-002` supplies a
  formal adapter and predeclared questions.
- Acceptance: baseline reproduction, both predeclared single-expert fallbacks, and their required
  joint fallback use the complete P2 capital path; effects retain interaction and frozen-simulator
  limitations.
- Non-goals: exhaustive combination search, Shapley allocation, additive profit ownership, or
  real-world causal claims.

### `P3-EXPERT-004` - candidate-detail evidence and research decision

- Objective: project published expert topology, scorecards, bounded effects, disagreement cases,
  limitations, and append-only research decisions into the existing candidate detail workflow.
- Acceptance: browser/API contract tests prove the view cannot compute or upgrade evidence, mutate
  an observation, create an approval state, or authorize execution; every displayed value resolves
  to a trusted artifact digest.
- Delivery: accepted by
  `docs/acceptance/p3-expert-candidate-detail-evidence-20260823.md`.
- Non-goals: a new dashboard product, role system, expert leaderboard, or second approval flow.

### `P3-EXPERT-005` - end-to-end MVP acceptance and Shadow disposition

- Objective: run the complete development loop, record a human lifecycle decision, and either hand
  the frozen version to bounded daily forward Shadow or prove it remains disabled, without granting
  promotion or notification authority.
- Acceptance: deterministic rerun, scorecard, bounded attribution, workbench trace, lifecycle
  decision, Shadow disposition proof, resource-cost report, rollback package, and zero
  sealed-partition access are preserved in one acceptance record.
- Delivery: accepted by `docs/acceptance/p3-expert-mvp-shadow-handoff-20260823.md`; the decision is
  `inconclusive` and forward Shadow is disabled.
- Non-goals: P6 quarterly acceptance, blind reveal, execution authorization, or live capital.

## 15. Follow-on boundary

The MVP deliberately does not close `P5-TARGET-001`, `P6-SCORE-001`, `P6-GOV-001`, or
`P6-GOV-002`. Later work may generalize the accepted artifact family, but P5 model research still
depends on its phase gate and P6 promotion still requires its own scorecard, partition, Shadow, and
review evidence. Any later enabled forward Shadow produces input evidence only; the accepted MVP
did not enable it.

### Fixed-snapshot exploratory comparison preparation (2026-10-23)

The Owner separately approved preparing the retrospective question in
`config/research/p3-strat-003/retrospective-comparison.v1.json`. It preserves the strict
`P3-STRAT-003` request and its unaccepted B/NOT_RUN D. The new purpose uses the declared
information snapshot to inspect the original frozen business dates; it cannot establish that
the inputs were historically available or support a production/P6 claim.

The result-blind `p3_retrospective_preparation` consumer owns one immutable job-local typed port
journal and one prefix-safe feature materialization. Candidate rules, the common complete-case
mask and all four variant definitions reuse their existing numeric rules. The journal preserves
Decimal scale and real source/business clocks; each variant binds the same full input, support,
static-industry, ordinary-code/config and runtime identities. Neither a schema/digest nor a
native executable flag qualifies missing listing, normalized-action or execution support.

`PREPARATION_COMPLETE.json` attests only this engineering materialization, and is deliberately
distinct from a data READY or research admission. Cold checking takes an externally bound
manifest digest and reads only the package and ordinary code/config. The strict EventRecord,
StrategyReplayFrame, BacktestSignal and legacy P2 entry points keep their original clock gates;
the preparation cannot relabel its late-clock records as those types. An explicit two-clock
simulation boundary and purpose-specific source/support admission remain prerequisites for a
future result run. No result execution is authorized by this preparation approval. Shared
calendar defects go to P10 #555; historical-PIT evidence remains with P10 #474. Only the affected
step waits; independent consumer and synthetic engineering may continue.
