# P3 Continuous Historical Frame Reader Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded contract, full-range input audit, and real round-trip smoke passed |
| Evidence date | 2026-07-29 |
| Scope | Lazy PIT market views, hidden T+1 execution sources, identity mapping, exception enforcement, replay-spec assembly, and CLI audit |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-REPLAY-001` continuous-corpus increment |
| Dependency evidence | `docs/acceptance/p3-historical-replay-bundle-gate-slice-20260728.md` |

## 1. Decision

The continuous frame-reader increment passes its synthetic contracts and the real immutable
snapshot `p1-historical-replay-20260729-v1`. It closes the remaining data-to-frame infrastructure
gap for one-unit daily strategy research. It accepts no concrete feature, label, expert, threshold,
position policy, or strategy, and it does not complete `P3-ACCEPT-001`.

The CLI audit entry is:

```bash
python -m quanthus audit-historical-replay-frames \
  --snapshot-dir <immutable-snapshot> \
  --scope-start <date> \
  --scope-end <date> \
  --output <report.json>
```

## 2. Accepted behavior

- The reader opens only a checksum-verified `HistoricalReplayBundle`, then validates required
  Parquet schemas, unique universe/execution/bar keys, execution-to-universe containment, and
  exchange identity agreement.
- Complete SSE/SZSE calendar rows define consecutive T-to-T+1 frames. Bars cannot invent a
  session, and the final decision date requires a later execution session in the same bundle.
- `PITBundleMarketView` exposes the current T daily universe and bounded per-security history.
  Strategy code receives that view but no T+1 session, action source, or future frame.
- The bundle `security_id` is an opaque economic identity. Normalized actions and ledger
  exceptions map source stock codes through the versioned validity table to exactly one identity.
- Daily-universe rows without execution evidence remain visible with
  `execution_evidence_missing`; they are non-executable and are counted by the audit.
- T+1 sessions and actions load lazily for only the selected or held security. The full market is
  not expanded into millions of Python frame objects.
- A normalized cash/bonus action applies before the open. Its theoretical mark is
  `(prior_mark - cash_per_share) / (1 + bonus_ratio)`, rounded to the price tick, and retains source
  evidence lineage.
- An exception interval blocks new execution for that security/date. Reaching such an interval
  while holding the security aborts replay; it cannot silently carry a known-wrong position.
- Reader version 1.1 also derives exact-date `unexplained_price_transition` blocks when a raw close
  is outside the resolved price-limit interval and no normalized action explains the discontinuity,
  or when the action-adjusted gross return still exceeds that day's effective limit percentage.
  The block prevents a structurally valid but economically inconsistent price from entering a
  simulated fill or held-position mark; it never infers a replacement action.
- `HistoricalReplayFrameReader.build_replay_spec` requires the run snapshot id to equal the bundle
  snapshot id. Every report retains `execution_authorized=false`.

## 3. Real full-range evidence

| Measure | Result |
| --- | ---: |
| Bundle digest | `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |
| Requested scope | 2012-01-01 through 2025-12-31 |
| Decision/execution boundary | 2012-01-04 through 2026-01-05 |
| Consecutive frames | 3,400 |
| Decision observations | 12,573,234 |
| Execution evidence rows | 12,574,837 |
| Missing execution-evidence rows | 1,259 |
| Distinct economic identities | 5,425 |
| Suspended evidence rows | 375,143 |
| Missing bars | 4,958 |
| Invalid bars | 0 |
| Effective non-executable observations | 538,522 |
| Unexplained price transitions | 13,465 |
| Normalized corporate actions | 35,157 |
| Security-date exception blocks | 164,375 |
| Audit digest | `sha256:cb5566b7c85c6c13150aa6376fdf38454af7b2350581846fa1ebdd2ca3b90357` |

The 1,259 missing execution-evidence rows in this scope belong to an identity that is present in
the daily universe but outside the price-limit execution source. The reader now preserves these
rows as explicit fail-closed observations. This acceptance does not treat their absence as
tradability or drop them from strategy-visible coverage.

## 4. Real execution-path smoke

A deterministic 2025-12-01 through 2025-12-10 research smoke used only T-close visibility, entered
economic identity `000001` for 100 shares at the next open, and requested full exit at the following
decision. It produced eight replay sessions, two signals, two orders, two fills, one closed trade,
no open position, and no pending exit. Entry was 2025-12-02 at CNY 11.69 and exit was 2025-12-03 at
CNY 11.63. With the accepted research costs, the result was CNY -16.59.

This is an integration oracle, not a baseline strategy or return claim. Its fixed identity and
dates must not be copied into a production rule.

## 5. Evidence map

| Concern | Evidence |
| --- | --- |
| Reader, views, lazy sources, audit report | `src/quanthus/backtest/historical_frames.py` |
| Strategy visibility and lazy frame contract | `src/quanthus/backtest/strategy_replay.py` |
| CLI audit entry | `src/quanthus/cli.py` |
| Synthetic Parquet and end-to-end cases | `tests/unit/test_historical_frame_reader.py` |
| Bundle completeness and checksums | `src/quanthus/backtest/historical_bundle.py` |

## 6. Verification

- Eight focused reader tests pass continuous calendars and universe dates, T-only visibility,
  execution-state mapping,
  alias identity, theoretical action marks, entry/held exception handling, audit counts, and a full
  lazy round trip.
- Focused Ruff and strict mypy pass for the reader, replay contracts, package exports, CLI, and
  tests.
- The full-range CLI audit and the real short-range execution-path smoke pass against the immutable
  bundle named above.
- Repository-wide `make check PYTHON=.venv/bin/python` passes doctor, compilation, all 342 tests,
  and the planning synchronization preview.
