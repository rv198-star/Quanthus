# S2 high-coverage PIT source plan — 2026-07-24

## Current ceiling (honest)

| metric | value |
|--------|------:|
| ST intervals | 7,025 |
| unknown intervals | 5,538 |
| proven codes (non-unknown labels) | ~500 class |
| 2026 unknown share of stocks | ~0.91 |

Cause: cninfo archive yield for definitive ST/摘帽/摘星 titles is sparse;
builder correctly refuses `unknown → normal`.

## Recommended source ladder (priority order)

1. **Exchange name-history / short-name change ledger** (SSE/SZSE 证券简称变更)
   - Best lever for historical ST/*ST markers embedded in short names.
   - Delivery: intervalize name spans with announce/effective dates → PIT kcut.
2. **cninfo full-text harvest expansion** (not title-only)
   - Keywords: 风险警示、撤销风险警示、其他风险警示、退市风险警示、摘帽、摘星.
   - Require NOTICE/announce date; no fuzzy effective_date invention.
3. **Baostock / vendor isST day flags as secondary corroboration only**
   - Never sole SoT (often present-name biased); use to propose candidates → human/archive confirm.
4. **Manual gold list** for high-impact names (optional ops)

## Executable entry points (Hermes)

| step | script / path | notes |
|------|----------------|-------|
| rebuild S2 | `bin/build_st_status_pit_v1.py` | writes **new** `st_status_pit_v1_1_YYYYMMDD` revision |
| weekly | `bin/weekly_st_status_pit_incr.py` via `ref-post-weekly.sh` | archive-driven |
| year stats | `revisions/st_status_pit_*/unknown_by_year.json` | fail-closed quant |
| gate policy | unknown remains unknown; consumer `block_new` | **no auto-close** |

## Acceptance bar to reopen S2 close discussion

Not "unknown=0". Propose product bar, e.g.:

- 2011–2020 unknown interval share ≤ X% **or**
- listed universe as-of each year-end with proven status ≥ Y%

Until then: **S2 OPEN**, tradability enrichment may ship full parquet with
`status_unknown_block_new` dominating executable_ok=false.

## Out of scope for this plan

- Inventing normal from current short name
- Closing P1 because enrichment parquet exists
