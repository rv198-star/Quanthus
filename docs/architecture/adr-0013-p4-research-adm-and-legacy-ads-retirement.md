# ADR 0013: P4 Research ADM and Legacy ADS Retirement Boundary

- Status: Accepted
- Date: 2026-08-20
- Decision id: `P4-EVENT-002`
- Design status: approved
- Delivery gate: P4 Research ADM active; historical-industry supplement and #74 A/B acceptance open
- Extends: ADR 0007 P4 handoff boundary

## Context

P4 consumes the accepted DWD V2.1 Base and correction directly. During the first full-corpus Event
Library acceptance run, the P4 application path exposed two separate mismatches that should not be
solved by expanding DWD semantics:

1. the old P3 static-industry reference used a legacy security-id namespace; and
2. DWD `dwd_company_industry_membership` intentionally represents the first defensible current
   industry snapshot and has no fabricated historical backfill.

The second mismatch leaves 259 historical-only P4 securities without current DWD industry
membership. All 259 are outside the current-listed set; current-listed missing coverage is zero.
P4 needs only a static non-PIT grouping for concentration control, not a claim about each company's
historical industry at event time.

At the same time, the repository retains several historical objects named ADS. Those packages were
part of the former dual-lane/replay migration architecture. ADR 0007 already removed generic ADS
production from the current operating path, but the retained names, builders, configuration, and
physical packages can still be mistaken for current application authority.

## Decision

### 1. Introduce one thin P4 Research ADM

P4 has one explicit application-data-mart boundary between DWD and the Event Library:

```text
DWD V2.1 + correction
        |
        v
P4 Research ADM
  - candidate_daily.parquet
  - static_industry_mapping.json
  - static_industry_coverage_audit.json
  - adm.json
        |
        v
P4 Event Library
  - events.jsonl
  - entry-partition.jsonl
  - summary.json
  - manifest.json / READY
```

The ADM is purpose-specific and immutable. It is not a generic DWS, not a replacement for DWD, and
not a new enterprise warehouse layer. `config/p4-research-adm.v1.json` freezes its identity,
allowed assets, allowed uses, and forbidden authority claims.

The physical package is stored below `adm/` inside one Event Run. `adm.json` binds the exact DWD
Base/correction, source-policy digest, provider lineage, candidate projection, static-industry
mapping, coverage audit, and ADM-policy digest. Event identities continue to bind the exact
application snapshot through the ADM manifest digest.

### 2. Keep DWD semantics strict

DWD V2.1 remains unchanged. Current DWD industry membership may populate the ADM where evidence
exists, but the ADM must not rewrite DWD or describe a static application grouping as historical
PIT truth.

The 259 historical-only securities are therefore an ADM coverage problem. A future checksum-bound
historical static-industry supplement may complete the ADM, provided that it:

- maps to governed DWD `security_id` values;
- is explicitly static and non-PIT;
- is used only for concentration control;
- records source lineage and deterministic mapping rules; and
- makes no claim that the assigned industry was the security's industry at each historical event
  date.

### 3. Event Library consumes ADM, not mixed upstream references

The Event Library consumes the prepared ADM and P1/P2 T+1 execution evidence. It must not directly
fall back to old strategy ADS, portable research bundles, mutable pointers, or ad-hoc industry
files when ADM coverage fails.

A missing ADM coverage requirement fails closed before the expensive forward scan when possible.
A successful Event Library manifest binds the ADM identity and digest separately from the candidate
rule and split-policy identities.

### 4. Retire the ADS name from the current architecture

No new Quanthus application dataset should be called ADS. The term is reserved for retained
historical artifacts created under the superseded dual-lane/replay architecture.

The following are historical/reproduction surfaces, not current runtime authority:

- operational ADS Base and daily ADS packages;
- strategy-replay ADS policies and publication tooling;
- old dual-lane operational ADS outputs; and
- retained acceptance records that prove those historical releases.

They remain readable only where an explicit historical reproduction path still requires them.
Default P4 and current production paths must not resolve or infer them.

### 5. Physical ADS cleanup is a separate destructive gate

Repository deprecation and physical deletion are separate actions. Physical ADS packages may be
removed only after a cleanup audit proves all of the following:

1. no current production service, worker, timer, provider config, or pointer resolves the package;
2. no active acceptance gate depends on reading the physical package;
3. immutable manifest/content digests and acceptance records needed for historical provenance are
   retained; and
4. rollback/reproduction requirements identify any package that must remain archived.

This ADR authorizes retirement planning and removal of active/default ADS references. It does not
by itself authorize deleting a physical package that has not passed the cleanup audit.

## Consequences

Positive consequences:

- DWD remains a strict reusable fact layer instead of absorbing P4-only static semantics;
- P4 application inputs have one explicit manifest-bound home;
- the historical-industry supplement can be solved locally without a DWD 2.2 rebuild;
- Event Library lineage becomes easier to audit; and
- the old ADS name stops competing with the current application-data boundary.

Costs and constraints:

- P4 now has one additional small governed contract and manifest;
- the 259 historical-only securities still require a static ADM supplement before #74 can pass;
- historical ADS code and packages cannot be deleted in one unreviewed bulk operation; and
- exact P3/legacy reproduction may continue to retain selected old ADS artifacts until explicitly
  retired.

## Rejected alternatives

### Upgrade DWD to V2.2 solely for the 259 securities

Rejected because P4 does not require historical PIT industry truth. Adding invented or weakly
sourced historical membership to DWD would broaden canonical semantics for an application-only
concentration need.

### Reuse the old 1,687-security P3 ADS/reference

Rejected because it is both identity-incompatible with governed DWD security ids and incomplete for
P4's full historical universe.

### Build generic DWS before P4

Rejected for the same reason as ADR 0007: the current need is a small application projection, not a
shared cross-domain dimensional warehouse.
