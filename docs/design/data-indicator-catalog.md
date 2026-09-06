# Quanthus Data & Indicator Catalog

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked on production recovery/deployment |
| Governing work item | `P3-DICT-001` |
| GitHub parent | #215 |
| Parent LLD | `lld/03-label-feature-system.md` |
| Data authority | existing ODS/DWD contracts, registries and semantic catalogs |
| Product route | `/catalog` |
| API prefix | `/api/platform/v1/catalog` |

## 1. Goal

Build one read-only catalog surface that lets humans and AI answer, without guessing:

- what a dataset, field, feature, indicator concept, label or Structure concept means;
- which authority owns the definition and which version is being described;
- its grain, units, coordinate, time profile, window, formula and missingness semantics where the
  authoritative source supplies them;
- how it relates to upstream inputs, downstream consumers and compatibility/legacy identities;
- whether the item is merely planned/reserved, implemented, verified or accepted within a bounded
  scope;
- which required metadata is still missing.

The Catalog is a deterministic projection of existing authority, never a parallel formula store or
warehouse. Source contracts remain authoritative when a Catalog entry is incomplete or stale.

## 2. Product boundary

### Included in v1

1. DWD tables and fields from the machine DWD domain-model contract, including inherited lineage and
   time-profile columns.
2. ODS datasets declared by the current authoritative ODS Base contract; dataset-level metadata is
   published only when present in that machine contract. Source file schemas are not guessed from
   prose.
3. Versioned feature specifications from admitted Feature Registry configurations.
4. Label cards from the governed Label Registry configuration.
5. Structure/indicator semantic families and concepts from the semantic naming catalog, including
   reserved concepts without executable specifications.
6. Explicit legacy/compatibility relationships.
7. Gap records for missing machine-readable metadata or unsupported equivalence claims.

### Not included in v1

- online formula editing or indicator authoring;
- arbitrary repository scraping or LLM-inferred definitions;
- Elasticsearch, graph database, RDF/OWL runtime or a second metadata database;
- calculation execution, strategy editing, one-click promotion or trading authority;
- silent equivalence between similarly named indicators;
- fabricated source-publication timestamps or inferred units.

## 3. Authority and deterministic snapshot

The builder consumes an explicit source manifest. Each source adapter understands one existing
machine contract and emits normalized Catalog entries. The builder records the path and SHA-256 of
every source and produces a canonical JSON snapshot without wall-clock generated-at fields.

```text
Authoritative repository sources
        |
        +-- ODS Base contract
        +-- DWD domain model + extension
        +-- Feature registries
        +-- Label registries
        +-- Structure semantic catalog
        +-- selected release identity
        |
        v
Deterministic Catalog Builder
        |
        +-- full in-memory projection (runtime/query object)
        |
        v
config/data-indicator-catalog-snapshot.v1.json
        small frozen digest/count manifest
        |
        +-- read-only Platform API
        +-- /catalog Web UI
        +-- AI/tool consumption
```

The full derived projection is intentionally not committed as a second ~MB-scale copy of source
metadata. A small checked manifest freezes the source-manifest digest, builder/schema version,
full projection digest, entry/gap counts and kind counts. API composition deterministically rebuilds
the full projection from explicit authority sources and refuses startup when it differs from that
manifest. Contract tests perform the same comparison. Neither the manifest nor Catalog-only search
annotations may redefine formulas, schemas or authority relationships.

## 4. Catalog entry model

Every entry has a stable `entry_id`, one `kind`, its original `canonical_id`, source authority, a
bounded lifecycle/verification description and typed details. Supported v1 kinds:

- `ods_dataset`
- `dwd_table`
- `dwd_field`
- `feature_spec`
- `label_card`
- `semantic_family`
- `semantic_concept`

Common fields:

```text
entry_id
kind
canonical_id
display_name
description
domain
family
lifecycle
implementation_status
verification_status
accepted_scope
authority {path, pointer, sha256}
aliases[]
keywords[]
relations[]
details {}
```

`relations[]` contains only declared/provable links, for example:

- `contains` / `field_of`
- `references`
- `input_to` / `depends_on`
- `requires_feature`
- `belongs_to_family`
- `related_not_equivalent`
- `exact_equivalent` only when independently proven
- `replacement` only when the governing source says so

