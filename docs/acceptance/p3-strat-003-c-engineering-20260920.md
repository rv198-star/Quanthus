# P3-STRAT-003-C Result-Blind Engineering Acceptance — 2026-09-20

| Field | Value |
| --- | --- |
| Work item | `P3-STRAT-003-C` / GitHub #320 |
| Decision | `PASS_RESULT_BLIND_FOUR_VARIANT_ENGINEERING` |
| Parent | `P3-STRAT-003` / GitHub #317 |
| Frozen request | `config/research/p3-strat-003/request.v1.json` |
| Request digest | `sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde` |
| Runtime contract | `config/research/p3-strat-003/runtime.v1.json` |
| Runtime digest | `sha256:5d5223138fbe8187e78c755aa2e45d758a7a803e37f8624df5f4f689fffd9185` |
| Implementation commit | `1fdce60801c4f8c45a3bf1b85ac08feb95d4cd72` |
| Base commit | `de66f04da718247e1a57bf26b0602262f46e9bfe` |
| Recent-history result read | **none** |
| P6 mutation / deployment | **none** |
| D status | still blocked by B input qualification |

## Decision

C is accepted as **result-blind engineering only**.

The implementation makes the frozen B/T/R/TR factorial executable through the existing Expert
Strategy Layer, aggregator, capital allocation and complete P2 replay path without mutating the
accepted `candidate.p4-trend-risk@1.0.1` card.

This acceptance does not contain, infer or approve any 2025/2026 strategy return, expert value,
promotion result, P6 outcome, broker action or live-capital authority.

## Four-variant implementation

The accepted implementation adds one explicit research control,
`control.p4-candidate-pass-through@1.0.0`, and routes the four variants exactly as frozen by A:

- **B**: pass-through entry scorer; Trend off; Position Risk off;
- **T**: exact existing Trend mapping; Position Risk off;
- **R**: pass-through entry scorer; exact existing Position Risk on;
- **TR**: exact existing Trend mapping; exact existing Position Risk on.

The pass-through control does not bypass the expert/aggregator path. Missing shared entry evidence
abstains and the system guardian blocks the entry. Tradability, data quality, hard position,
funding, accounting, concentration and base-position management remain outside the experimental
factorial and stay active in every variant.

Risk-off means only the strategy Position Risk effect is replaced by its accepted fallback.
Base position management and system guardians are not disabled.

## Additive replay route

`FormalP2ReplayRuntime` now accepts explicit scope, exact parent Event Run identity and provider
configuration as parameters while retaining its previous 2020-2021 defaults. The existing formal
attribution route therefore keeps its old semantics unless a caller explicitly selects the new
research path.

`P3Strat003ReplayRuntime` admits only the four frozen research tokens and maps them to the two
declared experimental factors. Unknown tokens fail before a result-bearing replay.

`P3Strat003CompositeBackend` opens one replay/provider/feature prerequisite for one batch and runs
B, T, R and TR in fixed order. Variant publication is atomic; incomplete visible publication fails
closed. A resumed batch executes only missing variants.

## Cross-process reuse repair

Read-only review of the interrupted implementation found that the first draft could have reused a
persisted variant after the batch scope/provider identity changed, because the persisted check
covered only variant identity.

That defect was repaired **before any recent-history strategy result was read**.

Every batch now publishes an immutable `batch-input.json` binding:

- A request digest;
- accepted formal-strategy digest;
- research code version;
- decision/execution scope;
- exact parent Event Run digest;
- provider configuration path and digest; and
- provider partition.

Every variant manifest binds the same batch-input digest. A restart may reuse a variant only when
that semantic input closure still matches. A changed batch input conflicts with the immutable
batch-input artifact or the persisted variant manifest and fails closed.

Resume telemetry distinguishes `built` and `reused_existing` variants and reports zero elapsed
variant time for a reused artifact rather than inventing timing.

## Recent-history partition boundary

A second pre-result review finding was that the generic replay provider previously authorized only
the `development` partition. Treating 2025/2026 as development merely to reuse the provider would
misstate evidence authority.

