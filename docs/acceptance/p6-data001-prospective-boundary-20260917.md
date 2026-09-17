# P6-DATA-001 Prospective Data Boundary Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted / closed |
| Work item | `P6-DATA-001` |
| Parent | accepted `P6-GOV-001` |
| Owner value decision | `config/forward-validation/entry/p6-data-001-owner-value-decision.v1.json` |
| Data policy | `config/forward-validation/data/p6-prospective-data-boundary.v1.json` |
| Design | `docs/design/lld/08-validation-governance/p6-data-boundary.md` |
| Implementation | `src/quanthus/forward_validation/data_boundary.py` |
| Official prospective evidence read | none |
| Activation authority | none |
| Sealed partitions accessed | none |

## Decision

`P6-DATA-001` closes the prospective data-time and input-audit boundary needed by later P6 work.
The post-entry owner value review accepts the frozen 66-session collection budget, 20-session
maturity tail and 150-calendar-day ceiling as worthwhile mechanism-validation cost and opens this
data child only. The accepted P6 entry proposal itself remains byte-immutable and non-activating.

No historical 2026 release is relabeled as prospective evidence by this acceptance.

## Accepted contracts

The implementation freezes four independently auditable concerns:

1. **Daily DWD identity.** Only an immutable `cn01_daily_dwd_domain_increment` that passes the
   existing `verify_daily_domain_increment` deep verifier can enter the P6 boundary. P6 records
   package id, market date, manifest SHA, content digest and exact code revision.
2. **Operational visibility.** A separate deterministic visibility receipt binds the same package
   identity to actual READY-observed and consumer-visible clocks. Operational visibility does not
   become part of DWD business-fact identity.
3. **Access ledger.** Every P6 read creates a deterministic append-only access entry binding purpose,
   package, decision cutoff, consumption time, entry/activation digests, official/rehearsal state
   and partition. Only `prospective_daily_dwd` is admitted by the first program.
4. **Correction disposition.** A later correction uses a new immutable release identity and an
   append-only `preserve`, `degrade` or `censor` disposition. Prior observations and access entries
   are never rewritten.

## Prospective admission boundary

Without an enabled `P6-ACTIVATE-001` decision, every release is engineering rehearsal only. With a
later enabled activation, a release can become an official prospective candidate only when its
market date is strictly after the activation market date and the deeply verified package/visibility
binding is intact. The activation-date release itself remains rehearsal-only.

This acceptance therefore supplies the implementation required for the future boundary while
creating no official prospective observation today.

## Fail-closed behavior

Tests cover package/receipt identity mismatch, reversed visibility clocks, duplicate required
identity, pre-activation and same-day admission, pre-visibility read, cutoff/date mismatch, sealed
partition access, invalid correction disposition/identity reuse and downstream authority drift.

Arbitration 2022-2024 and permanent-blind 2025 remain explicit forbidden partition identities.
Missing or invalid data never becomes structural damage or a forced sale.

## Verification

Focused P6 entry/data/planning tests passed before repository-wide verification. Full repository
`make check` then passed on the isolated `origin/main` worktree:

- environment doctor: PASS;
- Python compilation: PASS;
- repository pytest: **2599 passed / 7 existing environment skips / 0 failed**;
- planning synchronization: PASS.

The seven skips are the existing optional production expert-artifact or external PostgreSQL-DSN
suites. No production service, Worker Job, DWD reader, paper account or forward cycle was started by
this acceptance.

## Authority after closure

```text
P6-GOV-001: CLOSED / ACCEPTED
P6-DATA-001: CLOSED / ACCEPTED
P6-SCORE-001: dependency-ready, not yet accepted
P6-GOV-002 and later: BLOCKED
P6-ACTIVATE-001: NOT OPENED
official prospective evidence: NONE
notification / broker / live capital: NONE
```

`P6-SCORE-001` is now the next dependency-ready child, but this acceptance does not automatically
approve any scorecard semantics.

## Claim ceiling

This evidence proves the engineering/governance boundary for future prospective data access. It does
not prove future DWD availability, expert quality, strategy value, paper economics, forward-worker
correctness or readiness to activate an official cycle.
