# META-DATA-PIPE-001 OCI Rehearsal 2026-07-25

- Issue: [#39](https://github.com/rv198-star/Quanthus/issues/39)
- Decision: OPEN; Hermes supplement required
- Pipeline: `t-close-plus-5h-v1`
- Code revision rehearsed: `68107bf32596a13ae756e452c878b7e7a4e5e7a8`

## Passed evidence

OCI pulled the open daily, closed daily, and weekly releases through the read-only rsync channel.
All three READY markers, manifests, artifact sizes, and SHA-256 values passed
`quanthus verify-ods-release`.

The 2026-06-19 closed-weekday release completed the full pipeline as
`skipped_closed_session`. No build or publication stage ran and no `daily_market_CURRENT` pointer
was created in the isolated warehouse.

## Blocking findings

The 2026-07-24 open release passed pull, manifest verification, and calendar verification, then
failed closed in `build_daily_dwd_partition`. Its bar delta omits the required `turnover_rate` and
`pct_change` fields. Publication was not invoked and no current pointer moved.

The open release was assembled on 2026-07-25 from local Hermes files under acquisition revision
`local-none-status-supplement-20260724-v1`. It validates transport mechanics but does not prove
that a real T-day source acquisition can meet the T+5h deadline.

The weekly release contains four current-window extracts. Its shareholder artifact has a valid
90-day disclosure range and 13,679 rows, but every row merely carries
`revision_retained=true`; there are no repeated `(code, end_date)` versions or separately exported
revision-history rows. The release also omits the adjustment-evidence artifact required by the
issue contract. It therefore does not yet prove correction-history retention.

## Required Hermes supplement

1. Publish a new immutable open-session release whose bar delta contains the complete DWD schema,
   including nullable `turnover_rate` and `pct_change` columns.
2. Use a real post-close T-day provider acquisition and record its actual start, finish,
   source revision, and knowledge cutoff. Local historical files may be fallback evidence but may
   not be represented as a successful live acquisition.
3. Export actual shareholder correction history or a versioned before/after ledger, not a constant
   assertion column.
4. Include adjustment evidence in the weekly manifest, or explicitly amend the weekly contract
   before OCI acceptance.
5. Post a new `READY_FOR_OCI` comment. OCI will rerun the open, closed, weekly, and changed-lineage
   rehearsals before enabling the timer.

