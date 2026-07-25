# P1-DATA-002 None-History Acceptance

- Decision: PASS for OCI canonical file and snapshot publication
- Date: 2026-07-25
- Issue: [#38](https://github.com/rv198-star/Quanthus/issues/38)
- Raw revision: `none_missing_276_raw_v1_20260725`
- OCI none revision: `none_history_276_oci_v1_20260725`
- Snapshot: `p1-s2-s3-none38-20260725-v1`

## Source handoff

Hermes reproduced the frozen S3 detection set exactly: 276 securities and 639,009 affected
execution rows. Baostock returned unadjusted daily history for 276/276 securities with no empty,
unsupported, or failed requests. The immutable handoff contained 915,146 raw rows in 276 shards;
all 291 exported files passed SHA-256 verification on OCI.

The root cause was an acquisition-universe defect. The old Hermes job was driven primarily by the
active stock list, which omitted 219 affected securities; 214 affected names contained ST or
delisting markers. It was not general provider unavailability.

## OCI normalization

OCI retained every existing parent `(security_id, date)` observation and inserted raw rows only for
absent keys. This avoids changing the established source for 68 securities that had partial history.

| Measure | Result |
| --- | ---: |
| Parent none rows | 13,802,529 |
| Raw candidate rows | 915,146 |
| Parent overlaps retained | 244,803 |
| Missing keys inserted | 670,343 |
| New none rows | 14,472,872 |
| Duplicate keys | 0 |
| Invalid inserted rows | 0 |

New none SHA-256:
`05ead429611c8726194ea2fbebb0559ee8ec224044fd1a963529e698c464aac4`.

## Downstream acceptance

- C2/S1 rerun: delisted `unexplained_missing=0`, `invalid=0`, terminal boundary=42.
- S2 rerun: 13,799,601 execution rows; status counts unchanged; PIT violations=0.
- S3 rerun: 13,799,601 execution rows; duplicate keys=0.
- Unexpected `prior_close_missing_fail_closed`: 0 rows and 0 securities.
- Exact `first_supported_trade_no_prior_close_fail_closed`: 72 rows and 72 securities; every case
  equals that security's first `tradestatus=1` date in the supported history and blocks entry.

Canonical child revisions:

- S2: `st_status_pit_v2_oci_v1_2_20260725`
- S3: `price_limit_execution_v2_oci_v1_2_20260725`
- S3 execution SHA-256:
  `832396e7e1f37c302739e815b7bf9faaf5b48942865116b3c8ba541dd08d4c80`
- Snapshot manifest SHA-256:
  `59285e6f9ee6e96909c84bccd9edb0d221e47651396a037323808c82ed9baf6b`

PostgreSQL publication is not part of this acceptance and remains governed by #37.
