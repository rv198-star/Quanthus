# P3-EXPERT-003 First Target Production Acceptance (2026-08-21)

| Field | Value |
| --- | --- |
| Status | accepted for the bounded development engineering proof |
| Work item | `P3-EXPERT-003` / Issue #148 |
| Code revision | `931065e` |
| Worker Job | `job_f586108025f44259a4d20268330f8f46` |
| Expert Run | `expert_run_eff9bef52ddd7eba83c7acf75ec75189fba503a0f6f210983bcc99092b8833a2` |
| Run manifest digest | `sha256:eff9bef52ddd7eba83c7acf75ec75189fba503a0f6f210983bcc99092b8833a2` |
| Input digest | `sha256:30612d5d591f7bed955304ae0fc5285490ffc368ccd5306792db5e6f5f3c2eae` |

## Accepted bindings

The production Job ran through the PostgreSQL Replay Worker with producer
`producer.trend-stability-development@1.0.2`. It binds DWD Base
`dwd-base-202607-v2.1`, correction `dwd-base-202607-v2.1-correction-20260813-001`,
the approved 2020-2021 development partition, and exact P4 parent Run A manifest
`sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c`.
The outer parent manifest was verified to bind the accepted Event and Label manifest/content
identities. Content-identical Run B was not admitted by its inner digests.

The target and population remain the human-approved
`expert-target.p4-event-first-passage-reuse@1.0.0` decision. DWD projection respects the
development Provider decision boundary through `2021-12-30`; `2021-12-31` remains the final T+1
execution boundary. The sealed eligible corpus contains no event after `2021-11-30`.

## Production accounting

| Ledger state | Count |
| --- | ---: |
| eligible | 9,234 |
| applicable | 8,597 |
| abstained | 637 |
| settled | 8,540 |
| unavailable | 25 |
| not executable | 32 |
| pending / censored | 0 / 0 |

Observation states are 3,550 fragile, 2,407 neutral, 2,640 stable, and 637 explicit abstentions.
No observation has a `knowledge_cutoff` after its T-close observation anchor.

The descriptive expert MAOE is `0.6510538641686182669789227166`; the predeclared always-neutral
baseline MAOE is `0.9152224824355971896955503513`. Coverage is
`0.9310158111327701970976824778` and abstention rate is
`0.0689841888672298029023175222`. The deterministic 2,000-resample, 22-session block interval is
`[0.5686060606060606060606060606, 0.7410824800270057387194778891]` over 22 effective blocks.
These are descriptive development results, not an effectiveness, strategy-admission,
portfolio-value, or action-authority claim.

## Determinism and artifact verification

The Worker Run passed deep inventory, checksum, Parquet row-count, stage-order, input-binding, and
content-derived Run-id verification. A separate direct CLI invocation from a different staging and
Run root reproduced the same Run id and manifest digest. `CHECKSUMS.sha256`, `READY`, spec closure,
observations, settlements, ledger, scorecard, and `run.json` were byte-identical.

Key artifact SHA-256 values are:

- observations: `26634d4266c6e40e65c0ffdce6d5ae34ff8feb97e211e319930debec5d29cd43`;
- settlements: `6ecb264206860ba570c85d1bdbe5288340f1f378b302c62627ff8a8363c3c591`;
- scorecard: `420c4746ab4ddde41c9c2d7b366aa92f48568bf5840570e1b5572f8b086215ae`;
- sample ledger: `f81ae371efe7f6d95235969ee0b5e56e472ab617f6712392b00560b7e15c70f1`.

Repository `make PYTHON=.venv/bin/python check` passed with 1,269 tests and four optional
external-PostgreSQL-DSN skips. Focused Ruff, strict MyPy, shell syntax, image import smoke, and
production mount checks passed. The replay Worker used read-only DWD, correction, research
reference, and Event Run mounts plus a dedicated writable expert-run volume.

## Production findings closed during acceptance

Production attempts exposed and closed four integration defects before a Run was accepted:

1. inner Event/Label digests could not distinguish content-identical P4 A/B parents; binding now
   uses one exact outer Run manifest and verifies its inner closure;
2. the replay image excluded `quanthus.event_library`; image construction now includes the package
   and performs a Worker/expert import smoke test;
3. the deployment override omitted the checksum-bound benchmark and industry references; both are
   restored as read-only mounts;
4. P4 events use governed DWD security ids while the legacy replay panel is queried by listing
   code; the expert adapter now queries with the sealed `listing_code` and normalizes evidence back
   to the governed event identity.

Failed attempts remain in PostgreSQL as operational evidence and published no immutable Run.

## Boundary and next gate

This acceptance closes only `P3-EXPERT-003`. Arbitration 2022-2024 and permanent-blind 2025 remain
sealed. It does not activate an expert, create a formal strategy, run an intervention, or authorize
Shadow, notification, approval, or execution. `P3-STRAT-002` is now dependency-ready but still
requires its own outcome-blind strategy card, exact two-expert binding, expected-change declaration,
resource budget, and human approval before result access.
