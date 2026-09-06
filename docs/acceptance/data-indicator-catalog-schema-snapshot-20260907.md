# P3-DICT-001 A/B: Catalog schema and deterministic snapshot acceptance

| Field | Value |
| --- | --- |
| Date | 2026-09-07 |
| Parent | P3-DICT-001 / #215 |
| Children | A #223, B #224 |
| Governing design | `../design/data-indicator-catalog.md` |
| Scope | Read-only authority projection; schema + source adapters + deterministic snapshot |
| Runtime/API/UI claim | Not established by this file |

## 1. Accepted contract

The Catalog has seven v1 entry kinds: ODS dataset, DWD table, DWD field, Feature spec, LabelCard,
semantic family and semantic concept. Each entry has one stable identity, exact authority path and
SHA-256, lifecycle/implementation/verification status, typed details and only explicitly proven
relations. Catalog-only annotations may add display/search/caution text but cannot create entries or
relations.

The builder reads only the explicit source manifest at
`config/data-indicator-catalog-sources.v1.json`; it does not scrape Markdown or infer definitions
from arbitrary repository text. DWD tables/fields are projected from the base machine domain model
plus approved v1.1 extensions and include inherited lineage/time-profile fields. ODS v1 publishes
only the 53 datasets declared by the repository machine Base contract. The accepted ODS package
manifest itself is not present in the checkout, so full package membership and source field schemas
are explicitly gapped rather than inferred from the documentation's 112-item instance inventory.

## 2. Deterministic projection

The full in-memory Catalog currently contains:

```text
entries: 664
DWD tables: 33
DWD fields: 524
ODS datasets: 53
Feature specs: 9
LabelCards: 4
semantic families: 20
semantic concepts: 21
gaps: 8
```

The frozen snapshot manifest is:

`config/data-indicator-catalog-snapshot.v1.json`

It binds source-manifest SHA, schema/builder versions, full projection digest and exact counts. The
full projection is rebuilt from authority at application/test time; it is not committed as a second
large copy. Current full projection digest:

`sha256:6f3d1a4dc804d6c1d5d05b47e1b466e3cc0c80d77f6ff0a7054c5201c184a682`

Two independent in-process builds were byte-identical. `scripts/build_data_indicator_catalog.py
--check` reproduced the frozen manifest exactly.

## 3. Required semantic regressions

The following cases are explicit tests, not UI-only labels:

- `price.sma20@1.0.0` retains input semantics around `price.total_return_index`, unit `index`, and a
  `related_not_equivalent` relationship to `indicator.trend.sma`; it is not a raw-close MA20 alias.
- `price.rsi14@1.0.0` retains the existing total-return/simple-window formula and warns against a
  silent Wilder-RSI replacement.
- `indicator.momentum.macd` is discoverable as a reserved semantic concept and remains without an
  accepted executable spec.
- `dwd_security_bar.turnover` is explicitly described as turnover/amount value, not turnover-rate;
  searching for the Chinese term for turnover rate surfaces the caution rather than inventing a
  denominator.
- DWD knowledge/time-profile fields are present through inherited schema projection.

## 4. Gap ledger

Eight gaps are published. They include the missing repository ODS package manifest, several
reserved indicator concepts without executable specs, unresolved legacy/projection identities, and
research projection datasets that are not silently equated with ODS/DWD tables. A gap is distinct
from zero/false/unavailable market data.

## 5. Verification

Focused evidence:

```text
10 Catalog unit tests: PASS
Catalog snapshot --check: PASS
Ruff on Catalog builder/tests: PASS
```

The unit suite includes duplicate/dangling relation rejection, search/filter bounds, ODS claim
ceiling, exact DWD extension/time-profile coverage and annotation non-authority checks.

## 6. Claim ceiling

A and B accept the Catalog schema and deterministic repository projection only. They do not create a
new indicator formula, prove missing ODS field schemas, authenticate historical source-publication
times, expose an API, establish a Web UI, or grant trading/model authority. Those are separate
children or explicitly out of scope.
