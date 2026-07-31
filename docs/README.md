# Documentation Map

This file is the level-zero router for people and AI sessions. It names the current delivery
branch and the smallest context set for common tasks; detailed rules and design stay in child
nodes.

| Field | Value |
| --- | --- |
| Current phase | P4 implementation open |
| Gate source | `config/p3-gates.v1.json` |

## Current branch

- P1 is accepted with the versioned C1 fail-closed condition.
- P2 is conditionally accepted for historical and paper research under the frozen
  `close-to-next-open-v1` profile. Its matcher, unit accounting, audit persistence, decision and
  shadow records, diagnostic replay, P1 execution-state adapter, and golden round trip pass.
- P3 rule-only historical capability acceptance is closed under `config/p3-gates.v1.json`; P4
  event-library implementation is open through the same accepted daily profile.
- The strategy-neutral daily replay substrate is accepted under `P3-REPLAY-001`, including strict
  typed P1-frame adaptation, carried-sell company actions, immutable replay packaging, the lazy
  continuous PIT frame reader, full 2012-2025 input audit, and a real-corpus round-trip smoke.
- The research-only `P3-FEAT-002`/`P3-STRAT-001` increment adds a vectorized PIT daily-indicator
  panel and a three-strategy one-unit baseline runner. Moderated catalogue v2 is the CLI default;
  v1 remains a replayable but rejected extreme-control record. The command is locked to 2012-2021
  development data and does not promote a target P3 policy or open arbitration/blind partitions.
  Its report now requires an offline checksum-bound `sh000300` CSI 300 curve and includes benchmark
  excess, time in market, average invested capital, and top-three closed-trade robustness metrics.
  Runner 2.2 retains that full-capital opportunity-cost comparison and adds a prior-close-weighted
  capital-matched diagnostic for selection/timing analysis.
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
- The additive 2026 research backfill uses snapshot
  `p1-historical-replay-2026-through-20260727-v1`, corporate-action policy v3, and portable ADS
  policy v2. It covers decisions through 2026-07-24 with next-open execution context on 2026-07-27,
  remains readable only as `development_research`, and does not rewrite the original three bundles
  or any mutable pointer. Formal publication and 134-frame replay evidence are accepted in
  `acceptance/issue-45-2026-research-ads-backfill-20260730.md`.
- Its v3 successor uses snapshot `p1-historical-replay-2026-through-20260730-v3`, the mixed-source
  CSI 300 tail, and the versioned fail-closed ST missing-source rule. Formal bundle
  `strategy-replay-recent-research-7c99724-v3` contains 137 decision frames through 2026-07-29 and
  T+1 context through 2026-07-30; publication and independent audit evidence are accepted in
  `acceptance/issue-45-2026-research-ads-refresh-20260730.md`.
- The frozen P3 capability fixture has a versioned card, concrete feature/label/expert/
  action chain, strict static-industry input, five fixed-capital-unit replay, P2-backed fills and
  settlement, four-source exits, carried orders, cooldown, benchmark metrics, and populated
  opportunity-funnel production. Its first frozen 2012-2021 development run passed all three
  strategy gates and the drawdown warning at 230 closed trades, 31.91% total return, 22.49%
  capital-matched excess, 19.11% return after removing the top three closed-trade contributions,
  and 4.75% maximum drawdown. An independent full invocation reproduced the feature panel,
  result, and scoreboard byte-identically, closing the P3 capability acceptance. It is not the
  first member of a formal strategy family, is ineligible for tuning or promotion, remains
  research-only, and has -83.01% full-capital excess versus CSI 300.
- P3 opportunity-funnel reconciliation remains non-executing by contract. The governed portfolio
  runner now supplies complete candidate, score/veto, entry, and held-review source observations;
  the funnel describes research evidence and cannot authorize an order.
- Human label review, constrained strategy attribution, recurring dashboards, and concrete shadow
  decision production resume only when a formal label or strategy proposal supplies their missing
  semantic input. They are not reasons to extend the frozen P3 fixture.
- The operator-assisted two-window profile remains blocked by intraday-input and matching-policy
  evidence; forward email transport remains P6 work. P4 and later keep their existing gates.
