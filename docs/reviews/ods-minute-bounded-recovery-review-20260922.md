# C2 bounded minute recovery review — 2026-09-22

Work item: META-OPS-MINUTE-002 / #429; parent #424; stacked on C1 #428.
Governing design: `../design/lld/01-data-foundation.md`, opt-in same-attempt 1m section.
Decision: PASS_SCOPED_ENGINEERING / NO_GO_PRODUCTION.
Subsequent SSH/Linux qualification: `ods-linux-qualification-20260922.md` records
182 scoped passes, cgroup fault probes, incomplete full-suite runs and #431 runtime
drift. These additional results do not change the production decision above.
Review method: same assistant, separate design, adversarial diff review and regression
phases. This is not independent approval or production acceptance.

## Implemented boundary

- Explicit versioned 1m incremental profile only; no deployment env changes.
- Failed symbols only in bounded extra rounds; retain earlier error history after success.
- One cooperative monotonic deadline across endpoint selection, provider/page retries,
  pacing and rounds. HTTP/probe timeout bounded by remaining time; late responses rejected.
- Latest result counts do not count retries twice. Disk failures under the profile do not
  cause extra network work. Circuit and C1 terminal-checkpoint ordering remain enforced.
- Initial status persistence precedes provider I/O in all modes. The C1 test is updated
  to prove no session was opened on initial write failure, not to relax cleanup behavior.
- Policy id/hash, attempt count and round are auditable. No cross-attempt cache/reuse,
  no provider null/rc reclassification, no formal 5m or OCI topology changes.

## Evidence

Python 3.10 via uv; editable dev/p1/platform extras, pandas >=2.2,<3 and requests >=2.31,<3.

| Check | Observed result |
| --- | --- |
| New deterministic C2 fault/policy tests | 41 passed within expanded/full suites |
| Expanded provider/lane/checkpoint/process/schedule/documentation suite | 182 passed, 24.04 s |
| Full local pytest | 2756 passed / 90 failed / 7 skipped, 105.55 s |
| C1 baseline comparison | 2715 passed / 90 failed / 7 skipped; exact failed test-id sets equal, added/removed failures both empty |
| Ruff on changed Python paths | passed |
| Compile / doctor | passed separately |
| Final documentation contracts after SSH rule update | 5 passed |
| Literal make check | exit 69 before execution: local Xcode license not accepted; no license/settings changed |
| Planning preview | existing P3-STRAT-003-B-R1 governing LLD-row gap; not repaired under this work item |

The 4992-symbol synthetic case yields 4994 symbol attempts for two transient failures;
4990 symbols are called once and the two residuals twice. Provider calls and clock are
fake; scale-case JSON persistence is stubbed to avoid a disk benchmark. Separate smaller
fault cases use actual checkpoint files. No live market/provider requests were run.
HTTP retry count is not the same as the recorded symbol-attempt count.

Tests cover persistent failure, circuit before retry, deadline at endpoint/response/pacing/
round-delay boundaries, no budget reset, HTTP backoff and late responses, TDX page/probe/
reconnect handling, cancellation, disk failure, malformed policies, scope/duplicate universe
rejection, CLI load error, and lane seed/custom/timeout conflicts with 5m non-activation.

## Remaining risks and release gates

- Deadline is cooperative: an uncooperative in-flight provider call or disk operation may
  overrun it. Outer process timeout and Linux process-group/systemd cgroup tests remain gates.
- No cross-attempt identity/checksum admission, rc/null semantics fix, whole-controller
  budget unification, formal direct-5m resolution or ten-trading-day acceptance is claimed.
- Full local baseline is not green. Equal failure IDs detect no new failing test but do not
  waive the 90 existing failures or replace production-compatible Linux validation.
- Status/checkpoint remain two atomic replacements, not an fsync transaction. A caught
  cancellation records only completed symbols, not invented results for the current symbol.
- Policy file must remain the reviewed deployed file; compare lane and collector hash
  receipts during rollout. This feature does not implement an immutable config distributor.

## Owner SSH exception and read-only node evidence

During this batch the Owner explicitly instructed: do not use MCP, prefer passwordless SSH.
Root AGENTS.md and the programme/runbook record this task-scoped exception. Existing SSH
entries CN01, hermes and oci connected with BatchMode and StrictHostKeyChecking=yes.
No key, host-check setting, service configuration, checkout or package was changed.

At approximately 2026-09-22 19:07 Shanghai, CN01 and Hermes tracked production checkouts
at `b02fb3cc9fabefe014314aa9edf63914ea16b4c1`; tracked-file status was clean (untracked
files were not audited). CN01 daily orchestrator was activating/running; do not replace
its checkout or restart it. OCI sync reported systemd Result=success, exit 0, last exit
2026-09-22 08:25:40 UTC. That process receipt alone is not a package/date/checksum audit.
Old failed prune/weekly units were observed but not attributed as new incidents.

SSH management availability replaces the earlier MCP-channel blocker. Linux validation,
approved rollout, node/date/package evidence and observation gates remain open. OCI remains
the CN01 DWD mirror under Base V2.1 + Daily. No merge or production activation performed.
