# Minute terminal checkpoint review — C1

| Field | Value |
| --- | --- |
| Scope | META-OPS-MINUTE-001 / #427, part of #424 |
| Engineering base | PR #426 at 690d96c; a stacked change, not an independently merged baseline |
| Governing design | LLD 01, Minute terminal checkpoint ordering |
| Design status | approved for the bounded C1 implementation |
| Delivery gate | blocked for Linux/deployment acceptance |
| Review method | Same assistant, separated implementation/adversarial-review/test phases; no independent signoff |

## Baseline, target and selected scope

The unmodified collector lost the last five checkpoint entries on a twenty-error
Eastmoney circuit break. Catchable cancellation and persistence exceptions could
also bypass HTTP session close. Eight synthetic cases yielded six failures and two
passing success-path controls before implementation.

The selected fix requires terminal status to follow its processed-security evidence,
with a common exception and session-cleanup boundary. It does not retry failed
securities, load prior checkpoints, change provider response classification, share
1m/5m data, relax coverage, or publish READY. These are independent later gates.

## Adversarial checks

- An interruption during a symbol preserves only completed results; no invented
  receipt for the interrupted or unattempted symbols.
- Persistence failure must leave a non-successful process outcome. It may leave an
  older running status, which is unavailable terminal evidence, not successful data.
- Failure while reporting an existing failure is written to stderr and does not
  hide the original exception. Session cleanup is attempted even on that path.
- Per-file atomic replace is not a two-file transaction or fsync/power-loss guarantee.
  SIGKILL/default SIGTERM do not execute this Python cleanup contract.
- The existing normal 0/1 exit contract and twenty-error circuit limit remain unchanged.
- Source/request methods, scheduling, versioned configuration, immutable Base,
  CN01-to-OCI Daily synchronization and data publication are not modified.

## Acceptance checkpoint

Initial focused verification: 38 passed (8 new cases + existing provider tests).
Final expanded regression: 121 passed, including all 12 new synthetic cases,
existing provider/lane/process/orchestrator and warehouse-documentation contracts.
`ruff check` for the changed Python files and `git diff --check` passed.

The literal `make check` command exited 69 before execution because this host's
Apple make launcher requires Xcode license acceptance. No license was accepted or
system toolchain setting changed. Using the existing uv Python environment, the
Makefile's doctor and compile components passed; full pytest returned
2715 passed / 90 failed / 7 skipped in 112.72 seconds. Its failed test ID set exactly
matches the previous first-batch baseline (2703 passed / 90 failed / 7 skipped).
Separately executed planning preview still fails on the existing
`P3-STRAT-003-B-R1` governing LLD row gap (#412); no planning apply was performed.

This is scoped local regression evidence, not a passing `make check` or Linux gate.
Verdict: PASS_SCOPED_C1_ENGINEERING / NO_GO_PRODUCTION. Linux/systemd, main merge,
node deployment and real daily observation remain unaccepted. Residual-set retries,
identity-bound recovery and shared time budgets remain open under the parent plan.
