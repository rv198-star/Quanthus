# LLD 03: Label and Feature System

| Field | Value |
| --- | --- |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | `P3-FEAT-001` accepted; `P3-FEAT-002` accepted; `P3-LABEL-001` accepted; `P3-LABEL-003` accepted; frozen fixture vocabulary accepted; `P3-LABEL-004` deferred pending a formal label proposal; `P3-STRUCT-001/002/003` accepted within their bounded scopes; `P3-STRUCT-004` bounded B1-B5 retrospective vertical accepted; `P3-STRUCT-005` Route-A **Support Evolution Fact Family / 演化支撑事实族** bounded retrospective extension accepted on two cold real-DWD Jobs (`F9` retained as historical technical code); broader v2 capabilities separately gated; `P3-DICT-001/002/003` accepted in production; Lineage V2 checkout/Web `c25638a`, API `3aa4837` |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-RULE-001, FR-EVENT-001, NFR-003/004 |

## 1. Goal

Create one versioned vocabulary for market state. A label is a deterministic function over a
declared point-in-time snapshot, not an analyst note.

## 2. Label dimensions

The registry supports primary stage, trend energy, price/volume acceptance, key-level
relationship, position/risk modifiers, market/sector context, and tradability. Primary stage
is single-valued; modifier dimensions may be multi-valued when their card permits it.

The draft `docs/design/stock-stage-state-machine.md` records a candidate primary-stage vocabulary:
twelve directional lifecycle states (`1`-`12`), neutral `0`, and exception sentinel `X`. It is
documentation-only until exact label cards, transition tests, PIT rules, and validation evidence
are approved under `P3-LABEL-002`. It must not be confused with the position state machine.
The non-governing [stage qualification proposal](../stock-stage-qualification.md) and its
[field-level dependency matrix](../stock-stage-data-dependencies.md) now describe the missing
identifiability, ambiguity and independent-validation design. They confer no implementation,
LabelCard, data-read, daily-schedule or strategy-Shadow authority; the existing fixture stays closed.

Historical sector-dependent labels remain disabled in official results while historical
membership is unavailable. A missing context produces an explicit unknown/context-missing
value rather than a current-membership approximation.

## 3. Label card contract

Each card contains:

- stable id, display name, dimension, and semantic version;
- lifecycle state: draft, shadow, active, deprecated;
- exact source fields and feature versions;
- mathematical rule and versioned parameters;
- lookback and minimum-history requirements;
- mutual-exclusion and coexistence rules;
- output evidence and reason codes;
- at least two known counterexamples;
- expected frequency and validation sample references.

Any threshold change creates a new version. Existing runs retain the old card.

`src/quanthus/label_registry.py` now owns the strict LabelCard registry and evaluator boundary.
Cards declare exact feature/version dependencies and their union of source fields, parameters,
allowed outputs, evidence fields, reason codes, conflict/coexistence relationships, at least two
counterexamples, expected frequency, and validation references. The controlled evaluator wrapper
rejects hidden feature inputs, mixed snapshots, feature-definition digest drift, inactive
dependencies, type mismatches, undeclared results, and an applied result based on an unavailable
feature. Every accepted result binds the card digest, evaluator version, snapshot, semantic
evidence, and exact feature-lineage digests. Evidence:
`docs/acceptance/p3-label-card-contract-slice-20260728.md`.

No production LabelCard or market-state evaluator is registered by this slice. In particular, the
draft twelve-state vocabulary remains non-governing until `P3-LABEL-002` supplies exact cards,
features, golden cases, and validation evidence.

### Frequency and conflict audit

`src/quanthus/label_audit.py` now audits already-produced `LabelResult` values against their exact
registered cards. It reconciles applied, unknown, and not-applicable counts; reports applied-value
distributions; checks each card's declared sample scope and expected applied-rate range; and detects
card-level mutual exclusion plus multiple applied primary-stage cards in one PIT context. Active
frequency failures and active-active conflicts block publication. A failure involving a shadow
card remains visible as a warning and does not block the active vertical slice. Evidence:
`docs/acceptance/p3-label-frequency-conflict-audit-slice-20260728.md`.

The audit contains no market threshold or concrete label definition. Expected ranges and
relationships come only from the versioned LabelCard, so this infrastructure does not promote the
draft twelve-state vocabulary or create a `P3-LABEL-002` result by implication.

## 4. Feature registry

Feature definitions record id, formula, input datasets/columns, units, null policy, lookback,
knowledge-cutoff rule, output type, version, owner, and lifecycle. Cross-sectional percentiles
must use only the complete T-close market snapshot. Insufficient history remains explicit and
is never imputed from future rows.

`src/quanthus/feature_registry.py` now owns the immutable registry and snapshot-lineage contract.
The strict loader supplies no defaults and rejects unknown fields, duplicate feature/version keys,
and more than one active version of a feature. A lineage record binds the definition digest and
full snapshot identity to each exact dataset version, manifest id, source column, and source
knowledge cutoff. Missing source datasets, insufficient history, incomplete T-close cross
sections, and future source evidence fail with stable reason codes. Evidence:
`docs/acceptance/p3-feature-registry-slice-20260728.md`.

The original registry slice publishes no production feature definitions and evaluates no formula.
`P3-FEAT-002` now adds a separate research-only baseline registry and evaluator. Active lifecycle
inside that registry means selected for the named research suite, not promoted for paper or live
decisions. The draft twelve-state vocabulary receives no authority from either slice.

### Baseline daily evaluator

`config/p3-baseline-features.research.v1.json` defines eight small, interpretable daily features:
a total-return continuity index, 20/60-session moving averages, 20-session momentum and prior high,
14-session RSI, prior-20-session volume ratio, and 20-session realized volatility. The evaluator
in `src/quanthus/backtest/baseline_research.py` scans one immutable historical bundle once with
DuckDB and writes a date-sorted feature panel. Strategy callbacks query that panel by exact
T-close date and economic `security_id`; they do not issue one history query per security.

`config/p3-baseline-strategies.research.v2.json` is the current moderated consumer of this panel.
It leaves feature definitions and the historical panel unchanged while bounding entry regions,
filtering volatile/extreme candidates, and sizing entries below the full fixed unit. The v1
catalogue remains loadable only to reproduce the rejected extreme-control run. Evidence:
`docs/acceptance/p3-baseline-strategy-suite-v2-20260729.md`.

The continuity index starts from unadjusted closes. On an effective normalized action date its
gross return is:

```text
(close_t * (1 + bonus_share_ratio_t) + cash_dividend_per_share_t) / close_t_minus_1
```

