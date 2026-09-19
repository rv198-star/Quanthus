# P3-STRAT-003-A preregistration acceptance — 2026-09-20

| Field | Value |
| --- | --- |
| Work item | `P3-STRAT-003-A` / GitHub #318 |
| Decision | `PASS_FREEZE_RECENT_HISTORY_FACTORIAL_BEFORE_RESULT_ACCESS` |
| Parent | `P3-STRAT-003` / GitHub #317 |
| Frozen request | `config/research/p3-strat-003/request.v1.json` |
| Request digest | `sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde` |
| Baseline source revision | `b0956a80f4ac48798ea891f36b07868a921fd74d` |
| Result-bearing recent-history reads during A | zero |
| P6 mutation | none |
| Next gates | B and C may proceed in parallel; D remains blocked by both |

## Decision

A is accepted as a **pre-result research freeze**, not as a strategy result.

The owner direction is to keep the existing P6-v1 long-running prospective observation intact while
using historical replay to answer questions that do not need months of new calendar time.
P3-STRAT-003 therefore does not wait for P6 and does not replace, shorten, rewrite, or consume the
P6 budget.

The request freezes one new question: whether the existing Trend and Position Risk strategy effects
add historical economic value relative to a non-empty deterministic P4 candidate baseline under the
same candidate, evidence, guardian, capital, matcher, cost and base-position rules.

## Frozen comparison

Exactly four variants are admitted:

- **B** — deterministic candidate pass-through control; Trend off; Position Risk off;
- **T** — exact existing Trend mapping; Position Risk off;
- **R** — deterministic candidate pass-through control; exact existing Position Risk;
- **TR** — exact existing Trend plus exact existing Position Risk.

The control emits a positive score only for an otherwise eligible candidate and still traverses the
accepted aggregator and all system guardians. It is not the old Trend-abstention fallback and cannot
turn missing, invalid or untradeable input into an entry.

The headline contrast is `TR-B`. The factorial contrasts `T-B`, `R-B`, `TR-R`, `TR-T`
and `TR-T-R+B` are diagnostics and cannot replace the headline after result access.

## Data authority

The recent-history windows are fixed before results:

- **2025-01-01..2025-12-31** — one-time bounded major-strategy validation. Its previous
  permanent-blind status is consumed when D first reads result-bearing evidence. After that read,
  successor work must label 2025 as exposed rather than untouched.
- **2026-01-01..2026-09-18** — fixed recent-history window derived from the parent planning anchor
  at 2026-09-19T16:35:56Z. It does not roll with CURRENT.
- **2022-2024** — remains closed for general result access. B may resolve only the exact 60 exchange
  sessions immediately preceding 2025-01-01 for feature/candidate warm-up, with labels and strategy
  outcomes forbidden.

A performs no 2025/2026 strategy-result read. B may qualify and seal inputs but may not produce
strategy economics. C may use synthetic or already-consumed golden evidence only. D is the first
result-bearing gate.

Open terminal positions are marked to market at the final admitted close without forced
liquidation. Unmatured targets remain pending; D does not wait for future data only to make the
window look mature.

## Frozen evidence rules

The existing formal-attribution materiality scale is reused without result-driven edits:

- return delta: 1 percentage point;
- maximum-drawdown delta: 1 percentage point;
- action-change rate: 2%.

The paired non-overlapping 22-session block bootstrap uses 2,000 deterministic resamples and a 95%
interval. The 22-session block is explicitly a dependence-aware resampling device, not proof that
market observations are independent.

Return increment and drawdown increment are reported separately. Negative economics, material harm,
no supported increment and inconclusive evidence are all valid research outcomes. They do not
authorize a parameter sweep.

## Resource and retry freeze

D is limited to two result-bearing composite batches: one primary run and one independent cold
reproduction. Each batch contains B/T/R/TR, uses Worker concurrency one, builds the expensive
provider prerequisite once, and has a 14 GiB proactive / 16 GiB hard memory boundary.

The independent reproduction cannot reuse primary-batch intermediates. At most one transport or
environment recovery is permitted per batch **only before a result becomes visible** and with
identical semantic inputs. There is no post-result retry under this request.

## P6 isolation

The request binds the existing P6 design and entry files by SHA-256 only to prove non-mutation.
P3-STRAT-003 may not alter the P6 Worker, activation, admission, run root, paper ledger, evidence
budget or mutable data pointer. P6-REVIEW-001/#120 remains an independent later review.

## Verification

The same change passed:

- focused P3-STRAT-003 preregistration/planning plus existing Expert Strategy planning contracts:
  19 passed;
- `doctor`, `compile`, and `planning-check`;
- every repository pytest file: 330 files partitioned deterministically for tool-safe execution,
  producing 2,670 passed and 7 existing environment skips for unavailable external PostgreSQL or
  production-mounted artifacts, with zero failures.

A monolithic `make check` was invoked first. Its long pytest child exceeded the MCP foreground
window and the transport replayed it, so duplicate children were terminated instead of wasting a
second complete run. The exact `make check` constituents were then executed explicitly:
`doctor`, `compile`, the complete pytest file set, and `planning-check`. No test file was
dropped; the one heavy shard was only subdivided further.

The contract tests recompute the request digest and the SHA-256 of every bound existing authority.
They also prove the four-factor surface, fixed 2025/2026 roles, no roll-forward, no P6 dependency,
two-batch cold reproduction budget, and honest negative/inconclusive closeout.

## Claim ceiling

This acceptance authorizes **B input qualification and C result-blind implementation only**.

It does not claim that 2025 has already been read, that the simple baseline is profitable, that the
two experts add value, that any comparison has run, that D is open, or that any new expert,
aggregator mechanism, P5/P7 feature, broker connection or live capital is authorized.
