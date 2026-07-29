# Quanthus

Quanthus is a point-in-time A-share event-driven trading research platform. Its first
objective is a deterministic, auditable data and backtest foundation. It does not treat
model output as a trading decision and it does not promise investment returns.

## Current gate

- P1 implementation: allowed.
- P1 acceptance: conditionally passed under `p1-c1-conditional-20260725-v1`; its 4,173
  factor-visibility exceptions remain fail-closed.
- P2 acceptance: conditional pass for `close-to-next-open-v1` research; target two-window
  activation remains blocked by `config/p2-gates.v1.json`.
- P3 implementation: allowed for a replayable rule-only historical vertical slice. The bounded
  strategy-neutral daily replay substrate, continuous real-history frame reader, and three simple
  development-only research baselines are accepted. Target strategy rules and inactive experts
  remain open or shadow-only as declared by their work items.

## Quick start

The bootstrap checks have no third-party runtime dependencies:

```bash
make doctor
make test
make check
```

Create a development environment when external tools are needed:

```bash
make bootstrap
```

Install the optional P1 data stack with:

```bash
make bootstrap-p1
```

## Repository layout

```text
config/              Frozen policy and release gates
docs/                Architecture decisions, contracts, and roadmaps
ops/                 Deployable scripts and service/timer templates
src/quanthus/        Runtime package and project doctor
tests/               Unit, contract, and acceptance tests
data/                Local data only; generated content is not committed
research/            Reproducible research entry points, not production logic
```

The frozen decisions are encoded in `config/p0-policy.v1.json`. Changes to data,
execution, risk, labels, or validation rules require a new policy version and matching
contract tests.

The production post-close data path is versioned in
`config/pipelines/t-close-plus-5h.v1.json`. Runtime ODS releases, DWD partitions, logs, locks,
environment files, and credentials remain outside Git.

## Source design

- Notion HLD: https://app.notion.com/p/b947cb9072444b9c8dd01d3a5a82f7a0
- P0 decisions: https://app.notion.com/p/3a691fb8262e81aa8659d83d85fd18ed

The repository is the approved executable and design baseline. Notion remains a historical
discussion and rationale source; it cannot override repository policy, contracts, or approved
design without a reconciled repository change.
