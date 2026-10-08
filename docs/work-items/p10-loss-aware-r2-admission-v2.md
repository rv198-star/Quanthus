# P10 1m v2 — R2 stores with defects, R3 is quarantined

- **Authority:** Owner explicit 2026-10-09; data quality business-value principle ADR 0016
- **Work item:** [#503](https://github.com/rv198-star/Quanthus/issues/503), parent #495
- **New product:** `intraday-1m-loss-aware-v2`, separate from v1 / strict 240 1m / daily-core
- **Artifact:** `src/quanthus/pipelines/loss_aware_m1_admission_v2.py`
- **Policy:** `config/pipelines/warehouse-minute-loss-aware-admission.v2.json`

## The authoritative warehouse-admission matrix

| Grade | Original source response admitted to formal v2 ODS? | Real OHLCV bars stored in formal v2 DWD? | Default research query? |
| --- | --- | --- | --- |
| R0 | Yes | Yes, exact original observations | Yes, purpose-restricted |
| R1 | Yes | Yes; real missing intervals remain missing | Yes, gap-aware only |
| **R2** | **Yes** | **Yes, with explicit defect fields** | **No; explicit qualified exception only** |
| **R3** | **No** | **No** | No |
| Suspended | Status metadata only | No invented OHLC | Not a normal traded row |

**R3 original response bytes are kept only in immutable forensic quarantine** for original-evidence audit. Minimal R3 *rejection* metadata (symbol/date, reason, grade, raw SHA and reference to quarantined evidence) remains in the status ledger; this is not an admitted market observation. The old v1 `SOURCE_EVIDENCE_READY` package retains historical unqualified original responses for chain-of-custody but must **never** be represented as a v2 admitted ODS or repurposed for 1m strategy consumption.

**R2 actual rows are stored, not upgraded to R1.** Every v2 row carries original source SHA, `quality_grade`, `quality_reason`, `quality_missing_actual_minutes_json`, `quality_critical_gap`, `original_source_observed=true`, `formally_ingested=true`, and `default_research_admitted=false`. Its status record also includes vendor semantic comparison, point-in-time acquisition, auction availability and missing timestamps. Consumer views must explicitly filter `default_research_admitted = true` unless an independently authorized R2-aware algorithm deliberately takes a restricted row. No original missing 1m OHLC is synthesized, even if the missing slot had zero original volume.

Research-date READY remains a **quality/consumer gate calculated from R0+R1** (99% traded universe / 98% each board), not from R2 stored row count; the `R2 → 0` objective is continuous improvement, never a falsely green global all-stock gate.

## Fixed input facts

Existing standalone 1m v1 quality, sources and immutable package receipts (do not refetch):
 
| Date | R2 original grade (v2 to physically store) | R3 source (v2 must exclude) | Research R0+R1 result |
| --- | ---: | ---: | --- |
| 2026-09-24 | 20 | 0 | READY |
| 2026-09-28 | 24 | 0 | READY |
| 2026-09-29 | 18 | 0 | READY |
| 2026-09-30 | 28 | 0 | READY |
| 2026-10-08 | 28 | 3,296 | NOT_READY |

The 10/08 minute vs daily close/volume issues **do not become R2** merely because most volume deltas are small. Only #502 can separately authorize a versioned bounded consumer-specific tolerance after actual impact tests; exact-source mismatch remains R3 under this policy.

## Controlled replay and release

1. Resolve fixed GitHub merge SHA, run focused v2 tests + P3 frozen contract tests, Ruff, and independent adversarial source/grade validator. Existing full-repo CI baseline dual-lane/ADM/web failures do not authorize gate relaxation.
2. Validate `/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v1` as **read-only forensic input**. Each source ODS + DWD/quality ledger has its exact v1 SHA checked. Bind separate daily-core market ODS `daily_ohlcv_none_delta` and source status for each date with its original manifest.
3. Replay and publish new `v2` artifacts **only** under `/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v2`:
   ```sh
   python -m quanthus.pipelines.loss_aware_m1_admission_v2 \
      --day YYYY-MM-DD \
      --v1-product-root /srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v1 \
      --daily-market-release /path/to/verified/YYYY-MM-DD/market-daily-core-ODS-release \
      --v2-product-root /srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v2
   ```
   Exact `--daily-market-release` paths must be physically verified, not constructed from a guessed date prefix.
4. Negative proof: v2 raw originals physically present **iff grade R0/R1/R2**, **no R3 or suspended original-response bytes**; R2 DWD real row count > 0 per R2 code and R2 default consumer false; all real rows date/source SHA checked; per-date metadata covers full 4,992 original code population.
5. Research pointer `CURRENT_RESEARCH_1M_V2.json` advances through 9/30 only under existing R0+R1 status; 10/08 can have a published v2 source/DWD with R2 and R0+R1 subset **without pretending the day is research READY**.
6. Compare byte identity of old v1 sealed manifests, legacy 4-lane and daily-core pointers; do not mutate P1/P3/P5 protected research or P6 production execution. OCI minute mirror is not implied.

## Stop / rollback conditions

- Do not write v2 if an admitted R2 raw does not match original SHA, source date/venue, valid native OHLCV, exact current daily share total and source daily close. Reject rather than downgrade such a source from R3 to R2.
- Do not delete forensic raw while enforcing v2 R3 exclusion; source custody and *formal admission* are separate storage domains.
- No backfilling missing bar values, no cross-vendor time-label assumption, no opaque automatic grade uplift.
- Any same-date correction to immutable v2 uses a versioned successor, not an overwrite.
- If CN01 / NexusDock unavailable, PR and tests can be prepared; **live deployment must be reported pending** until node-side physical verification succeeds.
