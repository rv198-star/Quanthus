# ADR 0006: Product-Bounded Securities DWD/DWS Model

- Status: Accepted
- Date: 2026-08-01
- Decision id: `META-DATA-DWD-002`
- Design status: implementation-approved
- Delivery gate: script implementation open under Issue #55; publication remains gated by Issue #69
- Supersedes: the unimplemented full-industry object proposal recorded earlier under this ADR

## Context

Quanthus is an A-share quantitative research and decision platform. Its warehouse must support
point-in-time replay, market and tradability analysis, company-event research, financial and
ownership analysis, and auditable strategy inputs. It is not a securities-issuance, underwriting,
brokerage, clearing, settlement, or legal-entity registry platform.

The earlier proposal allowed the FIBO semantic backbone to determine the warehouse object graph.
That produced a coherent industry ontology, but it gave first-class domain status to objects such
as legal entities and security issuance even though they do not control a Quanthus research
outcome. It also described DWS only as a future star projection without defining the dimensions,
facts, grains, or point-in-time binding needed to build it.

Industry references remain useful, but they do not own product scope. The controlling authorities
are now separated:

1. the product objective determines which business domains belong in the warehouse;
2. governed ODS evidence determines current source availability and fail-closed gaps;
3. industry references constrain names, relationships, and specialized vocabularies; and
4. physical DWD/DWS contracts determine keys, time semantics, reproducibility, and publication.

Existing DWD and ADS packages remain outside the new build entirely. They are retained without
deletion, but they are not read as sources, migration targets, reconciliation baselines, or
acceptance evidence. The new DWD Base starts from the exact immutable ODS Base and only those
immutable Hotfix children whose manifests bind that exact parent. A Hotfix is a fill-only evidence
overlay, not a replacement Base and not authority to mutate or advance the parent pointer.

This prohibition applies to historical packages, prior Bases, and mutable pointers. It does not
forbid one script from consuming an explicitly declared predecessor candidate produced in the
same frozen build run. Such a candidate supplies referential identities, not new business
evidence, and must match the run id, input-plan digest, knowledge cutoff, manifest, and semantic
digest before any row is read.

## Decision

### 1. Seven first-class business domains

The authoritative business-domain catalog contains exactly seven domains:

1. listed company;
2. security market observations;
3. tradability state;
4. disclosures and company events;
5. financial reporting;
6. industry classification; and
7. ownership and capital.

The listed company is the central business subject. Market, tradability, disclosure, financial,
industry, and ownership facts resolve to that subject directly or through one governed security
and listing identity.

Trading venue, listing code, security identifier, trading calendar, report concept, event term,
and similar objects are not independent business domains. They may exist as internal supporting
structures only when they are required for correct association, historical replay, or referential
integrity. Their existence does not expand warehouse scope.

Data-quality exceptions, lineage, manifests, receipts, and build evidence are governance
sidecars. They are not an eighth securities business domain.

### 2. Explicitly deferred industry objects

The following objects are outside DWD/DWS v1:

- legal-registry entity identity and jurisdiction;
- security issuance, underwriting, offering terms, and issuance arrangements;
- order-level and trade-by-trade market events;
- brokerage accounts, customer positions, clearing, and settlement; and
- dated index constituent membership until a product requirement and governed source justify it.

This is a product boundary, not a claim that those concepts do not exist. Adding one requires a
reviewed model revision tied to a concrete warehouse use and source contract.

### 3. Minimal identity bridge

Quanthus still requires a small identity bridge:

```text
listed company -> security -> listing -> trading venue
```

This bridge prevents code reuse, venue ambiguity, and current-identity leakage. It also connects
issuer-level financial, disclosure, industry, and ownership information to listing-level market
and tradability observations. It does not model the process by which the security was issued.

Provider organization ids and exchange codes remain namespace-qualified, effective-dated
identifiers. They are not legal-registry proof and are never warehouse primary identities.

### 4. DWD physical pattern

DWD stores reusable, source-reconciled business detail as:

- seven core business-object families;
- the minimum conformed identity, code, venue, calendar, concept, and event-term structures needed
  to connect those families;
- atomic source observations and events at their native defensible grain; and
- explicitly identified deterministic business states, such as tradability, whose exact inputs
  and revision digest are retained.

DWD uses a normalized snowflake where normalization protects identity, effective history, or
many-to-many semantics. It does not normalize every vocabulary into an independent table merely
because an industry ontology names it.

Unadjusted prices are the atomic market observations. Adjustment factors remain separate,
effective-dated inputs with queryable links to accepted action/evidence inputs. Adjusted prices are
reproducible projections and are not competing DWD market observations.

### 5. DWS fact constellation

DWS is a deterministic fact constellation derived from one exact accepted DWD package. It uses
shared conformed dimensions for snapshot, date, interval, listed company, security, listing,
industry, financial concept, market index, and event type.

The initial fact catalog contains separate grains for:

- security and index market intervals;
- daily security tradability state;
- security valuation;
- effective adjustment factors;
- disclosure events;
- corporate-action terms;
- financial metrics;
- company-industry membership;
- share capital;
- shareholder count;
- shareholding changes; and
- shareholding positions parsed from the dated top-holder observations retained in ODS.

These facts must not be merged into one company or security wide table. Market intervals,
financial periods, disclosures, industry memberships, and ownership observations have different
grains, revision behavior, and measure semantics.