Only an action declared known before its effective date may enter the calculation. Qfq vendor bars
are not evaluator inputs. They may be used after the fact as snapshot-bound corroboration only.
An invalid/missing bar transition, an unexplained close outside the resolved daily price-limit
interval, or an active corporate-action exception is null; rolling features remain unavailable
until their complete observation window is valid again. A normalized action explicitly explains
its effective-date raw-price discontinuity. Current T execution evidence and
`entry_policy_allows` are separate entry filters rather than feature values.

The evaluator binds its version, registry digest, immutable bundle id/digest, date scope, and row
counts in `BaselineFeaturePanelReport`. Ambiguous identities, unmapped actions/exceptions, future-
known actions, invalid exception intervals, and duplicate universe/bar/execution keys fail before
publication. The report separately counts unexplained price transitions, so future corpus repair
can reduce this conservative exclusion without changing strategy code. Evidence:
`docs/acceptance/p3-baseline-strategy-suite-20260729.md`.

The research suite applies one explicit full-scope data-quality filter: if an economic
identity has any versioned corporate-action exception or unexplained transition in the requested
development decision interval or its final T+1 execution session, it is ineligible for entry
throughout that run. This prevents a later unknown quantity change from invalidating a position
opened earlier, but it is not PIT market evidence and creates sample-selection bias. The panel
reports the excluded identity count and `data_quality_scope_selection_bias=true`. Results under
this filter are conditional infrastructure baselines, never official all-universe return claims.
Repairing the action corpus is the path to removing the filter.

### Governed candidate extension

`config/p3-governed-feature-extension.research.v1.json` checksum-binds the accepted baseline
registry and adds exactly one complete-T-close cross-sectional feature,
`market.breadth_above_sma60`. `config/p3-governed-labels.research.v1.json` defines four active,
candidate-specific cards for breadth regime, moderate uptrend, price-volume breakout confirmation,
and extension risk. `src/quanthus/governed_strategy.py` binds their facts to the immutable bundle
manifests and the exact T-close snapshot before running the strict LabelCard evaluator.

The extension label uses realized volatility and RSI. Absolute entry-day return is a separate
entry-evidence veto over the same T-close panel, so the two controls remain independently visible
instead of being collapsed into one label. These cards implement only the frozen P3
capability-fixture vocabulary `candidate.market-breadth-trend-breakout@1.0.0`; they do not activate the optional
twelve-state stock-stage model or create a generic label-formula language. The first 2012-2021
run produced 48,591 complete candidate-to-score observations without unknown or unreconciled
funnel inputs. Evidence: `docs/acceptance/p3-governed-strategy-development-20260729.md`.

The fixture is closed to tuning and promotion. `P3-LABEL-004` human review is deferred until a
formal label family exists; manufacturing a review corpus for this fixture would not improve the
accepted platform contract or establish strategy semantics.

## 5. Daily-bar anatomy

The first release may derive wick/body ratios, close location, volume ratio, turnover
percentile, volume-direction interaction, gap frequency, multi-day open/close relationships,
and range contraction/expansion. Minute sequences are not direct model inputs; later minute
features must be predeclared summaries.

### Structure Intelligence MVP boundary

`P3-STRUCT-001` is an additive post-P3 research proof governed by
`../structure-intelligence-mvp.md`. It does not activate the draft twelve-state vocabulary. The
MVP consumes the accepted replay/DWD decision view into one thin, purpose-specific Structure
Research ADM, derives only coarse causal structure facts, reuses the accepted trend-stability rule
semantics through a new security-day research adapter identity, and compares baseline versus
structure-assisted decisions through the existing aggregator and P2 execution contracts. The ADM
is not DWD, generic DWS, or operational ADS. Real-DWD A/B qualification and the bounded outcome-
blind review are accepted in
`../../acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md`. The accepted MVP
remains development-only and gains no economic-value, Shadow, paper, notification, or execution
authority.

`P3-STRUCT-002` is the separately gated economic-value step governed by
`../structure-intelligence-economic-value.md`. It reuses one shared Structure ADM trajectory and
holds the accepted formal strategy's candidate population, capital, position-risk path, exits,
costs, and P2 matcher fixed. C1 expresses that trajectory as a continuous entry score; C2 compresses
the same facts into coarse states. C2-C1 is the primary comparison, so a positive result cannot be
created merely by giving the state variant additional history or episode memory. The exact mapping
and no-tuning rule are frozen before results.

`P3-STRUCT-003` is the accepted fact-product successor governed by
`../continuous-structure-facts-v1.md` and closed by
`../../acceptance/continuous-structure-facts-v1-real-dwd-acceptance-20260906.md`. It publishes
prior-20 rolling support/resistance anchors, signed margins, independent volume ratio, and row-local
frozen breakout episode identity/progress. Episode creation is price-only; volume is never hidden
inside episode existence. Experts consume continuous facts directly through ordinary
`FeatureFact`/expert ports. The coarse state vocabulary is a replaceable explanation renderer and
is forbidden from the fact-row identity. Missing data resets causal continuity without implying
bearish structure or exit authority.

### Time-first Structure Facts v2

`P3-STRUCT-004` implements the reviewed modeling boundaries under
`../structure-facts-v2.md` and ADR 0014. The additive naming catalog classifies complete
`domain.family.concept` identifiers and distinguishes reserved concepts from executable specs.
Anchor/geometry/activity/interaction/episode are Structure-owned; indicators and other evidence
domains are parallel, with required DWD reference dependencies. B1-R separates Fact business time,
source/generation/publication metadata, and operation/decision use logs. Facts/indicators carry no
consumer, consumed-at or decision-cutoff usage attribute; logs reference exact revisions and receipts.
`temporal_contracts.py` supplies business/source-clock contracts; `evidence_consumption.py` supplies
append-only audit-event/receipt shapes and local binding checks. Neither rewrites v1 formulas,
FeatureFact IDs, source-view limits or accepted artifacts.

B1 and B1-R are local contract work only. The bounded B2-B5 retrospective adapter, qualified
references/interactions/episodes, separate consumption audit and fixed Worker entry are now
accepted under `../structure-facts-v2-delivery.md` and
`../../acceptance/structure-facts-v2-b1-b5-final-acceptance-20260907.md`: exact A/B reproduction,
168 audited consumer invocations, five P2 fills per Run and 12/12 outcome-blind review. Stronger
source availability/as-of capabilities remain unsupported by this retained data view. The machine-readable ledger records this bounded runtime acceptance while retaining B6-B11 as
separate unimplemented or unauthorized capability. `P3-STRUCT-005` is the Route-A **演化支撑事实族 / Support Evolution Fact Family** milestone (`F9` is the historical planning code) outside the accepted B1-B5 slice. It extends B3/B4/B5 with four coordinated dimensions: support object/binding, same-support break/reclaim events, unreclaimed process state, and continuous support-distance/post-break-rebound measurements. These are members of one fact family, not peer Stage labels. It does not open B6/B7 and it keeps Stage codes as research-only projections over continuous Facts.

