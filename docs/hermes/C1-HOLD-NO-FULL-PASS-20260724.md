# C1 hold — correctness pass / completeness controlled gap — 2026-07-24

## Product decision (binding)

| axis | status | note |
|------|--------|------|
| Correctness (exact-unique + NOTICE link rule) | **PASS** | no fuzzy; no effective_date invention |
| Completeness residual | **4,173** fail-closed | **8.95%** of factor events class |
| C1 full pass | **NO** | do not rewrite residual as full pass |
| P1 total gate | **OPEN** | product must explicitly accept 8.95% historical adj-feature hole |

## Numbers (frozen)

| metric | value |
|--------|------:|
| residual exceptions | 4,173 |
| CA linked | 39,739 |
| EM linked | 2,737 |
| total factor rows | 46,649 |
| near-identity residual share | ~78% of residual |
| factors sha | `081b9df7a21e…` |
| exceptions sha | `1960a84d4637…` |

## Weekly path

- Default: real network `--force-refresh`
- Evidence example: `fetch_20260724-232305.json` (120 net hits)
- Residual may only shrink via exact-unique new NOTICE links; never invent

## Do not

- Close P1 solely because S1 residual zeroed or enrichment parquet exists
- Claim C1 full pass while 4173 ledger is non-empty
