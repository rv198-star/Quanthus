# Quanthus Product Requirements Document

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | not-applicable |
| Current phase | P6 closed under Owner historical scope; P5 optional research deferred |
| Version | 0.7 |
| Effective date | 2026-10-23 |
| Product type | Internal research and trading decision system |
| Market | China A-shares, SSE and SZSE |
| Governing gate | `config/p3-gates.v1.json` |

## 1. Background

Event-driven A-share research is easy to overstate because historical selection, adjusted
prices, unavailable trades, repeated parameter tuning, and incomplete audit trails can all
inflate results. A useful system must first prove that it can reconstruct what was knowable
at a historical instant and execute a conservative, deterministic simulation.

Quanthus treats a qualified buy-point event as the unit of research and a fixed-capital unit
as the unit of execution. A position remains eligible only while its expected structure is
intact. Rules, constraints, and vetoes decide actions; learned models may produce research
signals but have no direct authority over orders.

## 2. Product objective

Build an auditable platform that increases the probability of discovering a robust
event-driven strategy without claiming or optimizing for a guaranteed return.

The staged objectives are:

1. Replay any supported historical date from the information available at that time.
2. Produce deterministic and conservative backtests with complete decision provenance.
3. Run a rule-only vertical slice whose drawdown is explainable and whose trades are auditable.
4. Build an event library that separates positive, intermediate, failed, and unexecutable cases.
5. Use models as a research instrument and promote them only after strict out-of-sample proof.
6. Validate frozen behavior through paper notification before operator-assisted small-capital
   deployment; automated broker integration is not required.

## 3. Non-goals

- Price prediction as the primary product.
- High-frequency or latency-sensitive trading.
- Full-market portfolio optimization.
- Compounding capital as the first-order performance mechanism.
- News and financial-statement experts in the first release.
- Production Transformer models before event volume and baselines justify them.
- Use of present-day universes, present-day sector membership, or future adjustment factors.
- Automated broker connectivity or unattended live order placement in the near-term product.

## 4. Users and operating roles

| Role | Primary need |
| --- | --- |
| Researcher | Reproducible event scans, labels, experiments, and attribution reports |
| Strategy owner | Versioned expert rules, evidence, shadow comparisons, and approvals |
| Operator | Windowed action-advice emails, manual actions, actual-operation reports, and exceptions |
| Reviewer | Blind validation, change history, and reproducible acceptance evidence |
| Developer | Explicit contracts, release gates, deterministic fixtures, and issue traceability |

One person may hold several roles, but the artifacts and gates remain separate.

## 5. Product principles

1. Representation does not decide. Models and labels describe state; experts create actions.
2. Risk vetoes have precedence. Force-liquidate outranks reduce-only, which outranks block-new.
3. Capital units have fixed notional. Profits leave and losses are replenished from a finite pool.
4. Rules precede models. A model must beat a frozen rule baseline to enter an action path.
5. Validation discipline outranks performance. Unarbitrated results are research results only.
6. Backtests are conservative. The active T-close profile may first execute at T+1 open; any
   intraday profile freezes its windows, PIT cutoffs, fill references, and costs before use.
7. Every accepted, rejected, shadow, and hypothetical decision is traceable to a snapshot.

## 6. Functional requirements

### Data foundation

- **FR-DATA-001**: Store unadjusted daily bars and effective-date adjustment factors separately.
- **FR-DATA-002**: Preserve delisted securities and the historical security lifecycle.
- **FR-DATA-003**: Classify each security-calendar row as trading, suspended, not yet listed,
  delisted, or missing.
- **FR-DATA-004**: Produce a point-in-time snapshot identified by market date, knowledge
  cutoff, source revision, and snapshot id.
- **FR-DATA-005**: Derive historical tradability, including ST state and board-specific limits.
- **FR-DATA-006**: Disable modules whose required historical data is unavailable; no current
  membership proxy may enter official results.

### Backtest and execution

- **FR-BT-001**: Consume dated signals and produce fills, rejections, order events, and ledgers.
- **FR-BT-002**: Under the active daily profile, execute T-close signals no earlier than the next
  session open.