### Data & Indicator Catalog

`P3-DICT-001` is now an active read-only Catalog delivery governed by
`../data-indicator-catalog.md` and `../../work-items/data-indicator-dictionary.md`. It projects
existing ODS/DWD machine contracts, Feature/Label registries and semantic catalogs into one
versioned query surface; it does not become a parallel formula or warehouse authority. A small
snapshot manifest binds the deterministic full projection digest and counts. The Platform exposes
GET-only summary/search/detail/relation routes under `catalog:read`, and `/catalog` provides the
human query surface. Missing metadata remains an internal maintenance diagnostic; the business
summary and homepage do not expose a developer gap dashboard. `P3-DICT-002` adds Chinese reading
metadata and declared processing lineage, governed by `../data-indicator-catalog-productization.md`
and `../../work-items/catalog-productization.md`. DWD field focus distinguishes exact field proof
from separately labeled parent-table context; table membership cannot fabricate a field edge.
Every visible node exposes a business label plus exact technical identity, and dataset-level context
connects sources directly to target tables instead of adding redundant generic processing boxes.
The Web renders these graphs inside fixed-height infinite viewports with an independent world transform,
true centered fit, unclamped drag pan, pointer-anchored wheel/pinch zoom, floating controls and MiniMap.
For `table_context_only`, the parent-table context is rendered in that same single canvas with a compact
non-field-proof notice instead of a second large context section; all such controls are presentation-only
and cannot alter the graph contract. `P3-DICT-003` then supersedes the mixed processing graph as the
main Data Lineage model: only governed ODS/DWD/Research ADM assets are nodes, fields stay nested in
those assets, transformations become edge metadata, and Definition/Usage dependencies are separate
views. A-D and the parent are accepted; the latest Web navigation refinement runs on checkout/Web `c25638a` while API remains `3aa4837`. Membership and
conceptual relationships remain separate from processing edges, and static lineage is not actual
consumption evidence. The Structure
blueprint may explicitly bind registered concepts to accepted B3-B5 runtime capabilities; only
those bindings may project `implemented`/`accepted` status into the Catalog. A discovery-only
`related_not_equivalent` legacy link never satisfies an executable-spec requirement. This Catalog
still has no dependency edge back into P3-STRUCT-004 B1-B5 and grants no strategy or execution
authority.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-FEAT-001 | Implement feature registry and lineage model | P2 snapshots | Version and cutoff tests |
| P3-FEAT-002 | Implement research baseline daily evaluator | P3-FEAT-001, P3 replay bundle | Split-continuity, cutoff, and real-panel audit |
| P3-LABEL-001 | Implement label-card schema and evaluator protocol | Feature registry | Deterministic fixture |
| P3-LABEL-002 | Implement vertical-slice tradability/stage/risk labels | P3-LABEL-001 | Golden label cases |
| P3-LABEL-003 | Build label frequency and conflict audit | Registry | No invalid coexistence |
| P3-LABEL-004 | Build human review corpus and adjudication form | Evaluators | 100 reviewed cases |
| P3-STRUCT-001 | Prove the bounded DWD -> Structure ADM -> trend-semantics adapter -> aggregator -> P2 path | P3-FEAT-002, P3 expert/aggregator contracts, P4 Event Worker | Real-DWD immutable artifact, independent reproduction, decision/P2 probe reconciliation |
| P3-STRUCT-002 | Compare continuous versus coarse Structure Intelligence through complete P2 | P3-STRUCT-001, P3-ATTR-001, P3-STRAT-002 | Preregistered C0/C1/C2 A/B replay, exact baseline reproduction, paired comparisons, lifecycle decision |
| P3-STRUCT-003 | Build Continuous Structure Facts v1 | P3-STRUCT-002 | Contract/adversarial semantics, Real-DWD A/B fact digests, direct expert/aggregator consumption, P2 compatibility, outcome-blind review |
| P3-STRUCT-004 | Deliver the bounded Facts v2 B1-B5 vertical slice | P3-STRUCT-003 | `docs/acceptance/structure-facts-v2-b1-b5-final-acceptance-20260907.md`; all six child gates accepted within bounded scope, parent #216 |
| P3-STRUCT-004-B1 | Freeze Facts v2 domains, semantic naming and coverage blueprint | P3-STRUCT-003 | Existing B1 checkpoint and design-entry review; #217 |
| P3-STRUCT-004-B1-R | Separate business clocks, production receipts and consumption audit | P3-STRUCT-004-B1 | Existing temporal ownership checkpoint and design-entry review; #218 |
| P3-STRUCT-004-B2 | Qualify bounded Facts v2 input, time view, identity and coordinates | P3-STRUCT-004-B1-R | Source/view/identity/coordinate readiness, denominator and negative tests; #219 |
| P3-STRUCT-004-B3 | Build prior-20/60 references with qualified target identity | P3-STRUCT-004-B2 | Excluded-T references, target grain, revision and prefix identity tests; #220 |
| P3-STRUCT-004-B4 | Record same-anchor close crossings, loss and reclaim | P3-STRUCT-004-B3 | Equality, frozen target, no fabricated reclaim, gap/action censoring; #221 |
| P3-STRUCT-004-B5 | Qualify minimal episodes, audited consumer and P2 through cold A/B | P3-STRUCT-004-B4 | Exact semantic A/B, persisted audit receipts, P2 compatibility and blinded review; #222 |
| P3-STRUCT-005 | Formalize Support Evolution Fact Family（演化支撑事实族；historical F9） | P3-STRUCT-004, P3-STAGE-001-H | closed / #253; bounded Route-A B3/B4/B5 extension accepted; no B6/B7 or official Stage labels |
| P3-STRUCT-005-A | Freeze Support Evolution object/event/process/measurement semantics | P3-STRUCT-004, P3-STAGE-001-H | closed / #249; `docs/design/structure-support-facts.md`; support role remains a candidate, thresholds stay outside Fact identity |
| P3-STRUCT-005-B | Implement Support Evolution Facts, deep verifier and governed Event Worker route | P3-STRUCT-005-A | closed / #250; historical technical code remains F9; focused counterexamples and `docs/acceptance/structure-f9-release-ready-20260910.md` |
| P3-STRUCT-005-C | Qualify Support Evolution Facts with two independent cold real-DWD Jobs | P3-STRUCT-005-B | closed / #251; exact A/B semantic reproduction and 12/12 outcome-blind review; `docs/acceptance/structure-f9-real-dwd-acceptance-20260910.md` |
| P3-STRUCT-005-D | Freeze accepted Support Evolution runtime bindings and projection boundary | P3-STRUCT-005-C | closed / #252; technical capability ID `B5-F9` remains stable; Stage 5/6/7 remain parked, no labels/consumers/trading |
| P3-STRUCT-006 | Formalize Trend Evolution Fact Family / 趋势演化事实族 | P3-STAGE-003, P3-STRUCT-005 | closed / #268; bounded Trend Evolution Facts accepted from two immutable cold-source artifacts with zero new DWD/provider access |
| P3-STRUCT-006-A | Freeze Trend Evolution Fact Family semantics | P3-STAGE-003, P3-STRUCT-005 | closed / #264; seven threshold-free geometry concepts and direction-aware interpretation boundary frozen |
| P3-STRUCT-006-B | Implement Trend Evolution Facts, deep verifier and fixed Worker route | P3-STRUCT-006-A | closed / #265; immutable-source qualification only; `docs/acceptance/structure-trend-evolution-release-ready-20260910.md` |
| P3-STRUCT-006-C | Qualify Trend Evolution Facts from two immutable cold-source artifacts | P3-STRUCT-006-B | closed / #266; 174 observations / 1,914 measurements, exact A/B fact payloads, market-data/DWD provider access 0 |
| P3-STRUCT-006-D | Freeze accepted Trend Evolution runtime bindings | P3-STRUCT-006-C | closed / #267; seven concepts accepted as `B6-TREND`; Stage 1/12 and 8/9 research remains separately gated |
| P3-STRUCT-007 | Formalize Structure Order Fact Family / 结构秩序事实族 | P3-STRUCT-006, P3-STAGE-003 | closed / #273; bounded Structure Order Facts accepted from two immutable cold-source artifacts with zero new DWD/provider access |
| P3-STRUCT-007-A | Freeze Structure Order Fact Family semantics | P3-STRUCT-006, P3-STAGE-003 | closed / #269; reuse existing range-width/width-change and add only range-center-shift |
| P3-STRUCT-007-B | Implement Structure Order Facts, deep verifier and fixed Worker route | P3-STRUCT-007-A | closed / #270; `docs/acceptance/structure-order-release-ready-20260910.md`; zero new market-data path |
| P3-STRUCT-007-C | Qualify Structure Order Facts from two immutable cold-source artifacts | P3-STRUCT-007-B | closed / #271; 174 observations / 1,218 measurements, exact A/B payloads, market-data/DWD provider access 0 |
| P3-STRUCT-007-D | Freeze accepted Structure Order runtime bindings | P3-STRUCT-007-C | closed / #272; three concepts accepted as `B6-ORDER`; Stage 0/2 proposition thresholds remain separately gated |
| P3-DICT-001 | Deliver the Quanthus Data & Indicator Catalog | P3-FEAT-001 | `docs/design/data-indicator-catalog.md`; #215 with children #223-#227; deterministic authority projection, read-only API/UI and drift gates; no dependency of P3-STRUCT-004 |
| P3-DICT-001-A | Freeze Catalog schema, authority and relation semantics | P3-FEAT-001 | #223; `docs/acceptance/data-indicator-catalog-schema-snapshot-20260907.md` |
| P3-DICT-001-B | Generate deterministic Catalog snapshot from authoritative sources | P3-DICT-001-A | #224; source adapters, deterministic digest/count manifest and required semantic regressions |
| P3-DICT-001-C | Expose authenticated read-only Catalog API | P3-DICT-001-B | #225; GET-only summary/search/detail/relations and `catalog:read` RBAC |
| P3-DICT-001-D | Build the `/catalog` search and detail UI | P3-DICT-001-C | #226; authenticated read-only Web search/detail/relations surface and build/SSR contracts |
| P3-DICT-001-E | Freeze Catalog drift gates and maintenance closeout | P3-DICT-001-D | #227; snapshot/source drift, traceability and full repository/Web gates |
| P3-DICT-002 | Productize Chinese Catalog cards and declared processing lineage | P3-DICT-001 | #232; `docs/design/data-indicator-catalog-productization.md`; `config/data-indicator-catalog-productization.v1.json` |
| P3-DICT-002-A | Separate internal diagnostics from business use limitations | P3-DICT-001 | #228; business response omits maintenance gaps, item limitations remain visible |
| P3-DICT-002-B | Cover all registered entries with Chinese business explanations | P3-DICT-002-A | #229; all-entry coverage, exact formula/identity and human-readable card tests |
| P3-DICT-002-C | Add layered overview and bounded field-focused processing graphs | P3-DICT-002-B | #230; final single-canvas requalification `docs/acceptance/catalog-lineage-single-canvas-20260907.md`; 524-field proof/context classification, exact identity and standard infinite navigation |
| P3-DICT-002-D | Audit, test and release the productized Catalog | P3-DICT-002-C | #231; `docs/acceptance/catalog-lineage-single-canvas-production-closeout-20260907.md`; full gates and Web-only production release `d4e7158` |
| P3-DICT-003 | Rebuild Catalog Lineage around governed ODS/DWD/ADM assets | P3-DICT-002 | #237; accepted; `docs/acceptance/catalog-lineage-v2-production-closeout-20260907.md`; latest Web closeout `docs/acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md`; checkout/Web `c25638a`, API `3aa4837` |
| P3-DICT-003-A | Freeze asset ontology and Research ADM Registry | P3-DICT-002 | #233; closed in Lineage V2 implementation audit |
| P3-DICT-003-B | Implement full registered asset/field closure | P3-DICT-003-A | #234; closed in Lineage V2 implementation audit |
| P3-DICT-003-C | Separate Definition and Usage dependency graphs | P3-DICT-003-B | #235; closed in Lineage V2 implementation audit |
| P3-DICT-003-D | Audit and release Catalog Lineage V2 | P3-DICT-003-C | #236; closed; full gate 1602 passed / 7 existing skips; latest Web navigation refinement `c25638a`, API `3aa4837` |
| P3-STAGE-001 | Learn and qualify stock-stage definitions from history | P3-STRUCT-004, P3-DICT-003 | blocked parent; A-F/I closed; H第二组development mixed closeout后blocked，当前无ready child；J/G remain blocked；2022-2024 fresh reuse forbidden and 2025/daily/consumer/strategy remain closed |
| P3-STAGE-001-A | Freeze first-group semantics, scope and experiment budget | P3-STRUCT-004, P3-DICT-003 | #238 closed; `config/stage-learning/experiment.v1.json`; 3/4 semantic/search/reference/data boundaries frozen; RX6800 full-base mirror claim ceiling recorded |
| P3-STAGE-001-B | Benchmark CPU capacity and qualify the training device | P3-STAGE-001-A | #239 closed; RX6800 WSL CPU-first accepted; `docs/acceptance/stock-stage-learning-b-capacity-20260908.md` |
| P3-STAGE-001-C | Implement minimal Facts inputs and historical harness | P3-STAGE-001-A, P3-STAGE-001-B | #240 closed; real-DWD governed harness accepted; `docs/acceptance/stock-stage-learning-c-harness-20260908.md`; no classifier training |
| P3-STAGE-001-D | Build independent T-only references and target settlements | P3-STAGE-001-C | closed; #241; v2 non-overlapping 120-context supplement resolved 87 definite / 21 ambiguous / 12 coexistence with 14 frozen third-party adjudications and zero audit veto; evidence remains `SYNTHETIC_EXPERT_CONSENSUS`, not HUMAN_EXPERT_REFERENCE |
| P3-STAGE-001-E | Learn constrained rules and calibrate adjacent stages | P3-STAGE-001-D | closed; #242; all 18 R1 trials retained, 4 pass hard gates; frozen development candidate is probe=1.0 / hold=2 / signed-distance=0 / activity=1.0; hold=1 ambiguity and hold=3 noninferiority failures retained; R2 not triggered |
| P3-STAGE-001-F | Evaluate held-out evidence and freeze label qualification | P3-STAGE-001-E | closed / INCONCLUSIVE; #243; Job `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7` consumed the only arbitration budget and failed before panel/prediction/scoring because `qualification_mature_held` supplied 58/80 additional unique episodes; no result artifact/SHADOW/QUALIFIED publication |
| P3-STAGE-001-G | Publish observational post-DWD daily labels | P3-STAGE-001-F | Separately qualified as-known inputs and scheduling; no P6/strategy activation |
| P3-STAGE-001-H | Expand stage groups and evaluate specific consumers | P3-STAGE-001-F | blocked; #245; 第二组development mixed closeout：Stage 10/11冻结development candidate，Stage 5/6/7为INCONCLUSIVE_PARKED；量化/短线/基金三视角review=`PASS_WITH_SCOPE_REDUCTION`，当前development search authority关闭；5/6/7/10/11身份不合并，无final labels、consumers或holdout |
| P3-STAGE-001-I | Repair held-out sampling protocol after consumed F | P3-STAGE-001-F | closed; #247; `qualification-protocol.v2.json` keeps original target caps, removes exact-quota process failure, forbids result-dependent backfill, leaves semantic hard gates unchanged, and opens no holdout access |
| P3-STAGE-001-J | Execute successor external-semantic validation on an untouched holdout | P3-STAGE-001-I | blocked for holdout; #248; development census shows 128 first meets all target caps and freezes successor cohort size=512 using measured compute headroom; specific securities/seed/holdout remain unfrozen, 2022-2024 fresh reuse forbidden, 2025 unopened/unselected |
| P3-STAGE-002 | Formalize Stage 10/11 Projection Contract over Support Evolution Facts | P3-STRUCT-005, P3-STAGE-001-H | closed / #258; formal development Projection Contract accepted; `{10,11}` candidate overlap is legal while `primary_stage=null`; no active qualification/LabelCard authority |
| P3-STAGE-002-A | Freeze nested Stage 10/11 projection semantics | P3-STRUCT-005, P3-STAGE-001-H | closed / #254; `docs/design/stage-10-11-projection.md`; prior Stage-10 confirmation is immutable and Stage 11 does not re-prove current depth |
| P3-STAGE-002-B | Implement formal projection, deep verifier and governed qualification route | P3-STAGE-002-A | closed / #255; fixed Worker dispatch and deep verifier accepted; `docs/acceptance/stage-10-11-projection-release-ready-20260910.md` |
| P3-STAGE-002-C | Qualify projection against two immutable accepted F9 artifacts | P3-STAGE-002-B | closed / #256; Job `job_5e2d82adb2a8492488725fcda379424f` PASS with 241 observations / 2 confirmations / 10 contexts / 6 nested rebound conditions, zero predicate mismatch and zero DWD/provider access |
| P3-STAGE-002-D | Freeze formal Projection Contract and successor boundary | P3-STAGE-002-C | closed / #257; `docs/acceptance/stage-10-11-projection-qualification-20260910.md`; primary-stage arbitration, external validation, LabelCard/daily/strategy/trading stay separate |
| P3-STAGE-003 | Profile third-group morphology and decide state-vs-modifier structure | P3-STAGE-002, P3-STRUCT-005 | closed / #263; 225,246-observation development profile accepted with dimensional reclassification; no active label/search/fact authority |
| P3-STAGE-003-A | Freeze third-group semantic decomposition and morphology axes | P3-STAGE-002, P3-STRUCT-005 | closed / #259; `docs/design/stock-stage-third-group-profile.md`; 0/2, 1/12 and 8/9 subgroups frozen before result access |
| P3-STAGE-003-B | Implement governed 512-security third-group morphology profile | P3-STAGE-003-A | closed / #260; fixed RX Stage Worker route and aggregate-only artifact; `docs/acceptance/stock-stage-third-group-profile-release-ready-20260910.md` |
| P3-STAGE-003-C | Execute frozen third-group morphology profile on RX6800 | P3-STAGE-003-B | closed / #261; Job `stage_313d48cfc06948e0a7fc7a3bd77b2657` PASS; no Stage labels/search/holdout access |
| P3-STAGE-003-D | Review third-group profile and freeze next facts/search route | P3-STAGE-003-C | closed / #262; 1/12 -> Trend Evolution Facts, 0/2 -> Structure Order Facts, 8/9 -> modifier hypotheses; later work needs new contracts |
| P3-STAGE-004 | Test Stage 0/2 structural disorder versus orderly consolidation | P3-STRUCT-006, P3-STRUCT-007, P3-STAGE-003 | closed / #278; INCONCLUSIVE_REFERENCE_SUFFICIENCY; completed experiment, no accepted Stage candidate |
| P3-STAGE-004-A | Freeze Stage 0/2 proposition semantics and finite search | P3-STRUCT-006, P3-STRUCT-007, P3-STAGE-003 | closed / #274; `docs/design/stage-0-2-order-proposition.md`; vocabulary and 81+27 grid unchanged |
| P3-STAGE-004-B | Implement bounded Stage 0/2 development runner and fixed RX route | P3-STAGE-004-A | closed / #275; runtime-only retry preserves frozen Facts/reference/scoring inputs |
| P3-STAGE-004-C | Execute frozen Stage 0/2 development experiment on RX6800 | P3-STAGE-004-B | closed / #276; successful retry reports 224,346 observations and 160 references; terminal-summary evidence, not full per-trial re-audit |
| P3-STAGE-004-D | Review and freeze Stage 0/2 development disposition | P3-STAGE-004-C | closed / #277; Stage-2 references 9 below required 20 block both searches; `docs/acceptance/stage-0-2-order-proposition-result-20260910.md`; no further retry/search |
| P3-STAGE-005 | Deliver scale-explicit 0/2 reference evidence mechanism | P3-STAGE-004 | closed / #282; synthetic engineering only; `docs/acceptance/stage-order-reference-engineering-20260910.md` |
| P3-STAGE-005-A | Freeze scale-explicit business and reference contract | P3-STAGE-004 | closed / #279; `docs/design/stage-order-reference-protocol.md` |
| P3-STAGE-005-B | Implement T-only packets and traceable reviewer ledgers | P3-STAGE-005-A | closed / #280; strict source/opinion binding, retained disagreement and cold readback |
| P3-STAGE-005-C | Accept synthetic engineering closure | P3-STAGE-005-B | closed / #281; synthetic fixtures, not market semantic qualification |

