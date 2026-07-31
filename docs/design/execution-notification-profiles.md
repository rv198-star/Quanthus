# Execution and Notification Profiles

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | not-applicable |
| Active profile | `close-to-next-open-v1` |
| Target profile | `operator-assisted-two-window-v1` |
| Target schedule | `two-window-0945-1015-1415-close-v1` clock bounds approved |
| Target activation | blocked by open activation blockers in `config/p2-gates.v1.json` |
| Automated broker execution | out-of-scope |
| Governing requirements | FR-BT-002/007, FR-AUDIT-005 |

## 1. Decision

The near-term product uses the same versioned execution semantics in historical backtest and
forward signal observation. The target operating profile has exactly two non-overlapping action
windows on each eligible market session. During forward operation the system may discover an
opportunity within a configured window, persist the decision, and notify the operator by email;
the operator decides whether and how to trade manually and then reports the actual action.

Email is a delivery side effect, not an order route. The repository does not connect to a broker,
place or cancel a live order, infer a fill from delivery, or treat a human action as known without
an explicit execution report. Any deployment involving real capital remains P7 live operation
even when every order is entered manually.

The frozen P0 clock remains the active executable policy. The target window boundaries are now
approved and covered by a synthetic contract fixture, but the target profile still lacks the
complete decision, matching, and live-input policy required for activation. This document does not
silently replace `config/p0-policy.v1.json`.

## 2. Profiles

| Profile | State | Decision and execution boundary | External side effect |
| --- | --- | --- | --- |
| `close-to-next-open-v1` | active | T-close PIT decision; first simulated execution at T+1 open | None required |
| `operator-assisted-two-window-v1` | target, activation blocked | `09:45-10:15` and `14:15-session close`; no decision or simulated fill may use information after its declared cutoff | Idempotent action-advice email and explicit operator report; no broker action |

The backtest engine is profile-aware rather than maintaining separate daily and intraday engines.
Every Signal, OperatorActionAdvice, OrderIntent, simulated Order, notification, operator report,
and run manifest carries the profile id and, for the target profile, a window id.

For the active daily profile, the decision snapshot remains market session T. Its persisted
`decision_at` may fall on T after close or on T+1 before the `09:30 Asia/Shanghai` open when the
exact canonical input is published overnight. A T+1 timestamp at or after the open is too late and
fails closed; it never changes the decision snapshot to T+1 or permits T+1 observations in the
decision view.

## 3. Window policy contract

The approved clock boundary decision is `two-window-0945-1015-1415-close-v1`:

| Window id | `Asia/Shanghai` interval | End boundary |
| --- | --- | --- |
| `window_1` | `09:45:00 <= t < 10:15:00` | `10:15:00` closes the morning action window |
| `window_2` | `14:15:00 <= t < session_close` | Authoritative session close, currently `15:00:00` |

Intervals are half-open. The end marks the point at which no new action may be initiated in that
window; in particular, a value first known at the `15:00:00` close cannot trigger a pre-close
decision or fill. A future exchange-calendar change resolves `session_close` from the versioned
session contract rather than changing matcher code.

Before target activation, one versioned executable configuration must additionally freeze:

- evaluation instants or cadence, decision cutoff, notification deadline, and signal expiry;
- allowed actions and whether an intent may carry into a later window or session;
- the first eligible simulated execution observation, fill-price reference, slippage, fees, and
  no-fill behavior for each action;
- required source datasets, interval, completeness gate, and maximum data latency;
- deterministic priority when multiple signals compete for finite capital within one window.

The clock bounds are approved; evaluation cadence and within-window fill semantics are not. These
remain policy parameters rather than constants to be chosen inside strategy, email, or matcher
code. A missing, overlapping, out-of-session, or partially evidenced window definition fails
closed.

## 4. Point-in-time and data boundary

Each evaluation receives a timezone-aware `decision_cutoff`. Features, market observations, and
cross-sectional values must be known no later than that cutoff. A later bar cannot be used to
decide a signal and then reused as its execution price.

