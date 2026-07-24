# S1 formal re-acceptance with terminal_boundary 42 keys — 2026-07-24

## Verdict

**S1 unexplained_missing = 0; invalid_observed = 0** after loading
`ref_delist_day_boundary_exceptions.parquet` (exact **42** `terminal_boundary` keys).

This closes the *session residual* that previously appeared as **39 missing + 3 invalid**
in the pre-boundary attribution report. It does **not** auto-close P1 total gate
(C1 residual 4173 / S2 unknown / consumer evidence package still open).

## Method

```text
quanthus.delisted_coverage.run_delisted_coverage(
  root=/root/work/astock/data,
  bars_file=daily_ohlcv_none.parquet,
  knowledge_cutoff=2026-07-23,
  universe_policy=p1-universe.v2.json  # SSE+SZSE, exclude 689009
)
```

Boundary file present: `data/ref_delist_day_boundary_exceptions.parquet` (42 rows).

## Summary (formal)

| metric | value |
|--------|------:|
| reference delisted securities | 337 |
| in history window | 268 |
| expected sessions | 696,213 |
| observed sessions | 696,171 |
| suspended (known under kcut) | 0 |
| terminal_boundary sessions | **42** |
| unexplained_missing | **0** |
| invalid_observed | **0** |
| complete | 226 |
| complete_with_terminal_boundary | 42 |
| outside_history_window | 69 |

Source checksum (`daily_ohlcv_none`): `142873926c59303769d9193e1327627914d5094099640361a627eeb8d7c0e67b`

Report: `data/revisions/s1_delisted_coverage_with_tb_20260724/s1_report.json`

## Mapping of prior 39+3

| prior residual | count | disposition |
|----------------|------:|-------------|
| s1_residual_39.csv (delist_day_boundary_no_bar) | 39 | exact keys ⊆ 42 terminal_boundary |
| none_delist_day_invalid_ohlc_v1 removed placeholders | 3 | exact keys ⊆ 42 terminal_boundary (000022/000043/300114) |
| qa_data_gaps resolved_invalid_ohlc (688089/143/173 @ 2024-11-06) | 3 | **not** S1 delisted residual; zero-volume retained bars (`data_valid` path / C2 day-status) |

## Gate note

- **S1 residual metric: PASS under terminal_boundary policy**
- C2 full-population tradability_coverage still separate (foundational)
- P1 total / C1 / S2 remain OPEN per product policy
