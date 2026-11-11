# P10-DATA-HIST-1D-001: independent historical daily product

Issue: [#602](https://github.com/rv198-star/Quanthus/issues/602). Parent: permanent P10 #478.
Contract: `config/pipelines/hist-1d-v1.json`.

## Owner acceptance scope — 2026-11-11

The Owner explicitly selected `hist1d-cycle-research-binary-st-v1`: binary ST is
required for this cycle-research delivery; ST/*ST/delisting-risk subtype gaps are
tracked as follow-up [#606](https://github.com/rv198-star/Quanthus/issues/606).
Unknown subtype observations retain their reason codes and source lineage.

The final binary-coverage check found inherited 2011–2019 gaps even after mapping
explicit NORMAL/ST/DELISTING_RISK classes: 5,463,062 listing/sessions across 3,610
securities. The Base 2.3 and 2.5.1 status files are byte-identical. Bounded completion
therefore uses the already-qualified BaoStock endpoint with only `date,code,isST`,
then fills the frozen missing intervals in a separate release. Existing bar/factor
members are reused. The final READY gate requires complete, nonconflicting binary
coverage of the research-tier listing/session denominator.

`hist_1d_finalize` binds the Owner profile, preserves the subtype debt ledger and
seals the full member manifest. The publication helper uploads a fixed revision,
publishes the dataset, and supports a small release overlay over the previously
verified candidate backup, including the additional raw status captures.

## Objective and scope

Publish Quanthus Hist-1D v1 for cycle research: SSE/SZSE A-share ordinary-equity daily bars
from 1990-12-19 through 2019-12-31, including period delistings. The frozen Base 2.5.1
listing/lifecycle snapshot defines the universe. Research grade starts in 2005; earlier
data is reference grade. 2020–2021 is an audit overlap, separate from published bars.

The product uses source lexical Decimal values, UTC timestamps and the DWD schemas.
Retrospective observations use their recorded acquisition clock. Its use grade is
“周期研究级，非历史 PIT 认证”. The existing sealed Base and production Daily remain separate.

## Pre-publication candidate checkpoint — 2026-11-11

CN02 workspace: `/srv/quanthus-base22-data/work/hist-1d-602-20261111`.
Versioned implementation: `quanthus.pipelines.hist_1d`, `hist_1d_collect`,
`hist_1d_materialize`, `hist_1d_factor_completion`, `hist_1d_audit` and `hist_1d_release`.

- Recovered four byte-verified Base 2.3 cloud archives and 1,238 selected members.
  Archive daily history was sparse before 2011; 2005 initially contained 200 listings.
- Froze 2,110 bounded BaoStock tasks. 4,220 accepted captures contain 4,481,083 source
  rows; every accepted source byte digest and request identity was verified, with no
  empty captures. Four authentication failures remain preserved alongside separately
  bound successful session-recovery captures.
- Final candidate contains 10,569,845 daily bars, 13 tables and 365 Parquet files
  (822,376,160 bytes). Its 397 manifested members total 827,035,626 bytes. Supplements
  fill missing archive keys; source-evidenced suspensions generate states without bars.
- A bounded 144-query completion added 830 native-factor observations. A source code
  returning predecessor history outside its frozen listing lifetime is excluded:
  601607 has 21 such observations before 2010-03-05, all retained in an exclusion ledger.
  The final 35,314 factors have no duplicate effective events, broken intervals,
  nonpositive values or basis mismatches. Every bar-bearing security has a factor series.
  000005's first 18 reference-grade bars precede its first observed factor and remain
  explicitly unadjustable; cross-reorganization returns retain their listing boundary.
- Exactly 567 legacy midnight bar coordinates are mapped through their existing
  calendar-session foreign key under `hist1d-session-clock-v1`. OHLCV, turnover and
  source fields are byte-value equivalent; the original source bytes remain retained.
- Trading-venue, listed-company and frozen tradability references are included.
  The old tradability snapshot's missing-observation reasons overlap 3,646,988 Hist bars;
  this reference-version reconciliation is separate from current coverage.
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
- Candidate: `release-candidate-a2`, built from `product-full-a3`. Full manifest SHA256:
  `60062d17b9eeaf48ce455014ff898fb04d4a99e5723a87fb51d26c0736d04a92`.
- Private HF candidate: `rv192/quanthus-hist-1d` at
  `75b0eab6650bc957e6cb0519cef3a3ca9698b544`; all 397 member identities match the
  manifest (372 LFS SHA256 content IDs and 25 full small-file downloads). READY and
  public anonymous-reader acceptance await the status-subtype disposition.

## Status capability and remaining acceptance

`PROVIDER_DAILY_STATE` retains BaoStock's binary ST/non-ST observations. The official
SZSE short-name history supplies 7,503 change records from 1994 onward; corroborated
ST and *ST names populate `SPECIAL_TREATMENT` as ST and DELISTING_RISK. Source conflicts
and unresolved subtypes remain UNKNOWN with reason codes. Reference-period regime
history is separately graded.
In 2005–2010, 215 distinct securities retain an UNKNOWN subtype: 186 unresolved and
29 official-name/provider-class disagreements. Binary provider observations are kept.

The relay's historical `namechange` endpoint reached its daily quota. Bounded official
SSE probes and their limitations remain recorded. The Owner's binary-ST scope above
supersedes the prior subtype readiness hold. P2 valuation and additional index work
remain optional and deferred.

## Recovery and verification commands

Use the pinned repository revision recorded in each runtime receipt. The acquisition
plan and accepted raw captures are immutable; session recovery creates new source
members with explicit supersession of authentication failures.

```bash
python -m quanthus.pipelines.hist_1d_collect --workspace WORKSPACE --base251 SEALED_DWD
python -m quanthus.pipelines.hist_1d_materialize --workspace WORKSPACE --base251 SEALED_DWD --output NEW_PRODUCT
python -m quanthus.pipelines.hist_1d_factor_completion --workspace WORKSPACE --base251 SEALED_DWD --product NEW_PRODUCT --output COMPLETED_PRODUCT
python -m quanthus.pipelines.hist_1d_audit --workspace WORKSPACE --base251 SEALED_DWD --hist-root COMPLETED_PRODUCT --output NEW_AUDIT
python -m quanthus.pipelines.hist_1d_release --workspace WORKSPACE --base251 SEALED_DWD --product COMPLETED_PRODUCT --audit NEW_AUDIT --output NEW_CANDIDATE
python scripts/read_hist_1d.py --revision FIXED_DATASET_REVISION
```

Runtime receipts own exact code and data identities, timings, quality ledgers, manifest
hashes, publication revisions and backup readback. Source data, credentials and runtime
outputs stay outside Git. P10 source delivery and research-consumer admission retain
their separate acceptance responsibilities.
