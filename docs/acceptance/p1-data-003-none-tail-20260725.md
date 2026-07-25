# P1-DATA-003 Active None-Tail Acceptance

- Decision: PASS
- Date: 2026-07-25
- Issue: [#40](https://github.com/rv198-star/Quanthus/issues/40)
- Hermes raw revision: `none_tail_fix3_raw_v1_20260725`
- OCI child revision: `daily_ohlcv_none_dwd_v2_oci_20260725`

## Root cause and evidence

The original Hermes checkpoint prematurely marked `000919`, `600805`, and `600810` complete at
exactly 2,000 rows on 2019-03-26. Checksummed Baostock `adjustflag=3` evidence proved that the
provider still returns the full series. For each security, the immutable handoff contains a
2,000-row prefix, a 1,778-row tail, and a 3,778-row full-window control. OCI verified that prefix
plus tail equals the full-window response exactly. All 19 files in the raw checksum ledger passed.

## OCI child

OCI retained every parent `(security_id, date)` key and inserted only absent tail keys.

| Measure | Result |
| --- | ---: |
| Parent rows | 14,472,872 |
| Raw tail rows | 5,334 |
| Parent overlaps | 0 |
| Missing keys inserted | 5,334 |
| Child rows | 14,478,206 |
| Duplicate keys | 0 |
| Invalid inserted rows | 0 |

Each repaired security now has 3,778 rows from 2011-01-04 through 2026-07-24. Every provider
observed tail key is present, and `tradestatus=1` without a child bar is zero. No qfq value was
converted, no parent observation was replaced, and PostgreSQL was not written.

The child daily checksum is
`d18d21478a6b07ff071dc2e8db90fbf7e444724fd38e8e0c55e61ebed800a608`.
`daily_ohlcv_none_CURRENT.json` was atomically advanced to the child after validation.

One inherited structurally invalid row exists at `600472/2007-12-26`; it is unchanged from the
parent and predates the P1 supported start in 2011. P1-range invalid OHLC remains zero.

