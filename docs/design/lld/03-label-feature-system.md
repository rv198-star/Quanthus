# LLD 03: Label and Feature System

| Field | Value |
| --- | --- |
| Phase | P3 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | `P3-FEAT-001` accepted; `P3-FEAT-002` accepted; `P3-LABEL-001` accepted; `P3-LABEL-003` accepted; concrete labels not started |
| Gate source | `config/p2-gates.v1.json` |
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

## 5. Daily-bar anatomy

The first release may derive wick/body ratios, close location, volume ratio, turnover
percentile, volume-direction interaction, gap frequency, multi-day open/close relationships,
and range contraction/expansion. Minute sequences are not direct model inputs; later minute
features must be predeclared summaries.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-FEAT-001 | Implement feature registry and lineage model | P2 snapshots | Version and cutoff tests |
| P3-FEAT-002 | Implement research baseline daily evaluator | P3-FEAT-001, P3 replay bundle | Split-continuity, cutoff, and real-panel audit |
| P3-LABEL-001 | Implement label-card schema and evaluator protocol | Feature registry | Deterministic fixture |
| P3-LABEL-002 | Implement vertical-slice tradability/stage/risk labels | P3-LABEL-001 | Golden label cases |
| P3-LABEL-003 | Build label frequency and conflict audit | Registry | No invalid coexistence |
| P3-LABEL-004 | Build human review corpus and adjudication form | Evaluators | 100 reviewed cases |

## 7. Acceptance

Active cards have no subjective conditions, a repeat run is identical, invalid combinations
are rejected, and a stratified 100-case review reaches the approved agreement threshold.
Shadow labels may fail the quality gate without blocking the vertical slice.