### Scale-explicit reference mechanism

`P3-STAGE-005` implements the owner-approved successor in
[stage-order-reference-protocol.md](../stage-order-reference-protocol.md). It is a reference
mechanism, not a LabelCard or rerun of `P3-STAGE-004`: 20-session primary propositions and
10-session recent conditions have separate evidence and opinion fields. The batch builds each
accepted Fact family once; a reviewer sees only the selected case's T-only history. Raw opinions,
source rows, disagreements and source/stratum denominators are retained in immutable packages.
Independent readback reconstructs the retained evidence rather than trusting only file hashes.
No declared role, AI session or human string automatically proves semantic independence. Missing
inputs do not become Stage 0; no candidate, official stage, new market run or trading gate opens.
Delivery: `../../config/stage-order-reference-delivery.v1.json`; engineering evidence:
`../../acceptance/stage-order-reference-engineering-20260910.md`.

### Retained-input reference feasibility

`P3-STAGE-006` is accepted and closed for real-input reference collection feasibility. Its two
successful Jobs reused the immutable accepted F9 inputs and retained twelve cases and twenty-four
same-origin simulated opinions. Collection feasibility and descriptive coverage are not independent
semantic qualification. The closed delivery contract opens no further Job, DWD, search, holdout,
official label, daily operation or strategy consumption.
Design: [reference feasibility](../stage-order-reference-feasibility.md); plan:
`config/stage-order-reference-feasibility-delivery.v1.json`.

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-STAGE-006 | Real-input reference collection feasibility | P3-STAGE-005 | closed; #287; `../../acceptance/stage-order-reference-feasibility-20260911.md` |
| P3-STAGE-006-A | Freeze source/selection/review/stop | P3-STAGE-005 | closed; #283 |
| P3-STAGE-006-B | Release bounded two-phase Worker route | P3-STAGE-006-A | closed; #284; 1932 pre-release tests passed |
| P3-STAGE-006-C | Execute twelve-case reference collection | P3-STAGE-006-B | closed; #285; two succeeded Jobs, no rerun |
| P3-STAGE-006-D | Freeze feasibility disposition | P3-STAGE-006-C | closed; #286; collection only, no independent semantic qualification |

