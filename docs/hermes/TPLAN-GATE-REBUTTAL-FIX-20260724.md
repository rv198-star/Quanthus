# TPlan Gate Rebuttal Fix — 2026-07-24 (still OPEN)

Responds to review:
1. #5 lacked real weekly fetch evidence (`skip_fetch=true` only)
2. #8 overwrote v1 / manifest stale SHA / ~91% unknown
3. #9 matrix used Saturday as IPO case; no open-session proof; not wired to tradability/snapshot

## Verdict

**Still cannot close #5 / #8 / #9 gates.**  
This pass only removes the three review defects. Residual risk remains fail-closed.

| Item | Gate | Why still open |
|------|------|----------------|
| #5 C1 | **OPEN** | Residual **4173** fail-closed; not C1 full pass |
| #8 S2 | **OPEN** | Unknown interval share still **~0.79–0.91** class; not proven ST/normal history |
| #9 S3 | **OPEN** | Board+IPO templates complete; full tradability coverage enrichment sample-only / contract; P1 acceptance blocked |

---

## #5 C1 — real network weekly evidence

| Artifact | Path / value |
|----------|----------------|
| Force-refresh fetch | `data/revisions/adj_factors_pit_em_link_v1/fetch_evidence/fetch_20260724-232305.json` |
| Network hits | **120 / 120** (`force_refresh=true`, `cache_hits=0`, HTTP 200 sample) |
| Weekly run | `…/incremental_runs/run_20260724-232706.json` → `skip_fetch=false`, `force_refresh=true` |
| Residual | still **4173** fail-closed |
| Factors sha | `081b9df7a21e9421…` |
| Exceptions sha | `1960a84d4637b899…` |
| Weekly default | `ref-post-weekly.sh` now passes `--force-refresh` unless `ASTOCK_EM_FACTOR_CACHE_ONLY=1` |

**Not claimed:** full residual-code universe re-fetched in one sitting (120-code network proof + pipeline default force-refresh). Full residual force-refresh can continue under weekly cron.

---

## #8 S2 — new revision + manifest SHA

| Artifact | Value |
|----------|--------|
| Parent revision | `st_status_pit_v1` **preserved** |
| New revision | `st_status_pit_v1_1_20260724` |
| Pointer | `data/revisions/st_status_pit_CURRENT.json` |
| SoT sha | `839c9cabdf38365d012954d1d4e444ea49bb60c0ebe41cc28a67fc52f9571225` |
| Rows | 7025; unknown 5538; proven non-unknown labels present |
| Yearly unknown | `revisions/st_status_pit_v1_1_20260724/unknown_by_year.json` |
| Manifest | rebuilt; `st_status_pit` checksum **839c9cab…**, version **v1.1**, notes gate=OPEN |
| Policy | **no** unknown→normal |

**Not claimed:** S2 complete / unknown cleared.

---

## #9 S3 — open-session matrix + snapshot/tradability bind

| Artifact | Value |
|----------|--------|
| New revision | `price_limit_regime_pit_v1_1_20260724` (parent `price_limit_regime_pit_v1`) |
| SoT sha | `ca33a2d00bc1dca0d7a5564e028e8e2938e4706f3a572971d0cee9d3ca4e8098` |
| Mainboard IPO 2011+ | **1524 / 1524** |
| Matrix | n=58; **non_open_rejected=10**; **bad_ipo_on_closed=0** |
| Calendar proof | `open_session_index` via `ref_trading_calendar`; Saturday → `reason_code=not_open_session` |
| Manifest | checksum **ca33a2d0…**, version **v1.1** |
| Tradability bind | `bin/enrich_tradability_s2_s3_v1.py` + `revisions/tradability_enriched_s2s3_v1_1_20260724/` join contract |
| Snapshot plan | `data/revisions/snapshot_plan_s2s3_open_20260724.json` includes `st_status_pit` + `price_limit_regime_pit` |
| Weekly hook | rebuild S3 + enrich contract after price_limit |

**Not claimed:** full-population `tradability` table with ST/limit prices for every security-session (foundational `tradability_coverage` still separate per Quanthus LLD; enrichment is contract + matrix bind, gate OPEN).

---

## Commit feedback (honest)

### #5
> Added real Eastmoney force-refresh evidence (`network_hits=120`, `force_refresh=true`) and weekly path default force-refresh. Residual **4173** remains fail-closed; **C1 stays OPEN**.

### #8
> Published **new** revision `st_status_pit_v1_1_20260724` with parent pointer; did not treat overwrite of historical v1 as the only lineage. Manifest SHA updated to `839c9cab…`. Unknown remains high and quantified; **S2 stays OPEN**.

### #9
> IPO joint matrix now uses open sessions only (`non_open_rejected=10`, `bad_ipo_on_closed=0`). Snapshot plan + tradability enrichment contract bind S2/S3 datasets. Board IPO coverage 1524 complete as templates; **S3 consumer/acceptance stays OPEN**.

---

## Still blocked for close

1. User acceptance of residual 4173 as controlled completeness risk (correctness already split).
2. Additional ST proof sources to materially reduce unknown (archive ceiling ~500 proven codes class).
3. Full tradability enrichment over complete coverage matrix (DuckDB job) + D2 reconciliation green.
4. Optional: force-refresh **all** residual codes (not only 120) under weekly window.

**P1 total gate: still OPEN. No false green.**