- **FR-BT-003**: Cancel unfilled buys and reassess; carry unfilled sells until tradable.
- **FR-BT-004**: Enforce fixed-capital-unit state transitions and finite external funding.
- **FR-BT-005**: Rank same-day candidates by score descending and code ascending.
- **FR-BT-006**: Produce deterministic results and reconcile golden trade cases exactly.
- **FR-BT-007**: Support a versioned target profile with exactly two eligible action windows per
  session; every decision and simulated execution carries its profile, window, and PIT cutoff.

### Rules, experts, and positions

- **FR-RULE-001**: Define each label as a versioned, replayable function with evidence.
- **FR-RULE-002**: Support scoring, constraint, and veto expert contracts.
- **FR-RULE-003**: Apply vetoes, constraint intersection, scoring, and action mapping in order.
- **FR-RULE-004**: Limit one level-one industry to two units and new entries to two units/day.
- **FR-RULE-005**: Exit on structural break, constraint stop, consecutive deterioration, or
  maximum holding period.
- **FR-RULE-006**: Run non-vertical-slice experts in shadow mode during the first rule release.

### Events and research

- **FR-EVENT-001**: Define event T at close and include T in the M-day feature window.
- **FR-EVENT-002**: Measure outcomes from an executable T+1 entry.
- **FR-EVENT-003**: Exclude `not_executable` events from training and official backtest
  evaluation while retaining them as a research subset.
- **FR-EVENT-004**: Use an independent, frozen structural-break label.
- **FR-ML-001**: Establish rule and logistic baselines before GBDT research.
- **FR-ML-002**: Prevent event slices from crossing training or validation folds.
- **FR-ML-003**: Keep model outputs outside the direct action path.

### Audit, governance, and attribution

- **FR-AUDIT-001**: Record complete expert outputs, constraints, vetoes, actions, reasons, and
  snapshot references for every candidate and position review.
- **FR-AUDIT-002**: Record a run manifest containing code, policy, data, feature, and rule ids.
- **FR-AUDIT-003**: Keep single-signal hypothetical PnL diagnostic only.
- **FR-AUDIT-004**: Calculate official risk-avoided and opportunity-captured amounts from
  capital-constrained strategy reruns.
- **FR-AUDIT-005**: Persist action advice, notification, operator-reported action, simulated
  execution, and actual ledger events separately; email delivery never constitutes an order or
  fill, and only an accepted operator report may update the actual ledger.
- **FR-GOV-001**: Enforce proposal, review, shadow, and promote/rollback states.
- **FR-GOV-002**: Separate development, arbitration, permanent blind, and live simulation data.

## 7. Non-functional requirements

- **NFR-001 Determinism**: Identical code, configuration, and snapshot ids produce identical output.
- **NFR-002 Traceability**: Any output can be traced to immutable source rows and a run manifest.
- **NFR-003 Point-in-time safety**: Automated checks reject values newer than knowledge cutoff.
- **NFR-004 Reproducibility**: Data, policy, feature, label, rule, and code versions are explicit.
- **NFR-005 Fail closed**: Invalid data blocks new risk and alerts; it does not invent a sell reason.
- **NFR-006 Testability**: Business rules have unit tests and critical paths have golden fixtures.
- **NFR-007 Local-first operation**: P1/P2 can run on OCI without external service dependencies.
- **NFR-008 Secret hygiene**: Credentials and market data are never committed.
- **NFR-009 Notification idempotency**: Retry preserves one logical notification per signal and
  cannot create a duplicate opportunity or execution record.

## 8. Release plan and success criteria

| Phase | Outcome | Release gate |
| --- | --- | --- |
| P0 | Frozen system constitution | Policy configuration and contract tests exist |
| P1 | Point-in-time data foundation | D1/D2/D3 pass; C1/C2/S1 are closed |
| P2 | Auditable event-driven backtester | Golden trades reconcile exactly and runs repeat |
| P3 | Rule-only vertical slice | Multi-year replay survives and every decision is auditable |
| P4 | Event library and three-class dataset | Three leading differentiators are reproducible |
| P5 | Optional research scoring layer | Model beats frozen baseline without regime collapse |
| P6 | Completed historical-mechanism and paper-flow validation | Owner scope closeout 2026-10-23; future prospective budget cancelled, not accepted; no production or P7 authority |
| P7 | Operator-assisted small-capital operation | Every advice reconciles to reported fills, modified/partial action, or no action |

## 9. Data and validation partitions

