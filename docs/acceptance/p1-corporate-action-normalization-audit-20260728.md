# P1 Corporate-Action Normalization Audit Record

| Field | Value |
| --- | --- |
| Evidence status | authoritative normalization, immutable revision, and replay-bundle publication passed |
| Evidence date | 2026-07-29 |
| Scope | Implemented corporate actions with explicit 2012-01-01 through 2025-12-31 effective dates, plus whole-source disposition |
| Governing design | `docs/design/lld/01-data-foundation.md` |
| Governing work | `P1-DATA-004` |
| Policy | `config/p1-corporate-action-normalization.v2.json` |
| P1 snapshot | `p1-s2-s3-dwd-20260725-v1` |
| Replay snapshot | `p1-historical-replay-20260729-v1` |

## 1. Decision

The v2 corpus audit passes its bounded contract. It prefers an exact Eastmoney implementation plan
with a pre-effective-date notice, uses the adjustment-factor ratio only as fallback or
corroboration, preserves every source disposition, and grants no execution authority.

The former 598 blockers are not silently discarded. Authoritative evidence resolves 340 of them;
258 remain conservative exceptions. Applying the same authority checks to previously factor-passed
rows exposes 14 additional material source conflicts, so the final ledger contains 272 rows. Every row is
versioned with its source evidence, blocking date, and `security_fail_closed` replay disposition.

The audit inputs were materialized into immutable revision
`corporate_action_replay_2012_2025_v1`. Snapshot `p1-historical-replay-20260729-v1` publishes its
normalized actions and same-version exception ledger together with the authoritative calendar and
accepted daily execution datasets. The real historical-bundle loader accepts it for frame
construction and continues to report `execution_authorized=false`.

## 2. Frozen behavior

- Authority order is Eastmoney implementation plan, then factor-ratio fallback.
- Authority matching is exact on `(security_code, effective_date)`. `NOTICE_DATE` must precede the
  effective date, `ASSIGN_PROGRESS` must be `实施分配`, and `IMPL_PLAN_PROFILE` must agree with the
  structured cash, bonus, and transfer fields.
- Eastmoney cash, bonus, and transfer values are per ten shares. The source action may encode cash
  per share or per ten shares; the uniquely best source-precision match selects that source unit.
- Binary-float noise is canonicalized at `1e-12`. Non-zero source values first use their displayed
  precision interval, with a `1e-5` floor for integer values; exact matches outrank bounded
  rounding/truncation matches. The final business tolerances are `0.00001` yuan per share for cash
  and `0.0001` shares per ten shares for bonus/transfer quantities. A tied best candidate fails
  closed.
- When authority resolves an action, missing or mismatching factor evidence remains explicit
  `unavailable` or `mismatch` audit evidence and does not override the implementation notice.
- Without exact authority, v1 factor-candidate limits remain frozen: maximum absolute error
  `0.005`, minimum candidate separation `0.00001`, and prior PIT factor before qfq/raw baseline.
- A dated exception blocks that security from its effective date. A missing-effective-date
  exception blocks it from its announcement date, open-ended until a later authoritative revision.
- Every report and normalized row set declares `execution_authorized=false`.

## 3. Corpus evidence

Source identity:

- Corporate-action SHA-256:
  `sha256:ac24885183ad3251c3b0438c2f2c3d627fc5234ed9f1308b258c20d2953e93df`
- Eastmoney authority revision: `adj_factors_pit_em_link_v1`
- Scoped Eastmoney authority SHA-256:
  `sha256:73c1c2895da478390019c5ac9683428fea03a238e983bf41629f0411fe927e2b`
- Audit report SHA-256:
  `sha256:757bc9c029e5f498a8ab6220e7e696f579779a7ae515029dffbfac26cc53238b`
- Exception-ledger SHA-256:
  `sha256:bc9d866ce65c4aa9afd23d5a030bdbb4dee016f0a920971e4568cd8d9a087e3d`

The adapter recomputed and matched the snapshot manifest checksums before querying:

| Dataset | SHA-256 |
| --- | --- |
| `adjustment_factors` | `sha256:081b9df7a21e942122b10a4affa04b9fc056ab8d4fbc8b4e38b3ded7e71e9be9` |
| `daily_ohlcv_none` | `sha256:05ead429611c8726194ea2fbebb0559ee8ec224044fd1a963529e698c464aac4` |
| `daily_ohlcv_qfq` | `sha256:703e1caf17aa35eb35e6f4beaf654e29f93a1717899de5473a9c332b109e8391` |

