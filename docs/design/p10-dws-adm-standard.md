# P10 DWS and ADM Layer Standard

| Field | Value |
| --- | --- |
| Status | Draft for P10 review |
| Owner | P10 permanent data line |
| Applies to | P10 data foundations, application data marts, research projections, and operational access layers |
| DWD authority | The accepted DWD Base and its immutable revisions remain the sole canonical fact layer |
| Related design | `lld/01-data-foundation/dwd-domain-model.md`, `lld/01-data-foundation/operational-ads-base.md`, `../architecture/adr-0013-p4-research-adm-and-legacy-ads-retirement.md` |

## 1. Purpose

This standard answers two questions for every downstream build:

1. Must it create a DWS layer, an ADM layer, both, or neither?
2. What evidence, contract, and physical package are required if it does build a layer?

The standard is demand-gated. It does not require every application to create DWS or ADM.
It also does not permit a new layer to become a second DWD authority.

Normative terms:

- **MUST** and **MUST NOT** are hard acceptance requirements.
- **SHOULD** identifies the default rule; a documented exception is required.
- **MAY** identifies an optional implementation choice.

## 2. Layer definitions

### 2.1 DWD

DWD is the canonical, typed, source-lineaged, point-in-time governed fact layer.

DWD:

- owns canonical business keys, typed values, knowledge time, effective time, source lineage,
  correction history, and quality outcomes;
- binds ODS, catalog, and calendar inputs through immutable manifests;
- MUST NOT read DWS, ADM, ADS, event-library, strategy, or notebook output as a business source;
- MUST NOT be replaced by an application-specific projection.

### 2.2 DWS

DWS is the shared conformed warehouse layer above DWD.

It provides:

- shared dimensions such as snapshot, date, interval, company, security, listing, industry,
  concept, index, and event type;
- reusable facts at stable, cross-application grains;
- explicit measure additivity and time semantics;
- one shared interpretation of identities, dates, intervals, and corrections.

A DWS object is not an application cache. If only one application needs a projection, the projection
belongs in that application's ADM or in job-local TMP, not in DWS.

### 2.3 ADM

ADM is a purpose-specific application access layer derived from DWD or DWS.

ADM may be:

- **research ADM**: immutable input to a named research/application workflow;
- **application ADM**: stable projection serving one product or bounded domain;
- **operational ADM/ADS**: a stricter ADM profile with per-decision evidence, release admission,
  and consumer authorization.

ADM MUST declare its purpose, owner, consumers, allowed uses, forbidden claims, upstream identity,
and semantic version. An ADM does not become DWD, generic DWS, or execution authority merely
because it is published.

### 2.4 TMP and diagnostic output

TMP, notebooks, debug output, one-off exports, and diagnostic scans are not data layers.
They MAY be retained as evidence, but they MUST NOT be registered as DWS or ADM unless they pass
the corresponding gates in this standard.

## 3. Decision matrix

| Situation | Required route |
| --- | --- |
| One consumer, one-off analysis, no stable contract | Direct read of accepted DWD, or job-local TMP |
| Simple row/column projection, filter, or rename with no reusable semantics | Direct DWD view or parameterized query; no DWS |
| One application with a stable purpose and bounded access contract | ADM, built from exact DWD or an accepted DWS |
| Two or more independent consumers need the same conformed grain and semantics | DWS candidate |
| Three or more recurring jobs repeat the same expensive DWD join/aggregation | DWS candidate |
| Multiple business domains need shared conformed dimensions | DWS candidate |
| Application output must be reproducible but is not shared outside its workflow | Research ADM |
| Application output controls a live decision, order, or production state | Operational ADM/ADS with separate PIT and admission review |
| Metric, grain, identity, or PIT semantics are not stable yet | TMP or direct DWD; no DWS/ADM |

A DWS build MUST satisfy at least one demand condition:

- at least two independent consumers; or
- at least three recurring jobs sharing one stable grain and semantic contract; or
- one explicitly approved cross-domain conformed model in which shared dimensions and measures
  materially reduce duplicated interpretation.

An ADM build MUST satisfy all purpose conditions:

