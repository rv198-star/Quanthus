# P6-DATA-001 Owner Value Review — 2026-09-17

| Field | Value |
| --- | --- |
| Decision | `OPEN_P6_DATA_001_ONLY` |
| Parent | `P6-GOV-001` |
| Parent proposal | `config/forward-validation/entry/p6-prospective-mvp-entry.v1.json` |
| Parent digest | `sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18` |
| Machine decision | `config/forward-validation/entry/p6-data-001-owner-value-decision.v1.json` |
| Authority opened | P6 prospective data-boundary implementation only |

## Decision

The post-entry value checkpoint accepts the frozen prospective mechanism MVP cost and opens only
`P6-DATA-001`.

The accepted budget remains exactly three non-overlapping 22-session collection blocks (66 trading
sessions), followed by the frozen 20-session target-maturity tail, with a maximum 150-calendar-day
ceiling from the first official market date. Cross-sectional candidates, observations, decisions,
fills or securities do not create extra independent progress. Low activity is an honest
`inconclusive_low_activity_no_budget_extension` outcome and cannot extend the budget after results.

## Why the cost is justified

Historical replay has already established deterministic engineering and bounded development
attribution. It cannot answer the remaining P6 question: whether the frozen chain stays
outcome-blind, reproducible and authority-safe when information arrives in real future time.

The calendar cost is therefore accepted as a mechanism-validation cost, not a profitability bet.
The program is deliberately cheaper than expanding the expert catalogue, reopening Stage research,
or starting P5 model work. Weak expert quality or negative paper economics remain valid evidence and
must not trigger rescue tuning.

The reusable value of `P6-DATA-001` is narrower than the whole P6 program: establish one trustworthy
prospective DWD/release/access/correction boundary that every later P6 child can consume without
inventing its own data-time rules.

## Frozen boundaries

This decision does **not** activate P6 or create prospective evidence. It grants no authority for:

- `P6-SCORE-001`, `P6-GOV-002`, paper runtime, Web projection, or official cycle activation;
- Stage action semantics, a new expert, aggregator changes, or P5;
- notification, broker connectivity, advice delivery, or live capital;
- arbitration 2022-2024 or permanent-blind 2025 access; or
- treating already-known 2026 history as prospective evidence.

Every later P6 child remains dependency-gated. The first official market date still requires a
future `P6-ACTIVATE-001` decision and must be the first complete trading session strictly after that
decision's Asia/Shanghai market date whose daily DWD release is READY and deeply verifiable.

## P6-DATA-001 implementation question

The data child must prove four things before it may close:

1. one daily DWD package can be bound by package id, market date, manifest SHA, content digest,
   code revision and verified READY state;
2. publication/visibility evidence is recorded separately from DWD semantic identity and proves
   when the verified package became admissible to the P6 consumer;
3. every P6 read emits an append-only access-ledger record carrying purpose, cutoff, package and
   activation/proposal identities; and
4. any later correction appends a `preserve`, `degrade` or `censor` disposition and never rewrites a
   prior observation or access record.

Missing visibility evidence, package verification failure, pre-activation dates, sealed-partition
requests, post-cutoff reads, identity drift, or attempted evidence rewrite fail closed.

## Claim ceiling

This review accepts implementation cost only. It does not claim that any future P6 observation will
be admissible, that the current expert strategy is effective, or that paper/Shadow/production
activation should occur. Those decisions remain with later frozen gates.
