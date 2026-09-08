# P3-STAGE-001-C Historical Harness Acceptance — 2026-09-08

## Decision

`P3-STAGE-001-C` is **ACCEPTED / CLOSED** for its declared development-only engineering scope.

This acceptance does **not** authorize classifier training, official LabelCard publication, 2022-2025 development reads, future-target access, strategy consumption, or profitability claims.

## Executed evidence

- Governed node: `RX6800`
- Worker job: `stage_baa4ae75cfdd4db59c5aba94671de246`
- Job kind: `quanthus.stage-harness.v1`
- Executed code revision: `e901a6747e8daccb95fa7f2bee4f29c08cffcda6`
- State: `succeeded`
- Exit code: `0`
- Harness run: `stage-harness-20260908T081908Z`
- Artifact manifest SHA256: `sha256:76564e80dca0ee95a7b0bfbeef92968a40b694cf273ca8a69bc8e1c87bb3226f`
- Artifact READY exactly binds the manifest SHA256.
- Source member closure digest: `sha256:50e71c939b41b7292927b974ae7e390934fea69918fafc4ada97c7f65937785f`
- Runtime: about `3.42s`

The later repository changes between the executed revision and closeout `main` affect only the governed stage Worker / installer runtime identity handling; the Stage Harness semantic implementation and `config/stage-learning/harness.v1.json` are unchanged.

## Source and authority closure

The artifact binds the accepted training source identities:

- Base manifest: `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`
- Accepted correction manifest: `sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde`
- Knowledge view: `retrospective_dwd_record_view`
- Read guard: `development_business_dates_only_2012_2021`
- `no_classifier_training=true`
- `no_future_target_read=true`
- Physical holdout exposure is explicitly disclosed as `true`; qualification relies on logical read guards, not a false physical-blind claim.

Executed slice:

- securities: `000001`, `300750`, `600519`
- scan start: `2019-10-08`
- report: `2020-01-02` through `2020-03-31`
- attempted source rows: `357`
- admitted source rows: `357`
- rejected source rows: `0`

## Mechanics evidence

The real-DWD run produced:

- Structure measurements: `1189`
- Structure interactions: `15`
- Structure episode observations: `247`
- Report-period activity observations: `174`
- Engineering candidates: `96`
  - stage 3 candidates: `3`
  - stage 4 candidates: `93`
- Engineering decisions: `96`
  - confirmed stage 3: `2`
  - confirmed stage 4: `91`
  - abstain: `0`
  - rule conflict: `0`

These counts are mechanics evidence only. They are not semantic-quality, calibration, market-frequency, or profitability results.

## Acceptance checks

| Check | Result |
| --- | --- |
| Minimal F1/F2/F3 reuse and F7 activity fact executable | PASS |
| Missing volume remains unavailable rather than zero | PASS by counterexample tests |
| Corporate-action coordinate reset is fail-closed | PASS by counterexample tests |
| Same-frozen-anchor stage 3/4 temporal distinction | PASS |
| Stage-4 reference role remains same-anchor acceptance support | PASS |
| Batch vs incremental activity exact | PASS |
| Batch vs incremental candidates exact | PASS |
| Batch vs incremental decisions exact | PASS |
| Future suffix cannot rewrite earlier candidate/decision records | PASS |
| Independent engineering oracle | PASS |
| Development read guard rejects post-2021 scope | PASS by contract/unit tests |
| Future target absent | PASS |
| Official label authority absent | PASS |
| Classifier/model fitting absent | PASS |
| Artifact file inventory and READY/manifest binding | PASS |

## Claim ceiling

C proves that the first-group development-only historical engineering harness is deterministic, bounded, auditable and capable of consuming accepted retrospective DWD without leaking official-label, model-training or future-target authority.

C does **not** prove that stage 3/4 semantics are correct, calibrated, stable on holdout data, useful to a strategy, or profitable. Those questions remain owned by later work items.