The frozen data policy identifies official 5-minute history only from `2024-07-01`; one-minute
history from `2026-03-01` is archive-only. The current Hermes minute lane publishes after-session
archives and is not a live in-window market-data feed. Therefore:

- official two-window historical results may cover only accepted, complete intraday snapshots;
- daily OHLC cannot be used to infer within-window opportunity, order, or fill timing;
- results from a daily proxy, if one is ever approved, must be labeled separately and cannot be
  merged with official two-window performance;
- forward in-window email activation requires a separately accepted data source and latency SLA.

## 5. Simulation, notification, and manual action

The canonical real-capital rehearsal loop is:

```text
persisted decision -> emailed OperatorActionAdvice -> manual action
                   -> OperatorExecutionReport -> reconciliation -> actual ledger
```

Historical backtest and paper tracking may turn an OrderIntent into simulated Order/Fill/Ledger
events through the P2 matcher. Forward notification instead emits a NotificationEvent from the
persisted signal or intent. Email delivery never creates a `Fill` and never advances an actual
position ledger.

Notification records include notification id, signal id, execution profile and window ids,
content hash, provider, attempt, terminal status, timestamps, and a bounded error reason. Recipient
addresses and provider credentials remain protected runtime configuration. Retry reuses the same
logical notification identity so one signal cannot become multiple apparent opportunities. The
emailed advice includes stable advice/signal ids, profile/window, action, suggested size, decision
cutoff, validity boundary, reasons, and policy/snapshot versions.

The operator reports one of `not_executed`, `executed_as_advised`, `partially_executed`, or
`executed_modified`. An `OperatorExecutionReport` includes report/advice/signal ids, operator and
report time, zero or more actual fills with side, quantity, price, execution time and fees, an
evidence reference, and a deviation or no-action reason when applicable. One advice may reconcile
to multiple fills; real partial execution is retained even though the first P2 simulated matcher
does not model partial fills.

Accepted reports are append-only. A correction creates a new report with
`supersedes_report_id`; it never edits the historical notification or prior report in place. Only
an accepted report may update the actual ledger. The paper ledger continues independently so the
system can measure advice-to-action, simulated-to-actual fill, timing, price, fee, and skipped-action
differences without rewriting either history.

Until this report and reconciliation interface passes P7 acceptance, the repository reports only
simulated/paper results and notification outcomes, not actual portfolio performance.

## 6. Phase ownership

| Phase | Ownership |
| --- | --- |
| P2 | Profile-aware event time, simulated matching, ledger, manifest, and notification event contract |
| P3 | Governed signal and OrderIntent generation; email content from persisted decisions |
| P6 | Frozen paper operation and two-window notification rehearsal without live-capital claims |
| P7 | Advice-to-report live safety, actual-ledger reconciliation, and any later broker adapter |

## 7. Activation evidence

The target profile remains blocked until a non-synthetic executable configuration and tests prove
PIT cutoffs, data completeness, no-lookahead matching, idempotent notification,
manual-versus-simulated ledger separation, and deterministic rerun. Window count, non-overlap, and
the approved clock bounds are already contract-tested but are not sufficient for activation. No
email delivery result may be presented as execution evidence. P7 additionally proves no-action,
partial, modified, duplicate, and superseding operator reports against an advice-to-actual
reconciliation fixture.

`config/p2-gates.v1.json` owns the current activation blockers:

- `P2-INTRADAY-INPUT`: accepted complete intraday snapshots and a forward latency SLA;
- `P2-INTRADAY-MATCH`: non-synthetic cutoff cadence, fill reference, expiry, and no-lookahead
  golden cases;
- `P6-NOTIFICATION-TRANSPORT`: an idempotent provider integration that cannot imply fills or leak
  recipient data into audit events.

P2 conditional acceptance applies only to `close-to-next-open-v1` historical and paper research.
It does not close these blockers or automatically activate the target profile, target matching, or
forward email delivery.