### Fixed-rule development screen

`P3-STAGE-007` is now closed as the owner-approved fixed-rule decision experiment in
[fixed-rule screen](../stage-order-rule-screen.md). The prepare route reconstructed the old cohort,
excluded historical economic identities and fixed 160 T-only 2021 cases. The first prepare failed
only at a post-selection canonicalization check; its single bound technical retry succeeded. C then
froze 320 same-origin `self_simulated` opinions and sealed them before the single frozen score Job.
The score Job succeeded technically and returned `REVISE_TARGET_OR_PARK`.

Stage 0 is the substantive blocker: definite precision is 0.70, positive-reference coverage about
0.933 and definite-negative FPR about 0.146. All 12 definite false positives are directionally
dominated `NEITHER` references, showing the four summary Facts cannot reliably distinguish coherent
directional relocation from low-direction structural instability. Stage 2 remains a strong exploratory
sub-result with definite precision 1.00, positive-reference coverage about 0.717 and zero definite
false positives. D also found a reference-quality defect: at least 21 ambiguous opinions used recent
10-session behavior as a primary-scale veto despite the frozen semantics. The observed ambiguity
assertion rate is therefore not clean standalone rule-quality evidence, but this does not rescue Stage 0
because its definite-reference failures are independent. No rerun, same-batch threshold tuning, 2025
access, official label, daily/strategy or trading authority opens. Machine plan:
`../../config/stage-order-rule-screen-delivery.v1.json`; closeout:
`../../acceptance/stage-order-rule-screen-cd-closeout-20260911.md`.

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-STAGE-007 | Decide whether one fixed rule warrants broader validation | P3-STAGE-006 | closed; #292; REVISE_TARGET_OR_PARK; `../../acceptance/stage-order-rule-screen-cd-closeout-20260911.md` |
| P3-STAGE-007-A | Resolve business boundaries and freeze one executable seed | P3-STAGE-006 | closed; `../stage-order-rule-screen.md`; synthetic rule-contract tests |
| P3-STAGE-007-B | Bind disjoint sources, fixed sampling and execution | P3-STAGE-007-A | closed; #289; one technical retry consumed, 512 old cohort / 515 identities excluded / 160 fixed cases; `../../acceptance/stage-order-rule-screen-b-prepare-success-20260911.md` |
| P3-STAGE-007-C | Execute fixed references and score once | P3-STAGE-007-B | closed; #290; 160 cases / 320 opinions sealed, one no-retry score Job succeeded |
| P3-STAGE-007-D | Attribute errors and give the business disposition | P3-STAGE-007-C | closed; #291; Stage0 target/representation revision required; Stage2 preserved as exploratory only |