Whole-source disposition reconciles exactly:

| Disposition | Rows |
| --- | ---: |
| Source rows | 40,594 |
| Not implemented (`progress != 实施`) | 488 |
| Implemented but dated outside the explicit audit scope | 4,677 |
| Implemented with missing effective date | 34 |
| Implemented, dated, and inside scope | 35,395 |

In-scope dated rows and the whole exception ledger also reconcile:

| Disposition | Rows |
| --- | ---: |
| Normalized | 35,157 |
| Missing factor cross-check with no exact authority | 191 |
| Factor-ratio mismatch with no exact authority | 31 |
| Material authority/source numeric conflict | 6 |
| Authority notice not PIT-visible | 5 |
| Authority profile/structured-field conflict | 5 |
| Dated in-scope total | 35,395 |
| Missing effective date | 34 |
| Versioned exception-ledger total | 272 |

Of the normalized rows, 31,937 use an Eastmoney implementation plan and 3,220 use factor-ratio
fallback. Factor corroboration is `matched` for 34,809 rows, `mismatch` for 216 authority-resolved
rows, and `unavailable` for 132 authority-resolved rows. These mismatches remain visible warnings;
they are not unit judges. Cash-unit lineage records 18,421 per-share, 16,078 per-ten-share, and 658
zero-cash source selections.

## 4. Evidence map

| Concern | Evidence |
| --- | --- |
| Frozen v1 fallback and v2 authority policies | `config/p1-corporate-action-normalization.v1.json`, `config/p1-corporate-action-normalization.v2.json` |
| Pure normalization, authority lineage, and factor warnings | `src/quanthus/corporate_action_normalization.py` |
| Snapshot-bound adapter, raw-authority verification, and exception ledger | `src/quanthus/corporate_action_corpus.py` |
| Repeatable read-only command | `quanthus audit-corporate-actions` in `src/quanthus/cli.py` |
| Atomic Parquet revision, prior-ledger comparison, verification, and pointer promotion | `src/quanthus/corporate_action_revision.py`, `tests/unit/test_corporate_action_revision.py` |
| Complete replay-snapshot publication and immediate bundle gate | `src/quanthus/historical_replay_snapshot.py` |
| Synthetic boundary cases | `tests/unit/test_corporate_action_normalization.py`, `tests/unit/test_corporate_action_corpus.py` |
| Design/implementation traceability | `tests/contract/test_document_traceability.py` |
| Downstream immutable-bundle gate | `src/quanthus/backtest/historical_bundle.py`, `tests/unit/test_historical_replay_bundle.py` |

## 5. Publication result

The published normalized Parquet contains 35,157 rows with SHA-256
`sha256:68e5265d1db2ccd3c0226bf70c2ef8004ec6c8e82cc40d264841317a974050d4`.
The exception Parquet contains 272 rows with SHA-256
`sha256:451422aeaa8a0393bc79c6814d6aab1c054a599f4e8914d717f14fd8592e34f0`.
Both snapshot manifests bind the policy, report, action source, authority source, ledger version,
ledger digest, and exact row reconciliation.

The complete snapshot contains all seven required replay datasets and has bundle digest
`sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707`.
It is ready for frame construction, not execution.

Future source revisions are deterministic script work. The builder performs a bounded full rebuild
and compares stable exception ids with the verified prior revision, producing new, persistent, and
resolved counts. Known exception reasons remain automatically versioned. Human or AI review is
reserved for schema/source-semantic drift, unknown reason codes, failed integrity gates, backward
cutoffs, or abnormal exception-volume changes. Weekly scheduler wiring remains an operational
follow-up.

A future continuous frame reader must enforce the ledger per security and date. It may not turn a
blocked or missing action into a sell signal, silently exclude the row, or borrow evidence from a
different snapshot. This record does not alter P1's broader conditional gate or claim completion of
the `P3-REPLAY-001` multi-year acceptance run.

The recorded corpus was rerun through the repository CLI. It reproduced the report and ledger
digests, materialized the declared Parquet counts, promoted a verified CURRENT pointer, published
the immutable snapshot through the pre-publication bundle gate, and loaded it again through
`load_historical_replay_bundle`.
