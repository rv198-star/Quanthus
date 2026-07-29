# Documentation Map

This file is the level-zero router for people and AI sessions. It names the current delivery
branch and the smallest context set for common tasks; detailed rules and design stay in child
nodes.

| Field | Value |
| --- | --- |
| Current phase | P3 implementation open |
| Gate source | `config/p2-gates.v1.json` |

## Current branch

- P1 is accepted with the versioned C1 fail-closed condition.
- P2 is conditionally accepted for historical and paper research under the frozen
  `close-to-next-open-v1` profile. Its matcher, unit accounting, audit persistence, decision and
  shadow records, diagnostic replay, P1 execution-state adapter, and golden round trip pass.
- P3 rule-only historical implementation is open through that accepted daily profile.
- The strategy-neutral daily replay substrate is accepted under `P3-REPLAY-001`, including strict
  typed P1-frame adaptation, carried-sell company actions, immutable replay packaging, the lazy
  continuous PIT frame reader, full 2012-2025 input audit, and a real-corpus round-trip smoke.
- The research-only `P3-FEAT-002`/`P3-STRAT-001` increment adds a vectorized PIT daily-indicator
  panel and a three-strategy one-unit baseline runner. Moderated catalogue v2 is the CLI default;
  v1 remains a replayable but rejected extreme-control record. The command is locked to 2012-2021
  development data and does not promote a target P3 policy or open arbitration/blind partitions.
- Targeted backtest sanity diagnostics passed a same-pipeline single-security buy-and-hold sentinel,
  reproduced v1 near-total losses with every cost set to zero, and reconciled daily-return extremes
  to raw bars. This narrows the v1 failure to strategy construction without making the sentinel a
  market benchmark or proving every engine path.
- The mixed-unit corporate-action normalization contract and a real-corpus audit are implemented
  under `P1-DATA-004`. Exact implementation-plan authority resolves the former factor/unit
  ambiguity, and all 272 retained rows now have a versioned per-security fail-closed disposition.
  Snapshot `p1-historical-replay-20260729-v1` publishes the 35,157 normalized actions, that ledger,
  the authoritative calendar, and accepted daily execution inputs together. The completeness,
  checksum, frame, and exception gates accept it for research replay without granting execution
  authority.
- P3 candidate-to-empty-unit reservation competition is accepted under the existing frozen P0
  ordering and concentration caps; sizing, concrete labels/experts/actions, multi-unit execution,
  and multi-year acceptance remain open.
- P3 opportunity-funnel reconciliation is accepted as a non-executing contract over explicitly
  supplied source events; concrete P3 decisions and populated historical funnel reports remain
  open.
- The operator-assisted two-window profile remains blocked by intraday-input and matching-policy
  evidence; forward email transport remains P6 work. P4 and later keep their existing gates.
- `design/stock-stage-state-machine.md` is a non-governing P3 draft and is not P2 context.

The current machine-readable release state is `config/p2-gates.v1.json`; P1's retained gate is
`config/p1-gates.v1.json`. Prose that disagrees with either applicable
gate is a conformance defect rather than an alternate project state.

## Tree

| Branch | Authority and purpose | Child index |
| --- | --- | --- |
| `product/` | Product goals, requirements, non-goals, and release criteria | `product/prd.md` |
| `design/` | Approved current-state HLD, execution profiles, and module LLDs | `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md` |
| `architecture/` | Accepted decisions and stable topic contracts | `architecture/README.md` |
| `work-items/` | Stable ids joining design, code, tests, issues, and evidence | `work-items/index.md` |
| `operations/` | Deployment, recurring operation, incident, migration, and rollback | Runbook selected by the active component |
| `acceptance/` | Point-in-time acceptance and conformance evidence | Load only for the claim being audited |
| `hermes/` | Historical audit and handoff artifacts | Not part of default design context |

## Progressive load routes

| Task | Required context | Add only when needed |
| --- | --- | --- |
| P2 backtest implementation | root `AGENTS.md`, this map, `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md`, `design/lld/02-backtest-engine.md` | LLD 01 for inputs, LLD 10 for logs, LLD 05 for boundary checks |
| P1/P2 data input | this map, HLD, LLD 01 | Linked architecture topic, ADR 0002/0003/0004, relevant acceptance file |
| ODS or daily operations | this map, architecture index, ADR 0003/0004, selected runbook | Exact config, service, and contract tests |
| Product or phase decision | PRD, HLD, LLD 09, work-item index | Governing gate, ADR, or acceptance evidence |
| P3 state-model discussion | HLD, LLD 03, state-machine draft | Never load as P2 implementation authority |
| P3 strategy-semantics discussion | HLD, `design/p3-strategy-semantics-checklist.md` | Load LLD 03/04/05 only for the decision group being discussed; the checklist is non-governing |
| P3 rule-only implementation | root `AGENTS.md`, this map, HLD, LLD 03/04/05, accepted P2 interfaces | Exact P1 input node and P2 evidence for the path under test |
| P3 strategy historical replay | root `AGENTS.md`, this map, HLD, LLD 02 and its `strategy-replay.md` child | Exact strategy, P1 snapshot input, and P2 execution evidence |

## Authority and conflicts

`design/source-precedence.md` defines repository and external-source authority. Within the
repository, executable policy and approved design must agree. Contract-test success does not make
an undocumented behavior architectural truth, and prose does not override a frozen executable
contract.

## Change discipline

Every behavior-changing change identifies the requirement or work-item id, governing design node,
affected code/config paths, contract or golden tests, and acceptance evidence. Accepted ADRs update
current-state HLD/LLD text in the same change. Drafts, discussions, runbooks, and evidence files do
not silently promote themselves into the design baseline.
