# LLD 03: Label and Feature System

| Field | Value |
| --- | --- |
| Phase | P3 |
| Status | Design baseline; implementation waits for P2 |
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

## 4. Feature registry

Feature definitions record id, formula, input datasets/columns, units, null policy, lookback,
knowledge-cutoff rule, output type, version, owner, and lifecycle. Cross-sectional percentiles
must use only the complete T-close market snapshot. Insufficient history remains explicit and
is never imputed from future rows.

## 5. Daily-bar anatomy

The first release may derive wick/body ratios, close location, volume ratio, turnover
percentile, volume-direction interaction, gap frequency, multi-day open/close relationships,
and range contraction/expansion. Minute sequences are not direct model inputs; later minute
features must be predeclared summaries.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-FEAT-001 | Implement feature registry and lineage model | P2 snapshots | Version and cutoff tests |
| P3-LABEL-001 | Implement label-card schema and evaluator protocol | Feature registry | Deterministic fixture |
| P3-LABEL-002 | Implement vertical-slice tradability/stage/risk labels | P3-LABEL-001 | Golden label cases |
| P3-LABEL-003 | Build label frequency and conflict audit | Registry | No invalid coexistence |
| P3-LABEL-004 | Build human review corpus and adjudication form | Evaluators | 100 reviewed cases |

## 7. Acceptance

Active cards have no subjective conditions, a repeat run is identical, invalid combinations
are rejected, and a stratified 100-case review reaches the approved agreement threshold.
Shadow labels may fail the quality gate without blocking the vertical slice.