### Directional-boundary repair successor

`P3-STAGE-008` owns the approved design successor in
[directional-boundary repair](../stage-directional-boundary-repair.md). It does not tune the old R0.
Instead it decomposes the 20-session primary judgement into structural instability, coherent
directional relocation and orderly consolidation. Coherent relocation becomes an explicit Stage-0
guard, so directional repricing, correction or trend legs cannot enter Stage 0 by residual logic.
Stage 2 remains frozen.

The minimum Fact extension contains only 20-session range traversal and counter-direction excursion
share. Existing window return and directional efficiency remain owned by Trend Evolution. Stage-007
is used only as a permanently exposed design diagnostic: it supports the choice of Fact shape but
cannot select thresholds or qualify the successor. A four-responsibility adversarial review
(short-term, fund, quant and data/evidence governance) found no open design-direction blocker after
requiring the counter-direction Fact and a primary-first two-phase review ledger. The review is
same-origin simulation, not independent expert validation.

P3-STAGE-008-B implemented those two threshold-free Facts plus Reference Protocol v2. C then passed
exposed diagnostic conformance without threshold learning. D completed a fresh disjoint screen: one
candidate was frozen from 64 calibration cases before evaluation data was read, then 95 morphology-
blind 2021 evaluation cases were reviewed and scored. The repair reduced old-R0 false positives from
23 to 11 while retaining all 15 definite Stage-0 positives, but precision 0.5769 and FPR 0.1692 missed
the frozen 0.80 / 0.10 gates. Eight remaining false positives expose the inherited center-shift-only
Stage-0 path, so the approved semantic repair is not fully realized by the executable candidate. The
concepts remain Catalog-registered but outside accepted runtime bindings. Stage 2 is unchanged and all
market-read / Job / threshold-search authority is now closed. Machine design:
`../../config/stage-learning/directional-boundary-repair-design.v1.json`; final evidence:
`../../acceptance/stage-directional-boundary-repair-d-evaluation-closeout-20260912.md`.

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-STAGE-008 | Repair Stage-0 direction boundary and primary/recent semantics | P3-STAGE-007 | closed; `REVISE_OR_PARK_EVALUATION_FAIL`; repair improved but did not meet fresh Stage0 precision/FPR gates |
| P3-STAGE-008-A | Freeze semantics, path Facts and four-party review | P3-STAGE-007 | closed; `../stage-directional-boundary-repair.md` |
| P3-STAGE-008-B | Implement threshold-free Facts and primary-first reference schema | P3-STAGE-008-A | closed; `../../acceptance/stage-directional-boundary-repair-b-implementation-20260912.md` |
| P3-STAGE-008-C | Synthetic conformance plus exposed-007 regression | P3-STAGE-008-B | closed; PASS_DIAGNOSTIC_CONFORMANCE_ONLY; `../../acceptance/stage-directional-boundary-repair-c-conformance-20260912.md` |
| P3-STAGE-008-D | Fresh disjoint development screen | P3-STAGE-008-C | closed; 95 evaluation cases; precision 0.5769 / FPR 0.1692 / coverage 1.0; `../../acceptance/stage-directional-boundary-repair-d-evaluation-closeout-20260912.md` |
| P3-STAGE-009 | Repair the positive Stage-0 structural-instability proposition | P3-STAGE-008 | closed; `REVISE_OR_PARK_DEVELOPMENT_FAIL`; A/B/C accepted, D stopped at 122-case development / 27 candidates / 0 eligible before evaluation; root-cause evidence `../../acceptance/stage-directional-boundary-root-cause-validation-20260912.md` |
| P3-STAGE-009-A | Freeze positive-instability semantics and adversarial counterexamples | P3-STAGE-008 | closed; `../stage-positive-instability-proposition-repair.md`; no runtime/data authority |
| P3-STAGE-009-B | Implement approved positive-instability contract | P3-STAGE-009-A | closed; `../../acceptance/stage-positive-instability-b-implementation-20260912.md`; no thresholds/data/runtime |
| P3-STAGE-009-C | Synthetic plus consumed-evidence conformance diagnostics | P3-STAGE-009-B | closed; PASS_DIAGNOSTIC_CONFORMANCE_ONLY; `../../acceptance/stage-positive-instability-c-conformance-20260912.md` |
| P3-STAGE-009-D | Fresh task-disjoint validation | P3-STAGE-009-C | closed; `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 2021 evaluation remained unread; `../../acceptance/stage-positive-instability-d-development-closeout-20260912.md` |
| P3-STAGE-010 | Repair positive-instability quantitative projection topology | P3-STAGE-009 | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; D2 sealed 160-case review and scored exact 9 candidates with 0 eligible; residual blocker is fresh Stage0/Stage2 truth-boundary conflict, preserved 2021 unread; `../stage-positive-instability-quantitative-projection-repair.md` |
| P3-STAGE-010-A | Freeze separated channel projection topology and review | P3-STAGE-009 | closed; design approved; `../stage-positive-instability-quantitative-projection-repair.md` |
| P3-STAGE-010-B | Implement deterministic separated-channel projection contract | P3-STAGE-010-A | closed; engineering accepted; `../../acceptance/stage-positive-instability-quantitative-projection-b-implementation-20260912.md` |
| P3-STAGE-010-C | Consumed-evidence topology conformance | P3-STAGE-010-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; no thresholds selected |
| P3-STAGE-010-D | Fresh 2019 development then exact preserved 2021 evaluation | P3-STAGE-010-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; exact 9 trials / 0 eligible; best coverage profile has 2 Stage2 overlaps, zero-overlap neighbor has 0.6296 coverage; 2021 unread; `../../acceptance/stage-positive-instability-quantitative-d2-development-closeout-20260913.md` |
| P3-STAGE-011 | Repair Stage0/Stage2 joint boundary and orderly-consolidation projection | P3-STAGE-010 | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; D1 recovery + sealed D2 review completed, exact 27 pairs scored with 0 eligible; Stage2 repair is strong but Stage0 coverage remains binding; preserved 2021 unread; `../stage-orderly-consolidation-boundary-repair.md` |
| P3-STAGE-011-A | Freeze Stage2 topology and joint 0/2 boundary | P3-STAGE-010 | closed; design approved; `../stage-orderly-consolidation-boundary-repair.md` |
| P3-STAGE-011-B | Implement deterministic orderly-consolidation projection and synthetic counterexamples | P3-STAGE-011-A | closed; engineering accepted; `../../acceptance/stage-orderly-consolidation-boundary-b-implementation-20260913.md` |
| P3-STAGE-011-C | Consumed-evidence topology conformance | P3-STAGE-011-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; `../../acceptance/stage-orderly-consolidation-boundary-c-conformance-20260913.md` |
| P3-STAGE-011-D | Fresh joint Stage0/Stage2 development | P3-STAGE-011-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 190 cases / 380 sealed opinions / exact 27 pairs / 0 eligible; best Stage0 coverage 58/83=0.6988 vs 0.70 while balanced Stage2 is 20/21 TP at precision 0.9524; preserved 2021 remains unread; `../../acceptance/stage-orderly-consolidation-joint-d2-development-closeout-20260913.md` |
| P3-STAGE-012 | Atomic proposition decomposition and compact-disorder boundary | P3-STAGE-011 | closed `CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY`; sole G1 failed frozen 512-identity gate before 2016 development price/evidence loading; no G2/G3/H or market qualification; preserved 2021 unread; `../stage-atomic-proposition-successor-concept.md` |
| P3-STAGE-012-A | Atomic ontology and resolver audit | P3-STAGE-011 | closed `PASS_ATOMIC_PROPOSITION_FIRST`; `../../acceptance/stage-atomic-proposition-a-ontology-audit-20260913.md` |
| P3-STAGE-012-B | Consumed relocation-conflict adjudication | P3-STAGE-012-A | closed `PASS_CONSUMED_SEMANTIC_ADJUDICATION`; P008 guard retune not supported; `../../acceptance/stage-atomic-proposition-b-relocation-adjudication-20260913.md` |
| P3-STAGE-012-C | Compact-disorder observational sufficiency audit | P3-STAGE-012-A | closed `INCONCLUSIVE`; new Fact not authorized; `../../acceptance/stage-atomic-proposition-c-observable-audit-20260913.md` |
| P3-STAGE-012-D | Expert review and bounded repair-route decision | P3-STAGE-012-A/B/C | closed `RECOMMEND_SYNTHETIC_ONLY_ATOMIC_AND_ENVELOPE_ROUTE`; `../../reviews/stage-atomic-proposition-d-expert-review-20260913.md` |
| P3-STAGE-012-E | Atomic reference/resolver + envelope synthetic package | P3-STAGE-012-D | closed synthetic-only; E1/E2/E3 accepted `PASS_SYNTHETIC_CONFORMANCE`; `../../acceptance/stage-atomic-proposition-e-synthetic-acceptance-20260913.md` |
| P3-STAGE-012-E1 | Atomic Primary review/resolver contract | P3-STAGE-012-D | closed; role atomics and mixed-state/projection denominators preserved before projection |
| P3-STAGE-012-E2 | Threshold-free range-envelope prototype | P3-STAGE-012-D | closed synthetic non-Fact prototype; no compact-disorder truth or threshold selected |
| P3-STAGE-012-E3 | Adversarial synthetic conformance | P3-STAGE-012-E1/E2 | closed `PASS_SYNTHETIC_CONFORMANCE` |
| P3-STAGE-012-E4 | Post-E expert review / Fact-projection route decision | P3-STAGE-012-E1/E2/E3 | closed `RECOMMEND_B6_PATH_ENVELOPE_FORMALIZATION_SYNTHETIC_ONLY`; `../../reviews/stage-atomic-proposition-e4-post-synthetic-review-20260913.md` |
| P3-STAGE-012-E5 | Structure-owned B6-PATH envelope formalization design + synthetic qualification | P3-STAGE-012-E4 | closed `PASS_SYNTHETIC_QUALIFICATION`; single Structure-owned algorithm core, proposed trace remains unregistered; `../../acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md` |
| P3-STAGE-012-E6 | Post-E5 registration/revision/research-only/park review | P3-STAGE-012-E5 | closed `REVISE_NEUTRAL_CONCEPT`; current `structured_path_trace` representation is not Catalog-compatible; `../../reviews/structure-directional-path-envelope-e6-registration-review-20260914.md` |
| P3-STAGE-012-E7 | Catalog-facing neutral concept/result-kind revision | P3-STAGE-012-E6 | closed `PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION`; existing `measurement` kind used, E5 semantics unchanged, Catalog/B6-PATH unmodified; `../../acceptance/structure-directional-path-envelope-e7-representation-acceptance-20260914.md` |
| P3-STAGE-012-E8 | Reserved registration decision review | P3-STAGE-012-E7 | closed `REGISTER_RESERVED_MEASUREMENT`; naming/boundary registration approved, Catalog unchanged; `../../reviews/structure-directional-path-envelope-e8-registration-review-20260914.md` |
| P3-STAGE-012-E9 | Additive reserved semantic registration + metadata/planning alignment | P3-STAGE-012-E8 | closed `REGISTERED_RESERVED_MEASUREMENT_NO_RUNTIME_BINDING`; `../../acceptance/structure-directional-path-envelope-e9-registration-acceptance-20260914.md` |
| P3-STAGE-012-F | Freshness inventory + atomic-first development preregistration | P3-STAGE-012-E9 | closed `PREREGISTRATION_FROZEN`; 2016 fresh development selected before data; `../../acceptance/stage-atomic-proposition-f-preregistration-20260914.md` |
| P3-STAGE-012-G | Fresh-2016 atomic development | P3-STAGE-012-F | closed `FAILED_FRESHNESS_CAPACITY_TERMINAL`; engineering asset retained, no retry/G2/G3/H; `../../acceptance/stage-atomic-proposition-p012-capacity-closeout-20260914.md` |
| P3-STAGE-013 | Era × board exposure-aware development reservoir | P3-STAGE-012 | ready `READY_METADATA_ONLY_CAPACITY_CENSUS`; replace whole-identity/random expansion with identity × disjoint-window × PIT-board × exposure accounting; `../../work-items/stage-development-resource-model.md` |
| P3-STAGE-013-A | Metadata-only era × board × exposure capacity census | P3-STAGE-013 | closed `PASS_METADATA_CAPACITY_CENSUS`; 90,067 exposure-aware identity-window capacity vs 18,967 legacy whole-identity capacity; no result-bearing read |
| P3-STAGE-013-B | Freeze board-era quotas and reusable-window policy | P3-STAGE-013-A | closed with additive metadata-only R1 temporal-spread revision; eight cells remain 32 reserve / 24 primary / 8 fallback and current-batch global identity uniqueness stays mandatory |
| P3-STAGE-013-C | Freeze successor result-bearing preregistration | P3-STAGE-013-B | initial exact-plan Job failed before artifact publication on early-STAR strict window slotting; one separately bound capacity-constrained metadata recovery is engineering accepted before any price read |
| P3-STAGE-013-D | Result-bearing successor development | P3-STAGE-013-C | blocked; requires separately accepted preregistration/runtime contract |

## 7. Acceptance

Active cards have no subjective conditions, a repeat run is identical, invalid combinations
are rejected, and a stratified 100-case review reaches the approved agreement threshold.
Shadow labels may fail the quality gate without blocking the vertical slice.
