# P3-STAGE-012-E9 Reserved Registration Acceptance — 2026-09-14

## Disposition

```text
P3-STAGE-012-E9: CLOSED
STATE: REGISTERED_RESERVED_MEASUREMENT_NO_RUNTIME_BINDING
SEMANTIC CONCEPT: structure.geometry.close-envelope-resolution-trace
RESULT KIND: measurement
REGISTRATION: reserved
RUNTIME BINDING: NONE
MARKET DATA READ: 0
PRESERVED 2021 READ: 0
```

E9 executes the E8 registration decision and nothing broader. The semantic concept is added to
`config/structure-semantic-catalog.v1.json` as a reserved `structure.geometry` measurement. The
reviewed Chinese business name and explanation are added to `config/catalog-business-metadata.v1.json`.

Reserved registration is naming/boundary authority only. It does not qualify the concept on market
data, add an accepted runtime binding, select a Stage threshold or authorize a strategy consumer.

## Deterministic Catalog update

The authoritative builder now reproduces exactly:

```text
entry_count:       761
semantic_concept:  34
gap_count:         10
snapshot_digest:   sha256:a4d2753840003c8b43812b65d7c102c5901a4edada15d7ba8d67666886d25319
```

The new semantic entry remains `declared_only / contract_only` in the Data & Indicator Catalog because
it is intentionally absent from `structure-facts-v2-blueprint.v1.json.semantic_runtime_bindings`.

## B6-PATH state

The historical P008 `config/structure-directional-path-geometry.v1.json` contract remains byte-
semantically frozen at its original digest. E9 updates only the Structure blueprint description to
record that the envelope measurement is registered-but-unqualified.

The existing E5 detector, frozen 20/10 profile, PIT/reset/coordinate behavior and E7 measurement
representation are unchanged.

## Next gate

The owner has delegated the remainder of P3-STAGE-012 iteration through closeout. Therefore E9 opens
P3-STAGE-012-F without another owner-approval stop, but F itself is only freshness inventory and
preregistration. No fresh market-data read is permitted until the F preregistration is frozen.

Preserved 2021 remains unread until an exact successor candidate/projection is durably frozen under
the later P012 gate.