- Development: 2012-2021.
- Quarterly arbitration: 2022-2024, rolling into development after reveal.
- Permanent blind: 2025, used only for major promotion decisions.
- Simulation accumulation: 2026 onward.

Any result that has not passed the relevant arbitration gate is labeled as a research result.
Official two-window backtests are additionally limited to accepted intraday evidence. The current
data policy starts official 5-minute history at 2024-07; daily OHLC cannot stand in for an
intraday decision or fill when reporting two-window performance.

## 10. Key product risks

| Risk | Control |
| --- | --- |
| Survivorship bias | Preserve delisted securities and historical daily universes |
| Adjustment leakage | Unadjusted prices plus effective-date factors only |
| Missing state history | Explicit gap classification and announcement-derived status |
| Strategy overfitting | Frozen baselines, quotas, shadow mode, arbitration, blind year |
| Correlated unit exposure | Industry and same-day entry concentration caps |
| Counterfactual overclaim | Official values from capital-constrained reruns only |
| Model authority creep | Model outputs cannot bypass expert contracts |
| Intraday proxy leakage | Two-window claims require accepted intraday snapshots and window cutoffs |
| Notification/execution confusion | Advice, email, paper fills, operator reports, and actual-ledger events remain separate |

## 11. Current decision

P1 is conditionally accepted against immutable snapshot
`p1-identity-code-validity-20260725-v1`. C1's 4,173 batch-dated PIT factor exceptions are not a
full-data pass: they are accepted only under `p1-c1-conditional-20260725-v1`, which requires any
factor-dependent historical path to fail closed before a declared knowledge cutoff. C2 and S1 are
closed. P2 is conditionally accepted for the frozen `close-to-next-open-v1` historical and paper
research policy. P3 is accepted for the bounded rule-only historical capability slice under
`config/p3-gates.v1.json`. The P4 Event Library, Label Corpus, Comparison Report, and bounded
low-memory Worker execution are accepted by their production evidence. The frozen P3 candidate is
an acceptance fixture, not an approved strategy lineage; it cannot be tuned or promoted by that
gate. The separate two-expert strategy MVP is also accepted for development evidence, but its
lifecycle decision is `inconclusive` and its forward-Shadow disposition is disabled. None of these
acceptances activates the target intraday profile or opens the 2022-2025 partitions. `P6-GOV-001`
has accepted one finite prospective-mechanism-MVP entry proposal under the daily
`close-to-next-open-v1` profile. The mandatory owner value checkpoint subsequently opened and closed
`P6-DATA-001`, which now supplies the prospective DWD identity/visibility, access-ledger, and
correction-disposition boundary. `P6-SCORE-001` subsequently froze role-specific forward scorecards
and material-loss attribution before results. `P6-GOV-002` then admitted the existing Replay Worker
P6 job kind and passed disabled-state sealing/recovery/fencing with zero prospective side effects.
`P6-PAPER-001` subsequently accepted the isolated five-unit fixed-notional P2 paper lane without
actual execution authority. `P6-WEB-001` then accepted the read-only Forward/Paper candidate-detail
projection with no activation or mutation controls. The older activation and `0 / 66`
collection checkpoint remain historical evidence. On 2026-10-23 the Owner
[closed P6 under the accepted historical-mechanism scope](../acceptance/p6-owner-scope-closeout-20261023.md).
P6-ACTIVATE-001 and P6-REVIEW-001 / #120 are cancelled, not accepted; the future
66-session collection and 20-session tail are no longer current work. The CN02
controller remains disabled. No strict PIT, economic, production or P7 authority
is inferred. P5 model research is optional, deferred, and not a P6 dependency.
In any separately authorized future programme, official prospective evidence begins only
after a separate immutable activation decision and cannot be
backfilled from earlier 2026 history. The next executable work is listed in
`docs/work-items/index.md`. The approved
near-term operating direction is `operator-assisted-two-window-v1`: exactly two versioned action
windows at `09:45-10:15` and `14:15-session close` in `Asia/Shanghai`, email notification, manual
operator action, and no broker connection. This later direction is outside the initial P6 scope.
The clock bounds are approved; evaluation cadence,
data-latency, and fill policy remain blocked until a versioned executable configuration and
acceptance evidence are committed. `close-to-next-open-v1` remains the active executable profile.
