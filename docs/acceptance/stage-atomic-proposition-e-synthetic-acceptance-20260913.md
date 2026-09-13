# P3-STAGE-012-E Synthetic Acceptance — 2026-09-13

## Disposition

```text
P3-STAGE-012-E1: ACCEPTED / ATOMIC-FIRST CONTRACT
P3-STAGE-012-E2: ACCEPTED / SYNTHETIC PROTOTYPE ONLY
P3-STAGE-012-E3: PASS_SYNTHETIC_CONFORMANCE
MARKET DATA: NOT READ
PRESERVED 2021: UNREAD
NEW FACT: NOT REGISTERED
THRESHOLD SEARCH: NOT PERFORMED
P008 RELOCATION: UNCHANGED
```

Owner approval opened only the synthetic implementation/conformance package recommended by
P3-STAGE-012-D. The governing authorization is
`config/stage-learning/stage-atomic-proposition-e-synthetic-authorization.v1.json`, digest
`sha256:c617f21613ce506287753c9da572c3793ddb0139385bddc96b2cbf309f1bacb6`.

## E1 — atomic Primary review / resolver

E1 does not fork or rewrite the historical P3-STAGE-008 protocol. It reuses the exact frozen
`order-reference-protocol.v2` and its validators, then adds an atomic-first composition surface in
`src/quanthus/stage_learning/atomic_primary_reference.py`.

The output preserves, per role:

- `structural_instability_20`;
- `coherent_directional_relocation_20`;
- `orderly_consolidation_20`;
- the downstream Stage0/Stage2 projection;
- proposition-level cross-role disagreement;
- roles that explicitly assert mixed instability + relocation.

Batch summaries separately report mixed-state, projection-disagreement and atomic-disagreement
denominators. `Stage0` is therefore no longer usable as a synonym for atomic instability.

Machine contract:
`config/stage-learning/stage-atomic-primary-review-contract.v1.json`, digest
`sha256:74900cfde3d7e86744cab8621a3fd14710fd3fd900fa18f53ef9a98ec750a80c`.

## E2 — range-envelope prototype

E2 implements a deliberately narrow, close-only synthetic prototype in
`src/quanthus/stage_learning/range_envelope_prototype.py`.

For a T-ending 21-session close path, each session after the first ten compares the current close to
the **previous ten closes**. A strict move above/below that frozen prior envelope creates an upper/lower
escape. Each escape then records whether a later close, still no later than T, re-enters that event's
frozen pre-escape envelope.

The prototype emits event/descriptive evidence only:

- escape and re-entry events;
- resolved versus unresolved counts;
- resolved escape side sequence and alternations;
- whether resolved escapes occurred on both sides;
- unresolved escape sides;
- event-trace digest.

It does **not** emit `compact_disorder=TRUE/FALSE`, does not infer Stage0/Stage2/relocation, and owns no
learned threshold. Equality with an envelope boundary is inside, not an escape. Missing close data and
insufficient history fail closed.

Machine prototype contract:
`config/stage-learning/stage-range-envelope-prototype.v1.json`, digest
`sha256:bf34c7790d8341826cc94521d7e5d1c2e6004af172d983d6f4d6ff6d167c4a6f`.

## E3 — adversarial synthetic conformance

The synthetic case contract was frozen before conformance execution:
`config/stage-learning/stage-atomic-envelope-synthetic-contract.v1.json`, digest
`sha256:df82e410efde252c48b3a94d9552f19c20d368a17ee9cdc09b2822486b9e2dcb`.

The deterministic runner is
`scripts/run_stage_atomic_envelope_synthetic_conformance.py`.

All frozen checks pass. The reproduced result exactly matches
`config/stage-learning/stage-atomic-envelope-synthetic-conformance-result.v1.json`, digest
`sha256:ee09715c69b2e12485ec841af6a96d74370a96b584d7fa9df637e42798820b0f`.

Key synthetic outcomes:

```text
orderly high-churn contraction   -> 0 envelope escapes
stable narrow noise              -> 0 envelope escapes
one-leg repricing then hold      -> 1 unresolved UPPER escape
failed-resolution compact path   -> 4 resolved escapes
                                    UPPER -> LOWER -> UPPER -> LOWER
                                    side alternations = 3
unavailable close                -> UNAVAILABLE
```

At the atomic layer:

```text
instability=TRUE, relocation=TRUE, order=FALSE
-> atomic coexistence retained for both roles
-> projected Stage0/2 reference remains NEITHER
-> mixed_directional_instability remains explicit
```

## Claim ceiling

This result proves only that the approved atomic-first contract and the threshold-free envelope event
semantics are coherent on frozen synthetic adversarial cases.

It does **not** prove:

- market separability of compact disorder from Stage2;
- that range-envelope resolution/persistence deserves a formal Fact;
- any numeric envelope threshold;
- a third instability channel;
- Stage0 or Stage2 qualification;
- P008 relocation retuning;
- permission to read a fresh cohort or preserved 2021.

A post-E expert review is required before any Fact/projection repair or fresh-cohort inventory is
opened.