Every DWS row binds one `snapshot_key`. The snapshot dimension records the exact DWD package,
manifest/content digests, and knowledge cutoff. Versioned dimension keys resolve the state that was
both effective and knowable at that cutoff. A DWS rebuild from the same DWD package, policy, and
cutoff must be byte-deterministic.

### 6. Standards boundary

FIBO remains a semantic reference for company, security, listing, corporate-event, and ownership
relationships. FIX constrains market interval and status vocabularies. Provider-native financial
concepts are sufficient for the initial DWD; XBRL and the applicable China Accounting Standards
taxonomy constrain only rows or projections that claim standardized concept equivalence. ISO MIC
and securities-event references constrain venue and corporate-action mappings.

No external standard can add a first-class Quanthus domain by itself. Provider values cannot claim
standard equivalence until a versioned mapping is accepted.

### 7. Source and delivery boundary

The initial input is exactly the self-contained `ods-base-202607-v1.1` rollup, manifest
`sha256:3a031118af4421b90df7aca8a96bd617a49dba2ef7e49a2074206104991e956e` and content digest
`sha256:86372a3e0237c3bba3ff36e575ab041e1a0a01223587b629e7125b5ac1ba76da`.
Its embedded lineage binds `ods-base-202607-v1` and
`ods-base-202607-v1-hotfix-001`; the older directory names are not DWD inputs and are not required
after rollup construction. The Hotfix repairs the verified 209-security collection-universe
omission while preserving parent precedence. The parent wins every overlapping business key, the
Hotfix may only fill an absent key, and an overlap value mismatch blocks the build.

The machine contract must classify every governed static ODS source, every named recurring release
artifact, and every unique Hotfix dataset id as modeled, ODS-only, superseded, source-gap, or
deferred. A container classification cannot hide its embedded business artifacts.

`source-gap` has one narrow meaning: business evidence required by an in-scope DWD object is absent
from the exact self-contained V1.1 manifest. A decoder, parser, unit
conversion, provider-concept catalog, or identity-normalization implementation is DWD construction
work, not a source gap. Every eligible source record must therefore produce a DWD row or an
explicit quality exception; it cannot be silently omitted because the transform is difficult.

The final ODS Base contains complete three-statement payloads, a quarterly financial projection,
raw F10 capital and top-holder sections, and structured shareholder counts. These sources populate
provider-native financial facts, share-capital observations, and shareholding observations in the
initial DWD. XBRL/CAS mappings and canonical shareholder identities are optional enrichments:
unmapped concepts remain in their versioned provider namespace, and unresolved holders retain the
source name with a null canonical holder id.

Objects outside the seven-domain boundary are classified as ODS-only or deferred rather than
materialized as empty DWD tables.

The detailed model review is complete and Issue #55 opens authority to implement, audit, and run
the DWD/DWS v2 scripts against immutable candidate paths. It does not authorize package
publication, deployment, pointer change, or cleanup. Paired publication remains fail-closed until
Issue #69 passes its independent release gate.

### 8. Acyclic script execution

Physical script ownership follows a directed acyclic graph even when two objects remain in one
business domain. `dwd_trading_calendar` is therefore built by the independent Issue #72 calendar
backbone after Issue #57 venue identity and before Issue #58 market observations. Issue #59 then
builds the remaining tradability objects from the sealed same-run identity, calendar, and market
candidates. This preserves all 33 DWD objects and seven domains while removing the former runtime
cycle between market observations and tradability.

Price-limit regimes are effective prioritized rule scopes. A rule binds a venue and may further
scope by board, security status, IPO phase, or an optional listing override. It is not expanded
into one duplicated rule row per listing. Ambiguous equal-priority matches fail the build.

## Consequences

Positive consequences:

- warehouse scope follows the Quanthus research objective rather than ontology breadth;
- financial, disclosure, industry, ownership, market, and tradability data remain connected by a
  minimal historically correct identity bridge;
- DWD internal structures are distinguishable from user-facing business domains;
- DWS facts, dimensions, grains, and point-in-time binding are reviewable before construction; and
- future industry objects can be added without pretending they are already required.

Costs and constraints:

- DWD still requires effective-dated security/listing identity and calendar structures even though
  they are not first-class domains;
- script orchestration must verify exact same-run predecessor packages and cannot substitute a
  historical DWD package or mutable pointer;
- the same business subject may require different DWS dimension versions at different knowledge
  cutoffs;
- source-native financial facts and detailed ownership positions require explicit parsers and
  source-row coverage evidence as part of this build; and
- a later expansion into issuance, transaction, or account data requires a model revision rather
  than an unreviewed table addition.

## Rejected alternatives

### Let the full industry ontology determine scope

Rejected because semantic completeness is not product relevance. It created domains that do not
control Quanthus research or decision outcomes.

### Remove all non-core objects from physical storage

Rejected because security/listing identity, venue, calendar, report concept, and event terms are
required to join the seven domains without code ambiguity or future leakage. They remain internal
supporting structures rather than first-class domains.

### Build one company or security wide table

Rejected because market intervals, financial periods, events, classifications, and ownership
observations have incompatible grains and revision semantics.

### Make current strategy features the DWD schema

Rejected because strategy features are consumer projections. Product scope bounds DWD, but a
specific strategy does not define its atomic business facts.

### Use existing DWD as the migration target

Rejected because current packages are narrow historical implementation evidence and cannot decide
the new domains, grains, keys, or relationships. They also cannot serve as a row-count or semantic
acceptance baseline for the ODS-only rebuild.