The provider loader therefore gained an additive v3 schema and an explicit
`allowed_partitions` argument:

- its default remains exactly `{"development"}`;
- every existing caller therefore keeps the old fail-closed behavior;
- the P3-STRAT-003 runtime opts in only to
  `p3-strat-003-recent-history`.

Tests prove that the recent-history partition is rejected by the default loader and accepted only
when the caller supplies that exact partition. This is an authority label, not permission to read
the data; B must still publish the exact immutable input manifest before D.

## Batch and cold-reproduction semantics

The runtime contract freezes:

- Worker concurrency = 1;
- provider builds per batch = 1;
- feature-panel builds per batch = 1;
- fixed variant order B/T/R/TR;
- no cross-batch cache for the independent cold reproduction;
- no execution, broker, live-capital or P6 mutation authority.

The CLI entry point is
`python -m quanthus.expert_strategy.p3_strat_003_cli`.
D must supply B's final provider config, Event Run digest and exact dates; C deliberately does not
invent those identities.

## Verification

Focused engineering verification after the final pre-result repairs:

- P3-STRAT-003 runner, formal strategy, formal attribution adapter and replay-provider tests:
  **32 passed / 0 failed**;
- broader expert-strategy/P2 regression set: **273 passed / 0 failed**;
- changed-file Ruff: **PASS**;
- changed-module compileall: **PASS**;
- CLI `--help` smoke: **PASS**;
- `quanthus doctor`: **PASS**;
- planning check: **PASS**;
- `git diff --check`: **PASS**.

The repository contains **331 test files / 2691 collected tests** at this revision. Because a
monolithic pytest invocation exceeds the MCP foreground response window, the suite was executed by
a deterministic file-shard partition rather than rerunning already completed work. All test files
were scheduled under those shards and no test failure was observed. The recorded skips are the
repository's existing optional PostgreSQL-DSN / production-artifact-mount gates; no P3-STRAT-003
test is skipped.

A monolithic `make check` output is therefore **not** claimed. Its components
(`doctor`, `compile`, pytest suite and `planning-check`) were executed separately so their
actual evidence survives the tool transport limit.

Full-repository `ruff check .` is also **not** claimed green: it reports 199 pre-existing unrelated
lint findings, concentrated in older DWD audit/Stage/Catalog code and tests. Ruff over every file
changed by C is green. Those unrelated findings were not modified or hidden as part of this issue.

## Conformance review

The implementation was checked against #320 and the frozen A request, not only against green tests.

Accepted properties:

- exactly B/T/R/TR; no hidden fifth configuration or weight/threshold search;
- normal synthetic B/R has a non-empty entry path while missing evidence and Risk vetoes still
  produce legal no-action;
- T/TR use the accepted Trend mapping and TR uses the accepted Position Risk path;
- base exits and system guardians are unchanged;
- one batch owns one shared provider/feature prerequisite;
- variant output is isolated and atomic;
- restart reuse is exact-input-bound;
- cold reproduction has no cross-batch cache authority;
- recent-history evidence has a distinct explicit partition identity;
- old formal-attribution defaults remain compatible;
- no recent-history market result was used to debug or tune this implementation.

## Remaining dependency

C does **not** open D by itself.

B / #319 must still qualify and freeze:

- the exact 2025/2026 effective input;
- exact warm-up dates;
- Event Run identity;
- replay/provider configuration and digest;
- PIT/coverage/exception evidence; and
- the isolated read/write roots.

D may start only after the frozen A request, accepted B input manifest and implementation commit
`1fdce60801c4f8c45a3bf1b85ac08feb95d4cd72` are bound together.

## Claim ceiling

This acceptance proves that the four-variant research mechanism is construction-ready and
result-blind.

It does **not** prove that B is profitable, that Trend or Position Risk adds value, that TR beats B,
that 2025/2026 inputs are qualified, that D has run, or that any successor strategy should be
promoted.
