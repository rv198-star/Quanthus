# Priority-run delivery — 2026-07-25 (P1 still OPEN)

Order executed:
1. C2/S1 formal re-acceptance with 42 terminal_boundary keys
2. Full security-session tradability enrichment + manifest registration
3. S2 high-coverage source plan (not auto-close)
4. C1 hold as correctness-pass / completeness-controlled (no full pass)
5. trailing whitespace cleanup on a30ffbf docs → `839f718`

## 1) C2 / S1 formal

| metric | result |
|--------|--------|
| unexplained_missing | **0** |
| invalid_observed | **0** |
| terminal_boundary sessions | **42** |
| complete | 226 |
| complete_with_terminal_boundary | 42 |
| outside_history_window | 69 |

Report: `data/revisions/s1_delisted_coverage_with_tb_20260724/s1_report.json`  
Doc: `docs/ASTOCK-P1-S1-TERMINAL-BOUNDARY-ACCEPT-20260724.md`

Prior **39 missing + 3 invalid** map 1:1 into the 42 exact keys (39 residual + 3 removed placeholders).  
`qa_data_gaps` 688089/143/173 @ 2024-11-06 are **not** S1 delisted residuals (zero-volume retained bars / C2 day path).

**S1 residual metric: PASS under terminal_boundary policy.**  
Does **not** auto-close P1 total.

Foundational C2 coverage materialised:
- `data/revisions/tradability_coverage_v1_20260725/tradability_coverage.parquet`
- rows **20,913,249**
- status: trading 13,114,267 / not_listed 6,581,399 / missing 675,047 / delisted 524,700 / suspended 17,836
- SoT: `data/ref_tradability_coverage.parquet`

## 2) Full S3 enrichment (consumer-usable)

| field | value |
|-------|-------|
| revision | `tradability_enriched_s2s3_v1_1_20260725` |
| rows | **20,913,249** (= full coverage) |
| sha256 | `2b39d0bbe5c4e38fcb7051fc369fe0644241d580e7e7fec8b733a5304635da61` |
| unknown_status_share | **~0.946** |
| gate | **OPEN** |
| SoT | `data/ref_tradability_enriched_s2s3.parquet` |
| builder | `bin/enrich_tradability_s2s3_full_v1.py` (year-sharded DuckDB) |
| coverage builder | `bin/build_tradability_coverage_full_v1.py` |

Manifest registers:
- `tradability_coverage`
- `tradability_enriched_s2s3`

Policy in data: `status_unknown_block_new` / never unknown→normal.

## 3) S2 high-coverage sources

Plan only (no false close):  
`docs/ASTOCK-P1-S2-HIGH-COVERAGE-SOURCE-PLAN-20260724.md`

Ladder: exchange short-name history → denser cninfo full-text → vendor isST as corroboration only.

## 4) C1 hold

`docs/ASTOCK-P1-C1-HOLD-NO-FULL-PASS-20260724.md`  
Residual **4173 / 8.95%** — correctness pass, completeness controlled, **no full pass**, **P1 total OPEN**.

## 5) Hygiene

Quanthus `839f718` — strip trailing whitespace in TPlan hermes docs.

## Pack pointer

`data/revisions/p1_formal_pack_20260725/PACK.json`

## Still OPEN (product)

| gate | why |
|------|-----|
| C1 | 4173 residual |
| S2 | unknown ~79% intervals / ~91%+ stocks recent |
| S3 consumer quality | enrichment ships, but unknown blocks most new entry |
| P1 total | PRD: do not auto-close on historical adj hole |

## Next (if authorized)

1. Exchange 简称变更史导入 → S2 unknown ↓  
2. Optional full residual EM force-refresh (beyond 120-code proof)  
3. OCI republish evidence bundle with S1=0 + audit factor-warn=4173  
4. Product decision: accept/reject 8.95% C1 completeness hole