Physical run ids, last-used times and mutable latest pointers never enter semantic Catalog identity.
Actual usage stays in operation/decision audit logs.

## 5. Search and discovery semantics

Free-text search may match canonical id, display name, description, aliases, keywords and selected
formula/input text. Structured filters are exact:

- `kind`
- `domain`
- `family`
- `lifecycle`
- `implementation_status`
- `verification_status`

Semantic prefix membership still uses complete dot-segment boundaries. Free text does not grant
semantic family membership or executable authority.

The API returns deterministic ranking: exact canonical/alias match, prefix match, token match, then
stable `entry_id` order. The service is read-only and size-bounded; v1 loads one checked snapshot
into memory and needs no search engine.

## 6. Required interpretation examples

The first release must prove these examples without manual UI exceptions:

- `price.sma20@1.0.0`: feature spec input is the total-return continuity index and unit is `index`;
  it is related to `indicator.trend.sma` but is not silently equal to a raw-price MA20.
- `price.rsi14@1.0.0`: retained total-return/simple-window formula; not silently replaced by Wilder
  RSI.
- `indicator.momentum.macd`: discoverable reserved semantic concept with no accepted executable spec
  unless/until one is registered.
- DWD `dwd_security_bar.turnover`: amount/turnover value from the DWD contract is not a turnover-rate
  percentage. A turnover-rate measure requires its own denominator/spec.
- DWD knowledge fields and time profile are visible; the Catalog must not claim stronger historical
  `platform_as_known` or public availability than the governing source proves.

## 7. API

Feature id: `quanthus-data-catalog-v1`.

Read-only authenticated operations available to viewer/operator/admin:

```text
GET /api/platform/v1/catalog/summary
GET /api/platform/v1/catalog/search?q=...&kind=...&domain=...&status=...
GET /api/platform/v1/catalog/items/<entry_id>
GET /api/platform/v1/catalog/items/<entry_id>/relations
```

The API reads only the deterministic snapshot. It does not query market-data values or modify
PostgreSQL. Requests are bounded by maximum query length and maximum page size.

## 8. Web UI

`/catalog` is a first-class read-only product route rather than an admin-only editor. It reuses the
existing Platform session. Anonymous users are directed to the operator login flow.

Desktop v1 layout:

```text
search bar + catalog summary
-------------------------------------------------------------
filters          result list                selected detail
kind/domain      canonical id               meaning/status
status/family    aliases/summary             authority/time
                                             formula/spec
                                             lineage/relations
                                             evidence/gaps
```

The UI must visibly distinguish:

- reserved/planned concept vs executable spec;
- active implementation vs verified/accepted scope;
- exact-equivalent vs merely related;
- business time/source availability metadata vs operation usage logs;
- missing metadata/gaps vs zero/false values.

The initial lineage view is a simple typed relation list/flow, not a graph database.

## 9. Maintenance and drift gates

The release adds contract checks for:

- duplicate entry ids or canonical identities within a kind;
- missing authority source or source digest;
- dangling relations;
- unknown lifecycle/verification values;
- DWD fields omitted from the generated table projection;
- registered Feature/Label definitions absent from the Catalog;
- semantic family/concept registration drift;
- stale deterministic snapshot manifest or source-manifest digest;
- legacy links accidentally promoted to exact equivalence;
- required regression examples above.

A source change must refresh the small snapshot manifest in the same change. The full projection is
then reconstructed from those same sources rather than manually edited. Deprecated definitions
remain searchable with their historical identities unless their governing source itself removes
them under an approved migration.

## 10. Issue plan

| Child | Scope | Exit |
| --- | --- | --- |
| `P3-DICT-001-A` | Schema, authority, relation/gap semantics | Contract and adversarial tests |
| `P3-DICT-001-B` | Source adapters and deterministic snapshot manifest | Deterministic generation and required examples |
| `P3-DICT-001-C` | Read-only Platform API | RBAC/OpenAPI/query tests |
| `P3-DICT-001-D` | `/catalog` UI | Build, component/HTML and e2e contract tests |
| `P3-DICT-001-E` | Drift/maintenance gates and closeout | Full repo gate, issue/evidence closure |

Children close in dependency order. Parent closes last. Completion does not authorize new indicator
formulas, B6-B11 Structure expansion, model training, Shadow or live execution.
