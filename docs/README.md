# Documentation Map

This file is the level-zero router for people and AI sessions. It names the current delivery
branch and the smallest context set for common tasks; detailed rules and design stay in child
nodes.

| Field | Value |
| --- | --- |
| Current phase | P2 implementation open |
| Gate source | `config/p1-gates.v1.json` |

## Current branch

- P1 is accepted with the versioned C1 fail-closed condition.
- The P2 matcher, order, ledger, and reporting engine is not implemented.
- P3 and later delivery remain blocked by their existing gates.
- `design/stock-stage-state-machine.md` is a non-governing P3 draft and is not P2 context.

The machine-readable release state is `config/p1-gates.v1.json`. Prose that disagrees with that
gate is a conformance defect rather than an alternate project state.

## Tree

| Branch | Authority and purpose | Child index |
| --- | --- | --- |
| `product/` | Product goals, requirements, non-goals, and release criteria | `product/prd.md` |
| `design/` | Approved current-state HLD and module LLDs | `design/hld.md`, `design/lld/README.md` |
| `architecture/` | Accepted decisions and stable topic contracts | `architecture/README.md` |
| `work-items/` | Stable ids joining design, code, tests, issues, and evidence | `work-items/index.md` |
| `operations/` | Deployment, recurring operation, incident, migration, and rollback | Runbook selected by the active component |
| `acceptance/` | Point-in-time acceptance and conformance evidence | Load only for the claim being audited |
| `hermes/` | Historical audit and handoff artifacts | Not part of default design context |

## Progressive load routes

| Task | Required context | Add only when needed |
| --- | --- | --- |
| P2 backtest implementation | root `AGENTS.md`, this map, `design/hld.md`, `design/lld/README.md`, `design/lld/02-backtest-engine.md` | LLD 01 for inputs, LLD 10 for logs, LLD 05 for boundary checks |
| P1/P2 data input | this map, HLD, LLD 01 | Linked architecture topic, ADR 0002/0003/0004, relevant acceptance file |
| ODS or daily operations | this map, architecture index, ADR 0003/0004, selected runbook | Exact config, service, and contract tests |
| Product or phase decision | PRD, HLD, LLD 09, work-item index | Governing gate, ADR, or acceptance evidence |
| P3 state-model discussion | HLD, LLD 03, state-machine draft | Never load as P2 implementation authority |

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
