# P2 Audit, Notification, and Window-Boundary Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Cases | `P2-AUDIT-NOTIFICATION-001`, `P2-WINDOW-CUTOFF-EXPIRY-001`, `P2-MAXIMUM-HOLD-SELL-001` |
| Scope | Immutable audit publication, no-implied-fill notification, synthetic window boundaries, and upstream exit execution |
| Governing design | `docs/design/lld/02-backtest-engine.md`, `docs/design/lld/10-monitoring-attribution.md` |
| Governing work | `P2-AUDIT-001`, `P2-OBS-001`, `P2-ENG-001`, `P2-ORDER-001`, `P2-ACCEPT-001` |

## 1. Audit and notification decision

One completed run publishes through an atomic directory rename. `manifest.json` binds the original
run manifest, ordered event count, whole-stream checksum, and last event checksum. Every JSONL
event carries its own checksum and the prior event checksum. Replaying byte-equivalent contents is
idempotent; reusing the run id with changed contents or loading changed evidence fails closed.

A notification is accepted only after its run and one matching Signal are persisted. Profile and
window ids must match that Signal. The synthetic case appends a failed attempt followed by a
delivered retry under one notification id. Attempts cannot be skipped, replaced, or re-identified.
Appending both notification outcomes leaves the original one-event run byte-for-byte equivalent
and its Fill count at zero.

## 2. Synthetic window decision

The fixture policy retains the approved half-open clock bounds and adds test-only instants:

| Window | Decision cutoff | First eligible | Expiry |
| --- | --- | --- | --- |
| `window_1` | `09:55` | `10:00` | `10:15` |
| `window_2` | `14:30` | `14:35` | `15:00` |

For `window_1`, a snapshot known at `09:55` may produce a persisted signal at `09:56`. Its intent
is pending at `09:59:59`, eligible from `10:00` through `10:14:59`, and expired at `10:15`.
Knowledge first available at `09:55:01` fails PIT validation. `window_2` is expired at session
close, so close data cannot trigger a pre-close intent. Intent construction emits no Fill and the
engine still rejects two-window matching as unsupported.

These instants are synthetic acceptance inputs. They do not freeze target evaluation cadence,
authorize an email sender, establish an intraday data SLA, or activate
`operator-assisted-two-window-v1`.

## 3. Upstream maximum-hold decision

The maximum-hold case supplies a complete sell Signal whose reason is `maximum_hold_exit`. P2
preserves that upstream reason, creates one generic sell order, fills at CNY 10.19, and realizes
CNY 12.48 under the same synthetic accounting oracle as the carried-sell case. No holding-period
counter or exit threshold was added to P2.

## 4. Evidence map

| Concern | Evidence |
| --- | --- |
| Run and notification append-only persistence | `src/quanthus/backtest/audit.py` |
| Notification and event contracts | `src/quanthus/backtest/contracts.py` |
| Synthetic cutoff/eligibility/expiry policy | `src/quanthus/backtest/policy.py`, `src/quanthus/backtest/window.py` |
| Golden oracles | `tests/fixtures/golden/p2_audit_notification.v1.json`, `tests/fixtures/golden/p2_window_cutoff_expiry.v1.json`, `tests/fixtures/golden/p2_maximum_hold_sell.v1.json` |
| Behavioral verification | `tests/unit/test_backtest_audit.py`, `tests/unit/test_backtest_window.py`, `tests/unit/test_backtest_engine.py` |

## 5. Residual boundary

P2 still lacks a P1-backed multi-session replay orchestrator, automatic corporate-action loading,
complete accepted/rejected decision records, the diagnostic hypothetical ledger, production fee
and intraday matching policy, and a full phase conformance audit. Email transport and manual-action
reconciliation remain P6/P7 work and are not implied by this storage contract.

## 6. Verification

- Focused P2 policy, engine, metric, audit, and window suites: passed 42 tests.
- Ruff, formatting, and mypy on the P2 package and focused tests: passed.
- Full repository check with the repository virtual environment: passed 162 tests.
