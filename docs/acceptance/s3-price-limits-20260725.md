# S3 Price-Limit Regime Acceptance

- Decision: PASS for canonical OCI file and snapshot publication
- Date: 2026-07-25
- Issue: [#9](https://github.com/rv198-star/Quanthus/issues/9)
- Revision: `price_limit_execution_v2_oci_20260725`
- Snapshot: `p1-s2-s3-20260725-v1`

## Results

| Check | Result |
| --- | ---: |
| Versioned rule rows | 2,750 |
| Execution-day rows | 13,799,601 |
| Securities | 5,465 |
| Date range | 2011-01-04 to 2026-07-23 |
| Entry-policy allowed / fail-closed | 13,109,683 / 689,918 |
| Duplicate execution keys | 0 |
| IPO window overruns | 0 |
| Unknown ST state allowing entry | 0 |
| Invalid upper/lower ordering | 0 |
| Non-session output rows | 0 |

Reason distribution:

| Reason | Rows |
| --- | ---: |
| price limit resolved | 12,427,037 |
| prior close missing, fail-closed | 639,009 |
| session not tradable | 402,069 |
| ST overlay applied | 320,734 |
| ST unknown, fail-closed | 5,391 |
| IPO no-limit session | 5,322 |
| trade status unknown, fail-closed | 39 |

The 639,009 missing-reference rows belong to 276 securities with no normalized `none` history:
265 are currently marked listed and 11 delisted. S3 did not lose their prior closes. They are
retained as an explicit upstream coverage boundary, cannot generate an entry, and are tracked as
a separate acquisition defect. The 39 missing trade-status rows match terminal-boundary dates.

## Golden coverage

The versioned golden fixture contains 22 cases covering both main boards, the ChiNext 2020 switch,
STAR, main/ChiNext/STAR ST overlays, unknown status, no-limit IPO sessions 1/5/6, legacy IPO,
44%-rule issue-price failure, a suspension inside an IPO window, non-open-session rejection,
missing prior close, and missing trade status. A separate build fixture proves a suspended session
does not increment `eligible_session_index`.

## Evidence

- execution SHA-256: `5359232be7ac90061204ddf2b13bea5546e4412d028802ac6310d92ba7b586d9`
- rule SHA-256: `2d4990cd7a967adb5ef4a56ff30a169c1296034c940375cea864ad995c747f40`
- manifest SHA-256: `2c7a3723259fd0eeb43ff556c076ed300d67b9c242de41b9700fbe68d7518ef7`
- snapshot SHA-256: `f1d23f44842e90867067fe79b0f890ab774841c3fb385ca271ef55db54706604`

Canonical artifacts remain outside Git under `/home/ubuntu/data/quanthus/revisions/` and the
snapshot catalog. PostgreSQL serving publication remains tracked by #37.