- a named owner and named consumer(s);
- a stable application purpose;
- a documented reason that direct DWD/DWS access is insufficient or unsafe;
- a declared allowed-use/forbidden-claim boundary;
- an acceptance path appropriate to research or operational use.

## 4. Mandatory DWS contract

Every DWS package MUST include a machine-readable policy and an immutable package manifest.
The policy MUST define:

| Field | Requirement |
| --- | --- |
| `contract_id` | Stable DWS contract identifier |
| `dws_id` and `dws_version` | Immutable logical identity |
| `owner` | Accountable P10/domain owner |
| `purpose` | Why the shared layer exists |
| `grain` | One exact row grain per fact/object |
| `business_keys` | Exact uniqueness and nullability rules |
| `partitioning` | Business-date or snapshot partition rule |
| `measure_semantics` | Additive, semi-additive, or non-additive per measure |
| `time_semantics` | Effective and knowledge-time behavior |
| `upstreams` | Exact DWD package, catalog, calendar, and correction bindings |
| `dimensions` | Conformed dimension identities and version behavior |
| `facts` | Fact definitions and declared shared dimensions |
| `correction_policy` | Rebuild/correction behavior; no in-place rewrite |
| `retention` | Retention and reproduction horizon |
| `acceptance` | Required structural and semantic gates |

The policy MUST bind exact immutable upstream manifests. It MUST NOT resolve a mutable
`CURRENT` pointer as source identity. Paths are storage hints; manifest hashes and content digests
are the identity.

## 5. DWS physical package rules

DWS packages MUST:

- be built from one frozen DWD package and one knowledge cutoff;
- use deterministic transforms and deterministic ordering;
- reject orphan, ambiguous, or mixed-version dimension resolution;
- bind every fact row to the snapshot and dimension keys used by that build;
- use half-open interval semantics where the source uses intervals;
- preserve source-lineaged keys and typed measures;
- record schema, schema SHA-256, row count, key count, null counts, duplicate-key counts,
  date bounds, byte size, and SHA-256 for every table;
- write `manifest.json` before `READY`, and write `READY` last;
- forbid symlinks and path escapes inside the package;
- publish by atomic rename after physical and semantic verification;
- keep absolute paths out of logical package identity.

DWS packages MUST NOT:

- mix rows from multiple DWD generations or candidate lanes unless an explicit correction contract
  defines the merge;
- silently retune a measure, grain, identity rule, or PIT rule;
- depend on mutable consumer files, notebooks, vendor calls, or ad hoc SQL;
- infer a canonical DWD fact from an ADM/ADS projection;
- overwrite an accepted package when a correction is needed.

## 6. DWS semantic rules

### 6.1 Dimensions

Dimensions MUST define:

- natural/business identity;
- technical key;
- effective interval;
- knowledge interval;
- status and source revision;
- ambiguity or conflict behavior;
- deprecation/successor behavior.

Codes and names are attributes. They are not identities unless the source contract explicitly says
so. Security and listing remain separate where their grains differ.

### 6.2 Facts

Every fact MUST define:

- grain;
- keys and uniqueness;
- shared dimensions;
- additive/semi-additive/non-additive measures;
- effective/observation/reporting date coordinates;
- null and unknown-value semantics;
- correction and late-arrival behavior.

Facts MUST NOT add measures across incompatible units, non-overlapping intervals, or knowledge
versions unless the policy explicitly provides a conversion and aggregation rule.

### 6.3 PIT

A DWS build MUST freeze the exact DWD knowledge cutoff in a snapshot dimension. Historical
dimensions and as-of joins MUST use the same snapshot. If a downstream application cannot prove the
membership version it used, it must consume an explicit ADM/ADS projection that records that
version.

## 7. Mandatory ADM contract

Every ADM package MUST include a policy and manifest. The policy MUST define:

