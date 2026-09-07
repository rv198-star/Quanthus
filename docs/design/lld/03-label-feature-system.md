# LLD 03: Label and Feature System

| Field | Value |
| --- | --- |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | `P3-FEAT-001` accepted; `P3-FEAT-002` accepted; `P3-LABEL-001` accepted; `P3-LABEL-003` accepted; frozen fixture vocabulary accepted; `P3-LABEL-004` deferred pending a formal label proposal; `P3-STRUCT-001/002/003` accepted within their bounded scopes; `P3-STRUCT-004` bounded B1-B5 retrospective vertical accepted; broader v2 capabilities separately gated; `P3-DICT-001` Data & Indicator Catalog accepted in repository and production |
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
source availability/as-of capabilities remain unsupported by this retained data view. The
machine-readable ledger records this bounded runtime acceptance while retaining B6-B11 as separate
unimplemented or unauthorized capability.

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
and `../../work-items/catalog-productization.md`. Membership and conceptual relationships remain
separate from processing edges, and static lineage is not actual consumption evidence. The Structure
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
| P3-DICT-001 | Deliver the Quanthus Data & Indicator Catalog | P3-FEAT-001 | `docs/design/data-indicator-catalog.md`; #215 with children #223-#227; deterministic authority projection, read-only API/UI and drift gates; no dependency of P3-STRUCT-004 |
| P3-DICT-001-A | Freeze Catalog schema, authority and relation semantics | P3-FEAT-001 | #223; `docs/acceptance/data-indicator-catalog-schema-snapshot-20260907.md` |
| P3-DICT-001-B | Generate deterministic Catalog snapshot from authoritative sources | P3-DICT-001-A | #224; source adapters, deterministic digest/count manifest and required semantic regressions |
| P3-DICT-001-C | Expose authenticated read-only Catalog API | P3-DICT-001-B | #225; GET-only summary/search/detail/relations and `catalog:read` RBAC |
| P3-DICT-001-D | Build the `/catalog` search and detail UI | P3-DICT-001-C | #226; authenticated read-only Web search/detail/relations surface and build/SSR contracts |
| P3-DICT-001-E | Freeze Catalog drift gates and maintenance closeout | P3-DICT-001-D | #227; snapshot/source drift, traceability and full repository/Web gates |
| P3-DICT-002 | Productize Chinese Catalog cards and declared processing lineage | P3-DICT-001 | #232; `docs/design/data-indicator-catalog-productization.md`; `config/data-indicator-catalog-productization.v1.json` |
| P3-DICT-002-A | Separate internal diagnostics from business use limitations | P3-DICT-001 | #228; business response omits maintenance gaps, item limitations remain visible |
| P3-DICT-002-B | Cover all registered entries with Chinese business explanations | P3-DICT-002-A | #229; all-entry coverage, exact formula/identity and human-readable card tests |
| P3-DICT-002-C | Add layered overview and bounded field-focused processing graphs | P3-DICT-002-B | #230; evidence, direction, bounded expansion and real browser interaction |
| P3-DICT-002-D | Audit, test and release the productized Catalog | P3-DICT-002-C | #231; complete repository/Web gates and production API/Web acceptance |

## 7. Acceptance

Active cards have no subjective conditions, a repeat run is identical, invalid combinations
are rejected, and a stratified 100-case review reaches the approved agreement threshold.
Shadow labels may fail the quality gate without blocking the vertical slice.
