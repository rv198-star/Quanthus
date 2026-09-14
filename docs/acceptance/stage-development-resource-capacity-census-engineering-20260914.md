# P3-STAGE-013-A — Metadata-only capacity-census engineering acceptance

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-013-A` |
| Parent | `P3-STAGE-013` |
| Decision | `PASS_ENGINEERING_READY_FOR_ONE_METADATA_ONLY_JOB` |
| Governing resource model | `config/stage-learning/stage-development-resource-model.v1.json` |
| Frozen census contract | `config/stage-learning/stage-development-resource-capacity-census.v1.json` |

## Scope

This checkpoint accepts only the implementation of a deterministic metadata capacity census. It does not accept a Stage candidate, freeze board-era quotas, open a result-bearing development experiment, or read protected evaluation market rows.

The resource unit is the already selected:

`economic identity × non-overlapping time window × PIT listing board × evidence role`.

## Frozen metadata surface

The census may read only:

- PIT `dwd_listing` identity, listing interval, venue and `board_code` metadata;
- governed open-session `dwd_trading_calendar` metadata through `2020-12-31`;
- bound `source-binding`, selection and preserved-plan metadata from P3-STAGE-007 through P3-STAGE-011 artifacts;
- immutable source manifests / READY markers required to verify those bindings.

It has no code path to `dwd_security_bar`, Structure morphology, Primary evidence, labels, candidate-grid output, threshold values, future outcomes, 2021 preserved market rows, 2022-2024 or 2025.

## Window contract

A census window contains exactly 61 open sessions and is constructed independently for each trading venue from the beginning of each frozen institutional era. The 61-session size is a metadata capacity proxy for a case that may require T plus a 60-session causal history; it is **not** a claim that the corresponding security has complete price bars. A tail shorter than 61 open sessions is not counted as a full window.

A listing contributes an identity-window token only when its effective listing interval covers every session of that window. Multiple simultaneous active listings for one economic identity in the same window are treated as identity ambiguity and excluded from capacity.

## Exposure reconstruction

The implementation reconstructs the bound predecessor chain without opening its evidence or candidate outputs:

- L0 — metadata discovered;
- L1 — fresh/reserved pool only;
- L2 — exact development/warm-up window was market-read;
- L3 — exact report window contributed to threshold/model/reference-selection basis;
- L4 — the exact selected review/scoring case window was consumed;
- L5 — exact preserved 2021 identities remain identity-global exclusions.

P3-STAGE-007 through P3-STAGE-011 source bindings and selection metadata are bound by exact artifact-manifest digests. The Stage-009 preserved evaluation plan must retain its exact 160-identity digest and `data_read=false` state.

## Old-vs-new capacity comparison

The artifact reports both policies on the same metadata universe:

1. legacy whole-identity burn-down — complete prior exposed/fresh identities are unavailable in every historical window;
2. exposure-aware reuse — L0/L1 windows remain available, L2-L4 consume only overlapping windows, and L5 remains globally unavailable.

The output therefore measures released identity-window capacity without randomly appending stocks and without weakening the preserved evaluation boundary.

## Runtime governance

The Worker admits exactly one new kind:

`quanthus.stage-development-resource-capacity-census.v1`

The one-shot budget is revision-bound and idempotent for the same submission. A different revision is rejected rather than silently replacing the census after observing its counts. The restricted RX6800 helper adds only `submit-p013-a`; no generic submit, shell, cancel or file-write authority is added.

## Output contract

A successful Job publishes exactly one governed artifact containing:

- `windows.json`;
- `capacity-matrix.json`;
- `exposure-ledger.json`;
- `identity-window-summary.json`;
- `summary.json`;
- manifest + READY binding.

The matrix contains every frozen era × board-family cell, including structurally absent cells, and reports active identities, usable identities, identity-window capacity, L0-L5 counts, legacy availability, exposure-aware availability, released capacity, protected overlap and bounded loss reasons.

## Boundaries after engineering acceptance

Still forbidden:

- P012 G1 rerun or reinterpretation;
- price-path or morphology reads in this census;
- quota selection before the census result;
- threshold / gate / candidate changes;
- preserved-2021 market access;
- 2022-2024 access;
- 2025 access;
- daily, strategy, production or trading activation.

Exit from this checkpoint is only permission for the exact one-shot metadata census after the accepted revision is deployed to RX6800.