- ADR 0005 accepts the dual-lane data-platform target under `META-DATA-PIPE-003`: CN01 produces the
  fixed-primary DWD/DWS candidate, Hermes produces the standby candidate, and OCI alone owns epoch
  arbitration, canonical catalogs, operational ADS, and the sole consumer release-set `CURRENT`.
  Repository contracts now cover exact ODS-attempt selection, trusted Shanghai-time windows,
  fenced lease tokens and SHA-precondition pointer CAS, cutover gating, immutable correction and
  revocation records, fenced rollback, accepted-ODS recovery receipts, and one self-contained
  `strategy_replay` ADS profile. The repository now also defines a sealed historical DWD/DWS seed
  and lane-local logical catalogs that join the common seed to each node's daily candidate chain
  without rewriting either. Three-node deployment, the three physically separate historical
  bootstrap bundles, role probes, and development-bundle replay equivalence are accepted in
  `acceptance/issue-45-portable-ads-20260730.md`; conditional two-build OCI resource qualification
  is recorded separately in `acceptance/issue-45-ads-parallelism-20260730.md`; normal strategy
  replay now uses an explicit immutable ADS bundle. Catalog-native publication, independent
  restore evidence, full PIT/tradability candidate families, and production cutover remain open.
  Bounded daily shadow timers for OCI epoch, both lane candidates, and OCI arbitration were
  activated in `acceptance/issue-45-shadow-timer-activation-20260730.md`. Their first scheduled
  cycle completed after a recovered ODS pointer-ownership defect and is recorded in
  `acceptance/issue-45-first-scheduled-cycle-20260730.md`; corrective revision `7c99724c` is
  deployed on all three nodes, but a later clean unattended session is still required. The legacy
  OCI full-ODS pull/build path stays live until an explicit cutover gate passes. Migration is copy-only;
  no existing data or evidence may be deleted. Portable bundles use manifest-discovered semantic
  parts without mandatory annual directories. The historical 2025 bootstrap is physically isolated
  for migration, but because its source was already visible to the development principal it is not
  evidence of a genuinely unopened blind evaluation.
  The copy-only historical DWD/DWS migration is accepted through 2026-07-30 in
  `acceptance/issue-45-historical-dwd-dws-completion-20260730.md`: OCI, CN01, and Hermes hold the
  same sealed base bytes, and both lane-local logical catalogs join it to four daily candidates
  without physical concatenation or cleanup authority.
  The provisional OCI operational ADS historical Base through 2026-07-30 is accepted in
  `acceptance/meta-data-base-003-ads-base-202607/provisional-base-acceptance-20260731.md`.
  It byte-copies the accepted semantic source v2, binds the exact DWD/DWS v2 pair, passes an
  isolated one-day replay, and advances only its administrative `BASE_CURRENT.json`. The final
  July Base and every consumer-visible release/cutover gate remain open.
  Candidate-success-driven daily lane-history refresh is deployed on both producer nodes in
  `acceptance/issue-45-daily-history-catalog-automation-20260730.md`; installation and idempotent
  execution are accepted, while the first real scheduled `OnSuccess` observation remains pending.
- `design/stock-stage-state-machine.md` is a non-governing P3 draft and is not P2 context.

The current machine-readable release state is `config/p3-gates.v1.json`; P2's retained conditional
gate is `config/p2-gates.v1.json` and P1's retained gate is `config/p1-gates.v1.json`. Prose that
disagrees with any applicable
gate is a conformance defect rather than an alternate project state.

## Tree

| Branch | Authority and purpose | Child index |
| --- | --- | --- |
| `product/` | Product goals, requirements, non-goals, and release criteria | `product/prd.md` |
| `design/` | Approved current-state HLD, execution profiles, and module LLDs | `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md` |
| `design/strategies/` | Formal strategy proposals and frozen capability fixtures | `design/strategies/README.md` |
| `architecture/` | Accepted decisions and stable topic contracts | `architecture/README.md` |
| `work-items/` | Stable ids joining design, code, tests, issues, and evidence | `work-items/index.md` |
| `operations/` | Deployment, recurring operation, incident, migration, and rollback | Runbook selected by the active component |
| `acceptance/` | Point-in-time acceptance and conformance evidence | Load only for the claim being audited |
| `hermes/` | Historical audit and handoff artifacts | Not part of default design context |

## Progressive load routes

| Task | Required context | Add only when needed |
| --- | --- | --- |
| P2 backtest implementation | root `AGENTS.md`, this map, `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md`, `design/lld/02-backtest-engine.md` | LLD 01 for inputs, LLD 10 for logs, LLD 05 for boundary checks |
| P1/P2 data input | this map, HLD, LLD 01 | Linked architecture topic, ADR 0002/0003/0004/0005, relevant acceptance file |
| ODS or daily operations | this map, architecture index, ADR 0003/0004/0005, selected runbook | Exact config, service, and contract tests |
| Dual-lane candidate or ADS publication | root `AGENTS.md`, this map, HLD, ADR 0005, LLD 01 | Exact epoch/candidate/release config and contract tests; `operations/dual-lane-data-platform-runbook.md` for shadow/migration; cutover remains separately gated |
| Product or phase decision | PRD, HLD, LLD 09, work-item index | Governing gate, ADR, or acceptance evidence |
| P3 state-model discussion | HLD, LLD 03, state-machine draft | Never load as P2 implementation authority |
| P3 strategy-semantics discussion | HLD, `design/p3-strategy-semantics-checklist.md` | Load LLD 03/04/05 only for the decision group being discussed; the checklist is non-governing |
| P3 rule-only implementation | root `AGENTS.md`, this map, HLD, LLD 03/04/05, accepted P2 interfaces | Exact P1 input node and P2 evidence for the path under test |
| P3 strategy historical replay | root `AGENTS.md`, this map, HLD, LLD 02 and its `strategy-replay.md` child, LLD 01 `strategy-replay-ads.md` | Exact strategy, authorized ADS bundle, and P2 execution evidence; load the legacy P1 snapshot only for frozen P3 reproduction |
| P3 governed strategy implementation | root `AGENTS.md`, this map, selected `design/strategies/` card, LLD 03/04/05, accepted P2 interfaces | LLD 10 for decision/funnel evidence; exact feature, label, policy, and industry-map inputs |
| P4 event-library implementation | root `AGENTS.md`, this map, HLD, LLD 06, `config/p3-gates.v1.json` | ADR 0005 and LLD 01 only when producing/consuming portable ADS; exact frozen candidate source, label cards, split policy, and P3 replay evidence |

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
