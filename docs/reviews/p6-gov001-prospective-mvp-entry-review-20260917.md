# P6-GOV-001 Prospective MVP Entry Review — 2026-09-17

| Field | Value |
| --- | --- |
| Review state | `APPROVE_PROSPECTIVE_MECHANISM_MVP_ENTRY_FREEZE` |
| Governing work | `P6-GOV-001` |
| Parent route decision | `quanthus-multi-expert-successor-route-review-20260917.md` |
| Strategy under review | `candidate.p4-trend-risk@1.0.1` |
| Immediate authority | freeze entry proposal only |
| Prospective activation | not authorized by this review |
| Automatic downstream continuation | forbidden; owner re-review required after P6-GOV-001 |

## 1. Decision question

The owner clarified that the current two-expert system and mature Stage10/11 semantics are too narrow
to stand in for a complete Quanthus expert universe. The purpose of the first P6 increment is therefore
not to prove that the current expert catalogue or Stage vocabulary is complete. The bounded question
is narrower:

> Can the already accepted two-expert strategy participate in a genuinely prospective, auditable,
> outcome-blind observation -> decision -> settlement -> paper-reconciliation loop without semantic,
> data-time, authority, or execution drift?

If this mechanism is trustworthy on a finite local scope, later forward evidence may identify which
expert, Stage, sector, aggregation, or strategy capability deserves the next research budget. If the
mechanism itself is not trustworthy, expanding the catalogue first would compound uncertainty.

## 2. Frozen baseline

The proposal must reuse, without semantic edits:

- `candidate.p4-trend-risk@1.0.1`, semantic digest
  `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`;
- `expert.trend-stability@1.0.0`, with the frozen future-target evaluation and formal entry mapping;
- `expert.position-risk-review@1.0.0`, with the frozen policy-only risk mapping;
- target `p4-event.outcome-first-passage@1.0.0`, 20-trading-session horizon, frozen resolver and
  scorer;
- existing aggregation order and hard system guardians;
- `close-to-next-open-v1`, five fixed CNY 100,000 capital units, no profit redeployment and no
  compounding;
- the accepted P3 lifecycle evidence, which remains immutable `inconclusive + Shadow disabled` until
  a later activation decision creates a new append-only P6 authority record.

Stage10/11 remain optional development advisory semantics. They do not enter the P6 action path in
this proposal, and this review does not reopen Stage research or grant Stage action authority.

## 3. Four-perspective entry-policy review

The perspectives below are a single-assistant role-separated simulation against the same repository
evidence, not four independent external reviewers. The earlier successor-route review already records
why external Astra isolation was unavailable. This review uses the roles to test the budget and
boundary decision, while repository contracts remain the authority.

### 3.1 Short-term / event-driven

A one-week or single-market-event run is too weak because it could validate only plumbing. The entry
budget should span multiple non-overlapping market windows so that the system sees changes in event
flow, expert abstention, disagreement, veto/constraint use, and paper execution. The role accepts
three non-overlapping 22-session blocks as the smallest useful bounded sequence. Economic direction
must remain descriptive in this mechanism MVP; a poor return is valid evidence rather than a reason
to edit rules mid-run.

### 3.2 Fund / midterm risk

The first forward program should privilege ledger integrity, frozen risk rules, missing-data
fail-closed behavior, correction handling, and rollback over return optimization. Fixed units and
non-compounding capital must remain unchanged. Any real-capital, notification, broker, leverage, or
silent parameter change is outside the MVP. A hard calendar ceiling is required so an inactive or
low-opportunity market cannot keep the experiment open indefinitely.

### 3.3 Quant research

Cross-sectional opportunity rows are correlated through market dates, event windows, sectors and
capital competition; they cannot count as independent evidence. The existing 22-session non-overlap
unit is already used in the accepted expert uncertainty design and covers the candidate feature/
target dependence window. Freeze three such blocks: 66 collection sessions. Add the frozen
20-session target-maturity tail. Use raw opportunity counts only as coverage diagnostics, never as
budget progress. If the final corpus is too sparse to exercise settlement, close
`inconclusive_low_activity`; do not extend the budget or change thresholds after seeing results.

### 3.4 Data governance / warehouse

The proposal itself must not activate anything. Official evidence may begin only after a future
`P6-ACTIVATE-001` append-only decision. To avoid same-day partial-information ambiguity, the first
official market date is the first complete trading session strictly after the activation decision's
Asia/Shanghai market date whose DWD release is READY and deeply verifiable. Earlier 2026 data is
rehearsal-only. Every later cycle must bind publication/visibility time and one immutable DWD
release; corrections append a disposition and never rewrite an observation. Arbitration 2022-2024
and permanent blind 2025 remain sealed.

