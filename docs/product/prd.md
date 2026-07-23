# Quanthus Product Requirements Document

| Field | Value |
| --- | --- |
| Status | Approved for P1 implementation |
| Version | 0.1 |
| Decision date | 2026-07-23 |
| Product type | Internal research and trading decision system |
| Market | China A-shares, SSE and SZSE |
| Current release | P1 data foundation |

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
6. Validate frozen behavior in simulation before any small-capital live deployment.

## 3. Non-goals

- Price prediction as the primary product.
- High-frequency or latency-sensitive trading.
- Full-market portfolio optimization.
- Compounding capital as the first-order performance mechanism.
- News and financial-statement experts in the first release.
- Production Transformer models before event volume and baselines justify them.
- Use of present-day universes, present-day sector membership, or future adjustment factors.

## 4. Users and operating roles

| Role | Primary need |
| --- | --- |
| Researcher | Reproducible event scans, labels, experiments, and attribution reports |
| Strategy owner | Versioned expert rules, evidence, shadow comparisons, and approvals |
| Operator | Daily decisions, orders, exceptions, and complete audit trails |
| Reviewer | Blind validation, change history, and reproducible acceptance evidence |
| Developer | Explicit contracts, release gates, deterministic fixtures, and issue traceability |

One person may hold several roles, but the artifacts and gates remain separate.

## 5. Product principles

1. Representation does not decide. Models and labels describe state; experts create actions.
2. Risk vetoes have precedence. Force-liquidate outranks reduce-only, which outranks block-new.
3. Capital units have fixed notional. Profits leave and losses are replenished from a finite pool.
4. Rules precede models. A model must beat a frozen rule baseline to enter an action path.
5. Validation discipline outranks performance. Unarbitrated results are research results only.
6. Backtests are conservative. T-close signals may first execute at T+1 open with costs.
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
- **FR-BT-002**: Execute daily signals no earlier than the next session open.
- **FR-BT-003**: Cancel unfilled buys and reassess; carry unfilled sells until tradable.
- **FR-BT-004**: Enforce fixed-capital-unit state transitions and finite external funding.
- **FR-BT-005**: Rank same-day candidates by score descending and code ascending.
- **FR-BT-006**: Produce deterministic results and reconcile golden trade cases exactly.

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

## 8. Release plan and success criteria

| Phase | Outcome | Release gate |
| --- | --- | --- |
| P0 | Frozen system constitution | Policy configuration and contract tests exist |
| P1 | Point-in-time data foundation | D1/D2/D3 pass; C1/C2/S1 are closed |
| P2 | Auditable event-driven backtester | Golden trades reconcile exactly and runs repeat |
| P3 | Rule-only vertical slice | Multi-year replay survives and every decision is auditable |
| P4 | Event library and three-class dataset | Three leading differentiators are reproducible |
| P5 | Research scoring layer | Model beats frozen baseline without regime collapse |
| P6 | Frozen simulation | Results remain inside the expected backtest distribution |
| P7 | Small-capital live operation | Measured fills and slippage align with assumptions |

## 9. Data and validation partitions

- Development: 2012-2021.
- Quarterly arbitration: 2022-2024, rolling into development after reveal.
- Permanent blind: 2025, used only for major promotion decisions.
- Simulation accumulation: 2026 onward.

Any result that has not passed the relevant arbitration gate is labeled as a research result.

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

## 11. Current decision

P1 implementation is released. C1, C2, and S1 block P1 acceptance. P2 remains blocked until
all three Chapter 01 acceptance checks pass. The next executable work is listed in
`docs/work-items/index.md` and `config/p1-gates.v1.json`.
