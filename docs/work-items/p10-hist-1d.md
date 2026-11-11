# P10-DATA-HIST-1D-001: independent historical daily product

Issue: [#602](https://github.com/rv198-star/Quanthus/issues/602). Parent: permanent P10 #478.
Contract: `config/pipelines/hist-1d-v1.json`.

## Objective and scope

Publish Quanthus Hist-1D v1 for cycle research: SSE/SZSE A-share ordinary-equity daily bars
from 1990-12-19 through 2019-12-31, including period delistings. The frozen Base 2.5.1
listing/lifecycle snapshot defines the universe. Research grade starts in 2005; earlier
data is reference grade. 2020–2021 is an audit overlap, separate from published bars.

The product uses source lexical Decimal values, UTC timestamps and the DWD schemas.
Retrospective observations use their recorded acquisition clock. Its use grade is
“周期研究级，非历史 PIT 认证”. The existing sealed Base and production Daily remain separate.

## Current recovery checkpoint — 2026-11-11

CN02 workspace: `/srv/quanthus-base22-data/work/hist-1d-602-20261111`.
Versioned implementation: `quanthus.pipelines.hist_1d`, `hist_1d_collect`,
`hist_1d_materialize` and `hist_1d_audit`.

- Recovered four byte-verified Base 2.3 cloud archives and 1,238 selected members.
  Archive daily history was sparse before 2011; 2005 initially contained 200 listings.
- Froze 2,110 bounded BaoStock tasks. 4,220 accepted captures contain 4,481,083 source
  rows; every accepted source byte digest and request identity was verified, with no
  empty captures. Four authentication failures remain preserved alongside separately
  bound successful session-recovery captures.
- Full candidate contains 10,569,845 daily bars and 362 Parquet files, totaling
  360,174,821 bytes. Existing archive business keys are retained; supplements fill
  missing keys. Source-evidenced suspended sessions generate states without new bars.
- Final audit has zero unexplained listing/session gaps in both quality tiers, zero
  duplicate bar keys and zero source/universe differences. All 112 listings delisted
  within the publication interval have bars. The audit includes 142,826 additionally
  evidenced suspended listing/sessions.
- 2020–2021 overlap has two legacy-volume differences: 002336 and 002500 on 2020-06-17.
  Their legacy nonzero volume versus sealed Base 2.5.1 zero volume is retained in a
  machine-readable ledger. This overlap is audit-only.
- Native-factor overlap conflicts are zero. An independent 2010 sample for 600601 has
  231 comparison days, equal unadjusted closes and maximum common-anchor adjusted-close
  difference of 0.000253061 CNY, below one 0.01-CNY quote tick.

## Status capability and remaining acceptance

`PROVIDER_DAILY_STATE` retains BaoStock's binary ST/non-ST observations. The official
SZSE short-name history supplies 7,503 change records from 1994 onward; corroborated
ST and *ST names populate `SPECIAL_TREATMENT` as ST and DELISTING_RISK. Source conflicts
and unresolved subtypes remain UNKNOWN with reason codes. Reference-period regime
history is separately graded.

The existing relay's historical `namechange` endpoint is at its daily quota. Bounded
official SSE probes found a historical-announcement candidate, while 2001 test windows
returned no records. The Owner has been asked whether binary ST is sufficient for this
cycle-research delivery or whether full subtype coverage remains a P1 completion gate.
Until resolved, product readiness remains candidate-level. P2 valuation and additional
index work is optional and deferred.

## Recovery and verification commands

Use the pinned repository revision recorded in each runtime receipt. The acquisition
plan and accepted raw captures are immutable; session recovery creates new source
members with explicit supersession of authentication failures.

```bash
python -m quanthus.pipelines.hist_1d_collect --workspace WORKSPACE --base251 SEALED_DWD
python -m quanthus.pipelines.hist_1d_materialize --workspace WORKSPACE --base251 SEALED_DWD --output NEW_PRODUCT
python -m quanthus.pipelines.hist_1d_audit --workspace WORKSPACE --base251 SEALED_DWD --hist-root NEW_PRODUCT --output NEW_AUDIT
python scripts/read_hist_1d.py --revision FIXED_DATASET_REVISION
```

Runtime receipts own exact code and data identities, timings, quality ledgers, manifest
hashes, publication revisions and backup readback. Source data, credentials and runtime
outputs stay outside Git. P10 source delivery and research-consumer admission retain
their separate acceptance responsibilities.