## 4. Frozen budget and independence decision

The review approves the following finite evidence budget for the first prospective mechanism MVP:

- independent budget unit: `non_overlapping_22_trading_session_market_block`;
- collection blocks: `3`;
- sessions per block: `22`;
- collection sessions: `66`;
- longest target-maturity tail: `20` trading sessions;
- maximum wall-calendar duration from the first official market date: `150` calendar days;
- cross-sectional securities, candidates, expert observations, decisions and fills do not increase
  independent-budget progress;
- budget extension after result exposure is forbidden;
- if the finite window produces insufficient settleable/applicable observations to exercise the
  declared target and paper path, close `inconclusive_low_activity` and return to route review.

The 66-session collection budget is not a claim of statistical power for profitability. It is a
bounded mechanism-validation window aligned to three complete dependence blocks and the existing
22-session event/evaluation unit.

## 5. Primary gate and descriptive evidence

The primary MVP gate is **mechanism integrity**, not profit. P6 later scorecard work must implement
this precommitment without changing its meaning.

The integrity conjunction requires:

1. every official observation/aggregate decision is sealed before any target outcome used to settle
   that observation becomes available to the decision path;
2. zero post-cutoff/PIT evidence violations;
3. zero strategy/expert/policy/target/resolver/aggregation/P2 semantic drift;
4. zero unauthorized notification, broker, actual-fill, live-capital or sealed-partition events;
5. exact same-release reproduction has zero semantic mismatch;
6. every admitted paper cash/position/fill transition reconciles under the frozen fixed-unit P2
   contract;
7. late corrections are append-only dispositions and never rewrite published observations.

The following are mandatory descriptive evidence but do not become favorable-performance gates in
this MVP:

- trend-stability target error versus its frozen always-neutral baseline, coverage and abstention;
- position-risk applicability, abstention, constraint/veto/reduce/exit activity;
- aggregator agreement/disagreement and blocked-new/action distributions;
- fixed-unit paper return, drawdown, turnover, exposure, fill/reject/carry rates and honest-replay
  divergence;
- regime/date-block slices and dependence-aware uncertainty where sample size permits.

A negative, weak or inconclusive economic result is an acceptable P6 outcome. It may motivate a
later expert/Stage/strategy change only through a new proposal; it cannot trigger in-place tuning.

## 6. Expected behavior and coverage guardrails

The proposal does not predict a favorable return or a fixed expert-state distribution. It expects
only that the exact frozen semantics remain operationally observable:

- the trend expert may emit fragile, neutral, stable or abstain under its existing contract;
- the position-risk expert never gains positive entry authority;
- system guardians remain non-intervenable;
- the accepted aggregator precedence remains unchanged;
- Shadow and paper lanes consume the same aggregate decision but have distinct authority;
- any lack of sufficient action/settlement exposure is reported as an inconclusive coverage limit,
  not repaired by extending the observation window.

## 7. Stop conditions

Before or after later activation, stop or pause the prospective program if any of these occur:

- bound strategy/expert/policy/target/resolver/P2 digest drift;
- a new expert, Stage action mapping, aggregator mechanism or P5 model is required to continue the
  same proposal;
- DWD publication/visibility identity cannot be proven for an official cycle;
- post-cutoff evidence enters an observation or decision;
- a correction rewrites instead of appends to prior evidence;
- same-release independent reproduction changes semantic output;
- Shadow creates an Order/Fill or paper authority crosses into actual execution;
- paper ledger/cash/position reconciliation breaks;
- arbitration 2022-2024 or permanent blind 2025 access is attempted;
- notification, broker connection, live capital, or automated promotion enters scope;
- the 150-calendar-day ceiling expires before the frozen collection plus maturity-tail conditions
  become review-ready.

Low activity, weak expert quality, poor paper return or high abstention are evidence, not
infrastructure failures. They may close the MVP inconclusive or negative without rescue tuning.

## 8. Rollback and next gate

Rollback keeps every proposal, activation, Run, observation, settlement, incident and paper-ledger
record immutable, disables the future P6 admission binding, and rejects new P6 submissions. Existing
historical P3/P4 evidence remains unchanged.

Acceptance of `P6-GOV-001` must stop at a value checkpoint. `P6-DATA-001` remains blocked until a
fresh owner review explicitly decides that the frozen 66-session + 20-session-tail prospective MVP
is worth implementing. This review grants no official prospective evidence, Worker, paper, Web,
activation, notification, broker or live-capital authority.
