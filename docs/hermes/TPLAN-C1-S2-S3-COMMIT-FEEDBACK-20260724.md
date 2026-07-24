# TPlan Commit Feedback — #5 C1 / #8 S2 / #9 S3

**Mission:** `p1-c1-s2-s3-20260724`  
**Date:** 2026-07-24  
**Runtime:** Hermes SoT (`/root/work/astock`) + Quanthus (`rv198-star/Quanthus`)  
**Policy:** fail-closed; no default-unknown→normal; no forge `knowledge_cutoff`

---

## #5 C1 — Eastmoney exact-unique weekly + residual 4,173

### Done
| Item | Status | Evidence |
|------|--------|----------|
| Exact-unique EM link only | ✅ | `bin/backfill_em_factor_pit_link_v1.py` |
| Residual fail-closed ledger | ✅ | `ref_adjustment_factor_pit_exceptions.parquet` **4173** |
| Weekly incr hook | ✅ | `bin/weekly_em_factor_pit_incr.py` + `bin/ref-post-weekly.sh` |
| Seal run (no re-fetch) | ✅ | `revisions/adj_factors_pit_em_link_v1/incremental_runs/run_20260724-225218.json` |
| Ledger revision | ✅ | `revisions/adj_factors_pit_exception_ledger_v1/` |
| Quanthus C1 count gate | ✅ | accepts **{4173, 6910}**; correctness/completeness split text |

### Numbers
| Metric | Value |
|--------|------:|
| Factor rows | 46,649 |
| CA linked | 39,739 |
| EM exact linked | 2,737 |
| Residual exceptions | **4,173** |
| Completeness gap | 8.95% |

### Consumer rule
`as_of < knowledge_cutoff` → factor unavailable; never invent earlier visibility.  
**C1 correctness = PASS (no lookahead). C1 completeness = controlled gap (NOT full pass).**

### Commit feedback (#5)
> Weekly path now re-runs exact-unique EM sharebonus linking for residual + lookback codes, freezes residual ledger under `adj_factors_pit_exception_ledger_v1`, and syncs `adjustment_factors` + `adjustment_factor_pit_exceptions`. Residual **4173** remains fail-closed; not claimed as C1 complete.

---

## #8 S2 — Proveable ST/normal + yearly unknown

### Done
| Item | Status | Evidence |
|------|--------|----------|
| Definitive-event classifier tighten | ✅ | `build_st_status_pit_v1.py` (exclude 申请/可能/进展) |
| No unknown→normal default | ✅ | baseline + zero-event = **unknown** |
| Yearly unknown quantification | ✅ | `revisions/st_status_pit_v1/unknown_by_year.json` |
| Gold samples | ✅ | `gold_samples.json` |
| Weekly rebuild hook | ✅ | already in `ref-post-weekly.sh` via `weekly_st_status_pit_incr.py` |

### Numbers (post rebuild)
| Metric | Value |
|--------|------:|
| Intervals | 7,025 |
| Codes | 5,538 |
| Events used | 2,451 |
| unknown intervals | 5,538 |
| proven codes (non-unknown) | **500** |
| normal / ST / delisting_risk intervals | 516 / 479 / 492 |

### Yearly unknown share (selected)
| Year | codes | unknown | unknown_share | ST | delisting_risk | normal |
|------|------:|--------:|--------------:|---:|---------------:|-------:|
| 2011 | 2395 | 2323 | 0.970 | 1 | 19 | 52 |
| 2015 | 2900 | 2674 | 0.922 | 73 | 63 | 90 |
| 2020 | 4269 | 3903 | 0.914 | 166 | 108 | 92 |
| 2024 | 5410 | 4936 | 0.912 | 252 | 57 | 165 |
| 2026 | 5538 | 5038 | 0.910 | 225 | 59 | 216 |

### Interpretation
- **Unknown remains high by design**: cninfo archive only proves ~500 codes with definitive implement/revoke titles.
- Soft titles (可能/申请/进展) intentionally **not** classified — prevents false normal/ST.
- Further unknown reduction needs denser definitive announcements (or exchange name-history archive), **not** name reverse-inference.

### Commit feedback (#8)
> Rebuilt `st_status_pit` with stricter proveable-event rules, yearly unknown quantification, and explicit ban on defaulting unknown to normal. Proven ST/normal coverage improved only where announcements allow; residual unknown is quantified fail-closed risk.

---

## #9 S3 — Mainboard IPO 1,524 windows + joint gold

### Done
| Item | Status | Evidence |
|------|--------|----------|
| 2011+ mainboard IPO windows | ✅ **1524/1524** | META `mainboard_ipo_2011_plus.complete=true` |
| Era split legacy / 44% / reg-5d | ✅ | 237 / 1172 / 115 |
| Joint status×limit matrix expanded | ✅ | 48 cases incl. main IPO eras |
| Matrix exposes rule names + 44% band | ✅ | `reason_code=listing_window_rule` |
| Weekly rebuild hook | ✅ | `ref-post-weekly.sh` → `build_price_limit_regime_pit_v1.py` |

### Rule map (main board)
| list_date | Window |
|-----------|--------|
| 2011-01-01 … 2013-12-31 | `ipo_main_first_day_legacy_special` (session-1 special) |
| 2014-01-01 … 2023-04-09 | `ipo_main_first_trading_day_44pct` (±44) |
| ≥ 2023-04-10 | `ipo_first_5_trading_days_no_limit` (5 sessions) |

### Artifact
- SoT: `data/ref_price_limit_regime_pit.parquet`  
  sha `027d114b068ed95a982bdd7cf8779c7892096f85e39975cc8ed96148a5b6ea1f`
- META + matrix: `data/revisions/price_limit_regime_pit_v1/`

### Commit feedback (#9)
> Filled all **1,524** mainboard IPOs since 2011 with era-correct first-day/window limit regimes and expanded joint gold cases across legacy / 44% / registration. Production consumers must count trading sessions (matrix uses 14d calendar proxy only).

---

## Quanthus code follow-ups
- `src/quanthus/p1_evidence_build.py`: C1 residual count gate **{4173,6910}**; detail text splits correctness vs completeness.

## Explicit non-claims
1. **Not** closing C1/C2/S1/P1 total gates.  
2. **Not** claiming S2 universe-wide ST completeness.  
3. **Not** fabricating factor announce dates or ST normal status.

## Next (optional, not in this commit)
- Denser cninfo ST event harvest / name-history vendor for S2 unknown ↓  
- Point OCI audit snapshot factor-warn count to **4173** when republishing evidence  
- Full weekly EM fetch tick (network) under cron; seal path already offline-safe
