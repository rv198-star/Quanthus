# P3 Historical Replay Bundle Gate Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded gate and real immutable replay bundle passed |
| Evidence date | 2026-07-29 |
| Scope | Immutable snapshot completeness, normalized-action lineage, exception-ledger reconciliation, path safety, and partition checksums |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-REPLAY-001` continuous-corpus boundary |
| Dependency evidence | `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |

## 1. Decision

The continuous historical replay-bundle gate passes its bounded synthetic acceptance cases and the
real snapshot `p1-historical-replay-20260729-v1`. It provides one explicit precondition before any
future frame reader may query market rows.

The older snapshot `p1-s2-s3-dwd-20260725-v1` still fails because it lacks
`corporate_action_exceptions`, `corporate_actions_normalized`, and `trading_calendar`. The loader
does not borrow an older calendar or read mutable corporate-action evidence directly. The new
snapshot supplies all required datasets with matching lineage and checksums. This accepts the real
input bundle but does not implement a continuous frame reader.

## 2. Accepted behavior

- One format-version-2 `PublishedSnapshot` is the only bundle identity.
- The minimum execution corpus contains `daily_ohlcv_none`, `daily_universe`,
  `security_code_validity`, `price_limit_execution_daily`, `trading_calendar`,
  `corporate_actions_normalized`, and `corporate_action_exceptions` in that same snapshot.
- Every partition path remains inside the snapshot directory, and every materialized partition
  matches its manifest SHA-256 before the bundle is returned.
- The normalized-action manifest binds the exact policy, action source, authority source, audit
  report, exception-ledger version and digest, and declared exception count.
- The exception manifest must carry the same lineage, declare
  `replay_disposition=security_fail_closed`, and have exactly the declared number of rows. Both zero
  and non-zero ledgers are valid only when this reconciliation holds.
- Missing datasets, policy drift, lineage drift, exception-count mismatch, content tampering, and
  path traversal all fail before frame construction.
- A valid bundle has a deterministic digest and `execution_authorized=false`; it grants input
  readiness only.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| Bundle contract and loader | `src/quanthus/backtest/historical_bundle.py` |
| Shared materialized-snapshot verification | `src/quanthus/snapshot_publish.py` |
| Complete, incomplete, policy-drift, ledger, tamper, and traversal cases | `tests/unit/test_historical_replay_bundle.py` |
| Real authority and 272-row ledger evidence | `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |
| Atomic corporate-action revision and replay-snapshot publisher | `src/quanthus/corporate_action_revision.py`, `src/quanthus/historical_replay_snapshot.py` |
| Design/implementation traceability | `tests/contract/test_document_traceability.py` |

## 4. Real bundle evidence

The new snapshot publishes 35,157 normalized rows and the same-version 272-row exception ledger
together with the authoritative calendar and accepted daily execution datasets. Its deterministic
bundle digest is
`sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707`.
The loader reports `ready_for_frame_construction=true` and `execution_authorized=false`.

The residual work is the continuous frame reader. It must apply the ledger by security and market
date before producing a strategy frame. Concrete strategy decisions and multi-year acceptance
remain separate P3 work.

## 5. Verification

- Focused normalization, corpus, and bundle-gate suite passes authority precedence, notice PIT,
  exact-candidate ranking, source-precision boundaries, profile conflicts, ledger blocking,
  lineage reconciliation, policy drift, tamper, and traversal cases.
- Focused Ruff and strict mypy pass.
- Full repository verification is recorded after the implementation/doc synchronization audit.
- The older snapshot remains rejected on the three documented missing datasets, while
  `p1-historical-replay-20260729-v1` passes without cross-snapshot composition.
