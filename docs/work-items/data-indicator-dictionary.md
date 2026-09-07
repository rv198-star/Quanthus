# P3-DICT-001: Quanthus Data & Indicator Catalog

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Governing work item | `P3-DICT-001` |
| GitHub epic | `rv198-star/Quanthus#215` |
| Children | #223 A; #224 B; #225 C; #226 D; #227 E |
| Governing design | `../design/data-indicator-catalog.md` |
| Parent/owner | `../design/lld/03-label-feature-system.md` |
| Platform surface | read-only API + `/catalog`; no new metadata database |
| Current status | A-E closed; original foundation accepted; productization successor `P3-DICT-002` is tracked in `catalog-productization.md` |

## 1. Purpose

This work item turns the earlier deferred “data/indicator dictionary” request into a maintained,
queryable Catalog for humans and AI. It answers what a dataset, field, indicator/Feature, Label or
semantic Fact concept means, where its authority lives, how it is calculated when an executable
spec exists, what time/coordinate semantics apply, how it relates to other definitions, and what
has actually been verified or accepted.

The Catalog does not own a second copy of warehouse schemas or indicator formulas. Existing ODS/DWD
contracts, Feature/Label registries and semantic catalogs remain authoritative. Catalog-only search
annotations may improve discovery and warn about common misreadings but cannot create formulas,
relations or execution authority.

## 2. Delivered children

### A — Schema and authority (#223, closed)

Frozen seven Catalog entry kinds, strict authority SHA/pointer binding, lifecycle/implementation/
verification distinctions, typed relation vocabulary and explicit gap semantics. Actual Fact or
indicator use-time remains in decision/operation audit logs rather than Catalog semantic identity.

### B — Authority adapters and deterministic snapshot (#224, closed)

The explicit source manifest projects the current repository authority into 664 entries:

```text
33 DWD tables
524 DWD fields
53 machine-declared ODS datasets
9 Feature specs
4 LabelCards
20 semantic families
21 semantic concepts
10 explicit gaps
```

The full projection is reconstructed deterministically. A small checked snapshot manifest binds the
source-manifest SHA, builder/schema versions, projection digest and counts. It avoids checking in a
second large copy of derived metadata.

Evidence: `../acceptance/data-indicator-catalog-schema-snapshot-20260907.md`.

### C — Read-only Platform API (#225, closed)

Viewer/operator/admin receive `catalog:read`. Four GET-only API routes expose summary, bounded
search, exact detail and typed relations. There are no Catalog migrations, startup hooks, database
writes or market-value queries.

### D — `/catalog` product UI (#226, closed)

The authenticated first-class `/catalog` page provides search, structured filters, detail,
authority/time/spec presentation, visible gap ledger and outgoing/incoming relation navigation.
Operator and attribution surfaces link to it. No editor or promotion action is exposed.

Evidence: `../acceptance/data-indicator-catalog-api-ui-20260907.md`.

A post-acceptance semantic audit found and repaired two Catalog projection defects without changing
source formulas: `related_not_equivalent` links for legacy SMA/RSI no longer suppress missing
executable-spec gaps, and the Structure blueprint now explicitly projects the seven B3-B5 concepts
that have bounded accepted runtime evidence. Current audit evidence is
`../acceptance/data-indicator-catalog-post-acceptance-audit-20260907.md`; the audited production
closeout is `../acceptance/data-indicator-catalog-post-acceptance-production-closeout-20260907.md`.

## 3. Required regression examples

The Catalog must continue to make these distinctions explicit:

- `price.sma20@1.0.0` uses the total-return continuity index and unit `index`; it is related to but
  not silently equivalent to a raw-price MA20/SMA concept.
- `price.rsi14@1.0.0` retains the existing total-return/simple-window definition and is not silently
  replaced by Wilder RSI.
- `indicator.momentum.macd` is currently a reserved semantic concept without an accepted executable
  MACD spec.
- DWD `turnover` is turnover/amount value, not turnover-rate percentage; a rate requires a qualified
  denominator and separate calculation spec.
- time-profile/knowledge metadata must not be interpreted as stronger historical availability than
  the governing source proves.

## 4. Maintenance child E

`P3-DICT-001-E` freezes maintenance and drift checks, synchronizes design/work-item/Platform docs,
and requires full repository/Web gates plus production deployment/smoke before parent closeout.
The original post-acceptance production evidence remains in
`../acceptance/data-indicator-catalog-post-acceptance-production-closeout-20260907.md`.
Current productization `P3-DICT-002` uses production checkout/Web `73f1b40` while API remains
`0c671f9`; the latest infinite-viewport evidence is in
`../acceptance/catalog-lineage-infinite-viewport-production-closeout-20260907.md`, with earlier
node-identity/fixed-canvas evidence retained separately and work items in `catalog-productization.md`. A source change that changes the deterministic Catalog must refresh
the snapshot manifest in the same repository change. Original final evidence remains in
`../acceptance/data-indicator-catalog-final-acceptance-20260907.md`.

## 5. Non-goals and claim ceiling

This delivery does not implement every planned MACD/CCI/KDJ/ATR formula, rebuild ODS/DWD, create a
metadata editing backend, open Structure B6-B11, train a model, or authorize Shadow/paper/live
execution. Missing metadata remains an internal diagnostic rather than being synthesized from
prose or an LLM. Productization removes the developer dashboard but keeps relevant limitations on
business cards.