| Field | Requirement |
| --- | --- |
| `adm_id` and `adm_version` | Immutable logical identity |
| `owner` | Accountable application/domain owner |
| `purpose` | One named application purpose |
| `status` | Development, research, accepted, or retired |
| `research_only` | Explicit boolean |
| `execution_authorized` | Explicit boolean; default false |
| `upstream_authority` | Exact DWD/DWS package and revision binding |
| `assets` | Tables/files and their roles |
| `allowed_uses` | Permitted consumer behavior |
| `forbidden_claims` | Explicit non-authority statements |
| `consumer_admission` | Required for operational profiles; otherwise explicit research boundary |
| `semantic_digest` | Reproducible semantic identity |
| `acceptance` | Consumer-specific acceptance evidence |

The existing P4 Research ADM is the reference example of a thin research ADM. It is allowed to be
directly DWD-derived and MUST NOT claim generic DWS or operational ADS status.

An ADM MUST NOT:

- be treated as a canonical DWD fact table;
- silently become a generic DWS surface;
- expose an execution or trading authority flag it was not admitted for;
- merge datasets from different upstream revisions without a correction contract;
- use mutable consumer-specific paths as upstream identity;
- be relabelled as operational ADS without per-decision PIT and release admission evidence.

## 8. ADM physical package rules

An ADM package MUST:

- bind exact upstream identity;
- declare every asset and role;
- write a manifest and `READY` last;
- verify bytes, schema, row count, key count, nulls, duplication, and date bounds;
- preserve its policy and semantic digest;
- be immutable once accepted;
- generate a new ADM version for a semantic change.

Research ADMs MAY be thin and directly derived from DWD. Materialized ADMs need a reason such as
latency, resource isolation, stable application access, or consumer-safe schema reduction.

Operational ADM/ADS adds, in addition to the ADM rules:

- per-decision PIT evidence;
- a generation ledger;
- a bound release set or decision pointer;
- consumer capability separation;
- fail-closed missing/corrupt/unknown behavior;
- independent smoke acceptance;
- explicit execution authorization where applicable.

## 9. Layer promotion and correction

Layer lifecycle:

```text
proposal
  -> policy and ownership
  -> exact upstream binding
  -> deterministic build
  -> structural verification
  -> semantic acceptance
  -> publication / registration
  -> monitoring
  -> supersede or retire
```

Corrections MUST create a new immutable revision. They MUST NOT modify an accepted package in
place. A successor MUST declare the prior revision it supersedes and why. A revoked generation is
not publishable. A correction MUST propagate through DWS and every affected ADM without silently
rewriting historical monthly revisions.

Additive changes SHOULD create a new minor version. Breaking grain, identity, PIT, or measure
changes MUST create a new major version and run in parallel until consumers migrate.

## 10. Ownership and catalog registration

P10 owns:

- the DWS standard;
- canonical DWS policy and promotion rules;
- DWD/DWS lineage and correction propagation;
- the registry that records DWS and ADM identities.

Domain/application owners own:

- ADM purpose and allowed-use policy;
- consumer-specific schema and acceptance;
- operational authorization outside P10's data authority.

Every published DWS or ADM asset MUST be registered in the catalog with:

- layer and asset id;
- owner;
- policy path and digest;
- package manifest and content digest;
- upstream lineage;
- grain and key summary;
- correction/retention behavior;
- consumer admission or research boundary.

## 11. Minimum acceptance evidence

DWS acceptance MUST include:

- exact upstream manifest and content verification;
- complete physical file inventory;
- schema and type verification;
- business-key uniqueness and nullability;
- dimension resolution and orphan checks;
- measure/aggregation semantics;
- PIT snapshot binding;
- full deterministic rebuild comparison on representative data;
- correction propagation test.

ADM acceptance MUST include:

- policy and semantic-digest verification;
- exact upstream binding;
- asset inventory and schema verification;
- business-key and date-bound checks;
- allowed-use/forbidden-claim boundary;
- consumer smoke test;
- reproducibility test using a new process;
- for operational profiles, per-decision PIT and missing/corrupt rejection evidence.

## 12. Explicit non-goals

This standard does not:

- require a generic DWS for every domain;
- require every application to publish an ADM;
- permit an ADM to replace DWD;
- grant operational or execution authority to research output;
- create a second source of truth for identities, calendar, PIT, or corrections;
- preserve the retired OCI operational ADS as a current authority.
