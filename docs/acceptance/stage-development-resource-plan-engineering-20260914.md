# P3-STAGE-013-C — Exact metadata identity-window plan engineering acceptance

## Decision

`PASS_ENGINEERING_READY_FOR_ONE_EXACT_METADATA_PLAN_JOB`

This acceptance is implementation-only. It authorizes one metadata-only plan Job after the accepted revision is deployed to RX6800. It does not authorize price, morphology, reference, candidate, preserved-2021 market, 2022-2024 or 2025 reads.

## Frozen inputs

- P013-A verified census Job: `stage_0d24e5a8158445fabb2ae26bec742ada`;
- census manifest: `sha256:354a936dc9dc7262e1cc4f7377e5048cfa8a00b8d9585bb3fe7d39ede47c03c9`;
- P013-B quota freeze: eight structurally present Era × Board cells;
- reserve: 32 per cell / 256 total;
- primary: 24 per cell / 192 total;
- pre-frozen fallback: 8 per cell / 64 total;
- current-batch global economic-identity uniqueness is mandatory.

## Implementation

`src/quanthus/stage_learning/development_resource_plan.py` reconstructs the exact P013-A metadata surface from the same governed listing/calendar metadata and prior immutable exposure artifacts. It verifies the original census artifact and repository bindings, then compares reconstructed available-identity counts with the committed P013-A result before selecting anything.

Allocation runs in the P013-B scarcity order so scarce STAR cells receive first claim on globally unique identities. Within each cell, 32 reserve slots are spread deterministically across the available 61-open-session metadata windows. Every fourth slot is a pre-frozen fallback, producing exactly 24 primary and 8 fallback rows per cell. Hashes break metadata-only ties; no price or morphology influences identity or window choice.

The exact plan persists security/listing identity, code, PIT board, venue, era, 61-session window, prior exposure level and primary/fallback role. A deep verifier requires:

- exactly 256 rows;
- exactly 256 unique economic identities;
- exactly eight cells with 32 rows each;
- exactly 192 primary and 64 fallback rows;
- only L0/L1 prior exposure;
- exact plan and file digests;
- all protected data-access flags false.

## Worker and control boundary

New admitted kind:

`quanthus.stage-development-resource-plan.v1`

It has a one-shot revision-bound budget marker. Same-revision duplicate submission is idempotent; a different revision is rejected as replacement/retry. Automatic retry is zero.

The restricted AgentDock helper adds only `submit-p013-c`; there is still no generic submit, shell, cancel or file-reading capability.

## Verification

Focused engineering verification passes 52 tests across the plan selector, Stage Worker and restricted helper. Targeted Ruff and `git diff --check` pass.

The required full repository check must pass before this acceptance is committed and the Job is deployed.

## Hard boundaries

- P013-A is not rerun.
- P012 G1 is not rerun or rewritten.
- No inherited 512 requirement returns.
- No same-identity duplicate is admitted in the current 256-reserve batch.
- No cross-cell fallback or post-result replacement is allowed.
- Preserved 2021 remains market-data unread and identity-global protected.
- 2022-2024 remain closed; 2025 remains permanent blind.
- No daily, strategy, paper, production or trading authority is opened.

A successful plan Job closes only the exact metadata-plan half of P013-C. The successor result-bearing atomic-first preregistration still has to bind that exact plan artifact and be committed before any price read can occur.
