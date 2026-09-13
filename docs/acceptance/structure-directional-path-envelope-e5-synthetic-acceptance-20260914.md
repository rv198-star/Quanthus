# P3-STAGE-012-E5 — B6-PATH Envelope Extension Synthetic Acceptance

## Disposition

```text
DESIGN OWNER: B6-PATH / Directional Path Geometry
DESIGN: APPROVED FOR PROPOSAL SEMANTICS
SYNTHETIC QUALIFICATION: PASS
FACT REGISTRATION: NOT AUTHORIZED
SEMANTIC CATALOG CHANGE: NONE
EXISTING B6-PATH CONTRACT: UNCHANGED
MARKET DATA: NOT READ
PRESERVED 2021: UNREAD
```

E5 is bound to owner approval at base commit
`0e0cd67961935abed1016b9dc7896afa16d87bab` and to the post-E review digest
`sha256:125a510411eaec2896538005c57dc9650bc707df5c9005ba43975d9a780d409d`.

## 1. Frozen artifacts

- authorization: `config/structure-directional-path-envelope-e5-authorization.v1.json`
- proposed Structure contract: `config/structure-directional-path-envelope-extension.v1.json`
- synthetic contract: `config/structure-directional-path-envelope-synthetic-contract.v1.json`
- synthetic result: `config/structure-directional-path-envelope-synthetic-result.v1.json`
- Structure implementation: `src/quanthus/structure_directional_path_envelope.py`
- conformance runner: `scripts/run_structure_directional_path_envelope_synthetic_conformance.py`

Digests:

```text
authorization  sha256:634136cc9df451b8590626a0d2a57e7bb76e0452f3dbe9d333ddd0970bc25430
design         sha256:22d1031e547dec1db5a5d620b12949cc4a856537569c03d495d04cc4e559a71a
synthetic      sha256:4dcef798bc12cabf4cc0f0e67c016bc93eb504f1f9cf1bbacb9f8d04d6c6e0ea
result         sha256:413dd2256158f7e9eb68ee17711832070581af04f930c47bbd60f7ab2db39f9e
```

The frozen result canonicalizes exactly to a fresh invocation of the conformance runner.

## 2. Ownership result

The envelope algorithm core now lives under Structure:

```text
quanthus.structure_directional_path_envelope
```

The previously accepted Stage E2 module imports the Structure detector and preserves only the old
Stage-specific result schema for compatibility. Re-running P3-STAGE-012-E3 after the refactor produces
its exact prior canonical result digest:

```text
sha256:ee09715c69b2e12485ec841af6a96d74370a96b584d7fa9df637e42798820b0f
```

No duplicate Stage-specific detector remains.

## 3. Synthetic conformance result

All frozen checks pass, including:

```text
orderly-high-churn-contraction        0 escapes
one-leg-repricing-then-hold           1 unresolved upper escape
alternating-failed-resolution         4 resolved escapes / U-L-U-L / 3 alternations
stable-narrow-noise                   0 escapes
equality-on-prior-boundary            0 escapes
same-side-resolved-escapes            UPPER,UPPER / 0 alternations
T-session-unresolved-escape           unresolved at T
unavailable-close                     fail closed
insufficient-history                  fail closed
positive scale invariance             PASS
listing/coordinate rewarm             PASS
hidden-gap rewarm                     PASS
Stage E2 compatibility                PASS
```

`failed_checks=[]`.

## 4. What E5 proves

E5 proves that the proposed close-envelope resolution trace can be expressed as deterministic,
threshold-free, close-only B6-PATH geometry with explicit PIT/reset semantics and without changing the
accepted Stage E2 evidence.

The event trace is one proposed structured observation. Counts and alternation are deterministic
fields, not separately registered Facts.

## 5. What E5 does not prove

E5 does not prove market separability, window optimality, economic value, Stage0 validity, compact-
disorder truth, a relocation change, or formal B6-PATH qualification.

The current `config/structure-directional-path-geometry.v1.json` remains byte-semantically unchanged at
`sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c`, and
`structure.geometry.close-envelope-resolution-trace` is absent from the semantic catalog.

## 6. Next gate

A post-E5 review must decide one of:

```text
REGISTER_AS_B6_PATH_EXTENSION
REVISE_NEUTRAL_CONCEPT
KEEP_RESEARCH_ONLY
PARK
```

That review itself has no market-data authority. Any registration, runtime integration, fresh-cohort
experiment or preserved-2021 access requires later explicit authorization.
