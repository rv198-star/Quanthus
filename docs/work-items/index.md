# Work-Item Traceability Index

Stable ids are the join key between requirements, designs, code, tests, and GitHub issues.
The GitHub issue title must begin with the id in brackets. Closing an issue requires links to
acceptance evidence; closing an epic requires all blocking child ids to close.

## Meta

| ID | Title | State |
| --- | --- | --- |
| META-DOC-001 | Maintain approved PRD/HLD/LLD baseline | active |
| META-TRACE-001 | Maintain requirement-to-issue traceability | active |
| META-GATE-001 | Automate release gate reporting | planned |
| META-DATA-PIPE-001 | Operate Hermes ODS releases and OCI T+5h publication | active |
| META-DATA-PIPE-002 | Retire the legacy weekly monolith and split Hermes ODS schedules | planned |

## P1 data foundation

| ID | Title | Depends on |
| --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and dataset manifests | Bootstrap |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 |
| C2 | Classify every security-calendar gap | S4, security master |
| S1 | Measure and complete delisted-security coverage | Security master |
| S2 | Derive historical ST state from announcements | Landing data |
| S3 | Derive board-specific price limits | S2, security master |
| S4 | Build authoritative trading calendar | Source ingestion |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars |
| P1-SNAPSHOT-001 | Implement atomic point-in-time snapshots | Canonical datasets |
| P1-ACCEPT-001 | Execute Chapter 01 acceptance suite | All P1 blockers |
| P1-DATA-002 | Restore incomplete none histories detected by S3 replay | S3 coverage audit |
| P1-DATA-003 | Restore truncated active none histories detected by daily coverage | P1-DATA-002 |

## P2 backtest and observability

| ID | Title | Depends on |
| --- | --- | --- |
| P2-ENG-001 | Define event schemas and deterministic event loop | P1 acceptance |
| P2-POLICY-001 | Freeze fees, slippage, lot, and matching parameters | P1 acceptance |
| P2-MATCH-001 | Implement open matching and tradability handling | P2-ENG-001 |
| P2-LEDGER-001 | Implement unit and external-pool ledger | P2-ENG-001 |
| P2-ORDER-001 | Implement cancel-buy and carry-sell lifecycle | P2-MATCH-001 |
| P2-AUDIT-001 | Implement run manifest and audit persistence | P2-ENG-001 |
| P2-METRIC-001 | Implement fixed-unit performance reports | P2-LEDGER-001 |
| P2-OBS-001 | Define decision and shadow log schema | P2-ENG-001 |
| P2-HYP-001 | Implement diagnostic hypothetical ledger | P2-MATCH-001 |
| P2-ACCEPT-001 | Execute golden backtest acceptance suite | All P2 components |

## P3 rule-only vertical slice

| ID | Title | Depends on |
| --- | --- | --- |
| P3-FEAT-001 | Implement feature registry and lineage | P2 snapshots |
| P3-LABEL-001 | Implement label-card schema and evaluator | P3-FEAT-001 |
| P3-LABEL-002 | Implement vertical-slice labels | P3-LABEL-001 |
| P3-LABEL-003 | Implement label frequency/conflict audit | P3-LABEL-001 |
| P3-LABEL-004 | Build human label review corpus | P3-LABEL-002 |
| P3-EXP-001 | Define expert protocols and abstention | P3 labels |
| P3-AGG-001 | Implement veto precedence and constraint merge | P3-EXP-001 |
| P3-AGG-002 | Implement score and action mapping | P3-AGG-001 |
| P3-SHADOW-001 | Implement shadow execution and divergence log | P3-AGG-001 |
| P3-EXP-002 | Add vertical-slice rule experts | P3 aggregator |
| P3-POS-001 | Implement unit/position state machine | P2 ledger |
| P3-POLICY-001 | Freeze trial/add/hold/cooldown parameters | P2 acceptance |
| P3-RISK-001 | Implement concentration caps | P3-POS-001 |
| P3-POS-002 | Implement trial/add/review rules | P3 experts |
| P3-EXIT-001 | Implement four-source exit arbitration | P3 aggregator |
| P3-POS-003 | Implement cooldown and carried exit | P2 matcher |
| P3-FUNNEL-001 | Implement opportunity funnel | P3 vertical slice |
| P3-ATTR-001 | Implement constrained contribution reruns | P3 vertical slice |
| P3-DASH-001 | Produce monitoring report | Funnel and attribution |
| P3-ACCEPT-001 | Execute multi-year vertical-slice acceptance | All active P3 work |

## P4-P7

| ID | Title | Phase |
| --- | --- | --- |
| P4-EVENT-001 | Define event, isolation, and split schemas | P4 |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4 |
| P4-LABEL-001 | Freeze outcome and structural-break cards | P4 |
| P4-REPORT-001 | Produce daily group comparison report | P4 |
| P5-TARGET-001 | Define independent target cards and metrics | P5 |
| P5-BASE-001 | Build rule and logistic baseline scoreboard | P5 |
| P5-GBDT-001 | Train constrained GBDT research pipeline | P5 |
| P5-PROMOTE-001 | Implement model promotion/shadow gate | P5 |
| P6-GOV-001 | Implement proposal and expectation record | P6 |
| P6-GOV-002 | Implement shadow lifecycle gate | P6 |
| P6-SCORE-001 | Implement expert and aggregator scorecards | P6 |
| P6-DATA-001 | Enforce partition access and reveal logging | P6 |
| P6-REVIEW-001 | Run first quarterly arbitration review | P6 |
| P7-LIVE-001 | Define broker adapter and live safety boundary | P7 |
| P7-RECON-001 | Reconcile live fills, costs, and slippage | P7 |
| P7-SCALE-001 | Implement explicit unit expansion decision | P7 |

## Issue creation policy

P1 and P2 work items are created as actionable issues now. P3-P7 receive traceability epic
issues plus the stable child-id checklist. Child issues for a later phase are created when its
predecessor gate passes, preventing a stale speculative backlog while preserving full design
traceability.

## Planning synchronization

OCI owns GitHub planning synchronization. Preview the document-derived plan without network
access, then apply it through the GitHub CLI authenticated on OCI:

```bash
python3 scripts/sync_github_planning.py
python3 scripts/sync_github_planning.py --apply
```

The synchronization is idempotent, reconciles transient GitHub list inconsistencies, aborts
on duplicate stable IDs, and preserves unmanaged labels. GitHub credentials remain in OCI's
user configuration and are never read from the repository.
