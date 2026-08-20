# P4 Research ADM Consolidation Checkpoint

| Field | Value |
| --- | --- |
| Status | implementation checkpoint; #74 acceptance still open |
| Work item | `P4-EVENT-002` |
| ADR | `docs/architecture/adr-0013-p4-research-adm-and-legacy-ads-retirement.md` |
| ADM policy | `p4-event-research-adm@1.0.0` |
| ADM policy digest | `sha256:31020e184836e1981559aebd7d1c7620e29065d1038df696f932ec3f1d3555e3` |

## Consolidated boundary

P4 now names one explicit application-data boundary between DWD and Event Library. The immutable
`adm/` directory contains:

- `candidate_daily.parquet`;
- `static_industry_mapping.json`;
- `static_industry_coverage_audit.json`; and
- `adm.json`.

`adm.json` binds the ADM policy, DWD Base/correction, source policy, Direct-DWD provider lineage,
static-industry artifact, coverage audit, trading-date scope, and candidate projection. The Event
Library manifest binds the ADM identity/digest separately from candidate-rule and split-policy
identity.

No candidate threshold, feature definition, isolation window, concentration cap, split policy, or
P1/P2 execution rule changes in this consolidation.

## Industry coverage remains open

The production preflight immediately preceding this consolidation proved that current DWD industry
membership covers every current-listed candidate security but not 259 historical-only securities:

- `missing_securities=259`;
- `current_listed=0`;
- `historical_only=259`; and
- `no_company_industry_membership=259`.

The ADM boundary does not invent classifications for those securities. A separately sourced,
checksum-bound, static non-PIT supplement is still required before the #74 production A/B gate can
pass.

## Legacy ADS disposition

ADR 0013 retires ADS as a current application-data concept. Existing operational ADS and
strategy-replay ADS packages remain historical/reproduction artifacts only. Repository deprecation
and physical deletion are deliberately separated: no ADS package is deleted by this change.
Physical cleanup requires a later audit proving no active runtime, rollback, or exact-reproduction
dependency remains.
