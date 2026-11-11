# P10 Daily canonical price basis candidate

Status: **candidate implementation, not activated or a qualified historical handoff**.
Owner: P10; issue [#473](https://github.com/rv198-star/Quanthus/issues/473).
Machine contract: `config/pipelines/daily-price-basis.v1.json`.

| Field | Value |
| --- | --- |
| Design status | draft; non-governing release candidate under the approved physical model |
| Delivery gate | blocked; actual handoff/publication and consumer activation not authorized |
| Parent | [LLD 01](../01-data-foundation.md) |
| Ownership | [LLD 12](../12-permanent-data-line.md) |
| Work item | META-DATA-GAP-004 / #473 |
| Code | `src/quanthus/pipelines/securities_warehouse/daily_price_basis_domain.py`, `daily_price_basis.py` |
| Tests | `tests/unit/test_p10_daily_price_basis.py`, `tests/unit/test_p3_strat_003_semantic_equivalence.py` |

This draft describes a bounded code candidate. It creates no new execution or release authority.

## Existing authority and minimal repair

The approved physical model (`dwd-domain-model.v1.json`) already defines atomic security bars
as unadjusted and requires `price_basis_equals_unadjusted`. Base market-observation construction
uses `UNADJUSTED`. Daily construction instead emits `none`, while multiple consumers filter
`UNADJUSTED`. The completed 35-session diagnostic and temporary adapter prove the specific
historical mismatch; neither authorizes another attempt or establishes historical availability.

This candidate retains the versioned `build_daily_domain_increment` implementation in
`daily_price_basis_domain.py`, isolated from the byte-frozen legacy `daily_domain.py`. Explicit
`price_basis_policy="p10-daily-unadjusted-v1"` selects the new version. It is not a new scheduler,
recovery service, cache or research-owned adapter. Ordinary calls retain the legacy projection,
package kind and manifest version. No command, schedule or consumer selects the new policy yet.

Applicability is restricted to the verified four-lane `daily_ods_input_catalog`, ODS release schema
1, and the exact `market_daily_core/daily_ohlcv_none_delta` member selected by the existing
catalog verifier. Its release manifest, member SHA, source revision, date and producer code revision
remain bound in the existing package lineage. This is a producer correction for newly authorized
construction, **not** permission to interpret arbitrary historical `none` values. Historical DWD
packages are not an accepted input to this entrypoint.

Only the `1d` projection changes `none` to `UNADJUSTED`. Unknown basis, schema, source SHA/revision,
identity, invalid OHLC or invalid time ranges fail closed. The historical temporary receipt counted 21 DuckDB-visible fields, including hive-derived
`market_date`; producer Parquet has 20 physical fields. This candidate reports the actual physical
comparison count, and the package binds `market_date` separately. The 20-column physical schema and
Decimal(38,10) types remain unchanged. Nullable volume and turnover remain nullable as the physical
model allows; they are counted and do not establish tradability. Other intervals are unchanged.

## One construction, independent exact readback

The existing verified ODS members and Base references feed one job-local TMP bar projection.
Validation, mapping and equality consume that same TMP. It is removed when the connection closes;
there is no cross-Job reuse. The canonical Parquet is produced by DuckDB. A separate streaming Arrow
readback restores only the declared 1d basis and compares all 20 typed fields exactly against the
unchanged TMP. Prices, quantities, source identity/revision/SHA and original knowledge and observation
clocks are preserved. No Float cast, rounding, imputation, cutoff rollback or clock inference occurs.

This independent equality algorithm verifies serialization; it is **not independent cold source
construction**, economic availability, or replacement comparison against prior temporary artifacts.
Those statuses are explicitly `NOT_PERFORMED` in the candidate audit.

Explicit candidate verification also checks the serialized Parquet field order and physical types
before DuckDB can normalize timestamp precision, then verifies its canonical values, source and
count bindings, calendar scope and coverage against the retained audit. Relabeling an old manifest
or merely resealing inconsistent bytes cannot satisfy this contract. Default-v1 compatibility has
a byte-for-byte synthetic comparison against the exact SHA-pinned original builder; the frozen
P3 loader retains positive tests with that original source and rejection tests for the new runtime.

The per-date audit retains the union of source status and bar identities, including status-only,
bar-only, non-tradable status, zero activity, nullable quantities and raw knowledge after close.
It never calls non-tradable status or zero activity a proven suspension. `NOT_INFERRED` keeps that
classification unresolved pending governed evidence. Source-row conservation and duplicate/lineage
checks continue to run before the package is sealed. A candidate receipt does not prove the original
35-session population or admit a consumer window.

## Admission and deployment boundary

Explicit policy creates manifest schema 2, kind `cn01_daily_dwd_price_basis_candidate`, and binds the
full versioned basis contract. Its package id has a policy suffix. The contract always says
`decision_admissible=false` / `P10_CONSUMER_AND_PIT_ADMISSION_REQUIRED`. The normal verifier rejects
this kind, so CURRENT advancement, warehouse mirroring/notifications and default P6 admission cannot
consume it. Only an explicitly policy-aware verification call can inspect its candidate integrity.
Legacy v1 packages cannot satisfy an explicit request for this policy; an existing output is not
silently relabeled or accepted as repaired.

No current P6 producer/consumer default, source policy, timer, node, accepted package, original
research request, attempt marker or temporary receipt changes. GitHub distribution and each existing
operational gate continue to apply. This code does not create a deployment route.

Impact inventory at the reviewed main revision: canonical filters exist in
`forward_validation/prospective_source.py`, `backtest/direct_dwd_provider.py`,
`stage_learning/source.py`, `stage_learning/qualification_source.py`,
`stage_training_capacity.py` and `platform/product_query.py`. None is changed or activated here.
Base already uses the canonical value. Historical and prospective consumers each need their own
bounded handoff/release review; this list is exposure analysis, not authorization to modify them.

## Remaining formal #473 handoff

1. Approve publication/release of the versioned implementation; this local candidate has not been
   activated or deployed. Source integration preserves the candidate without selecting it. Select the exact bounded historical correction or prospective construction
   scope separately; this implementation does not rebuild the 35 packages.
2. Freeze authorized immutable source members/hashes, producer/source versions, full dates/universe,
   clock evidence grades, exclusions and replacement lineage. Verify actual bytes in the authorized
   execution environment. The previous diagnostic and temporary attempts remain spent.
3. Produce the separately authorized immutable handoff and independent comparison with the original
   physical fields and prior temporary artifacts. Preserve the complete original window and per-day
   denominator; resolve or formally retain status/suspension/invalid/missing classifications.
4. Each consuming line admits that handoff against its own unchanged contract. Historical P3
   availability still depends on #474; B remains NO_PASS and D remains inactive. P6 requires its own
   authority/release gate before any behavior change. Issue closure never substitutes for acceptance.

The none-to-UNADJUSTED vocabulary repair needs no new scientific judgment under the approved physical
model. The unresolved decisions are operational scope/activation and consumer/PIT acceptance, not
permission to silently expand this alias to other source families, schema versions or intervals.
